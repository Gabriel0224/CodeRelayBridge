"""A private Unix app-server endpoint shared by the Linux desktop and CRB."""
import json
import os
from pathlib import Path
import select
import signal
import socket
import stat
import struct
import subprocess
import tempfile
import threading
import time
import uuid
import websocket
STOCK_CLI = '/usr/lib/chatgpt/resources/codex'
SOCKET_NAME = 'desktop.sock'

def runtime_root():
    root = Path(os.environ.get('XDG_RUNTIME_DIR', '/run/user/' + str(os.getuid())))
    mode = root.lstat()
    if not root.is_absolute() or not stat.S_ISDIR(mode.st_mode) or mode.st_uid != os.getuid() or mode.st_mode & 63:
        raise PermissionError('需要当前用户的私有运行目录')
    return root

def connect_socket(path, pid):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) and info.st_uid == os.getuid():
        path = path.resolve(strict=True)
        parent = path.parent.lstat()
        if parent.st_uid != os.getuid() or parent.st_mode & 63:
            raise PermissionError('原 App 的连接目录必须为当前用户专用')
        info = path.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 63:
        raise PermissionError('原 App 的连接必须为当前用户专用')
    transport = socket.socket(socket.AF_UNIX)
    try:
        transport.settimeout(5)
        transport.connect(str(path))
        peer, uid, _ = struct.unpack('3i', transport.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if peer != pid or uid != os.getuid():
            raise PermissionError('连接与原 App 后端身份不一致')
        return websocket.create_connection('ws://localhost/', socket=transport, suppress_origin=True, timeout=5)
    except BaseException:
        transport.close()
        raise

def endpoint_for(backend):
    prefix = 'unix://'
    argv = backend.cmdline()
    if '--listen' not in argv:
        return None
    idx = argv.index('--listen') + 1
    if idx >= len(argv) or not argv[idx].startswith(prefix):
        return None
    path = Path(argv[idx][len(prefix):])
    if path.name != SOCKET_NAME or not path.parent.name.startswith('crb-owner-') or path.parent.parent != runtime_root():
        return None
    mode = path.parent.lstat()
    if not stat.S_ISDIR(mode.st_mode) or mode.st_mode & 63 or mode.st_uid != os.getuid():
        raise PermissionError('原 App 连接目录权限异常')
    return path

class OwnerRPC:
    """Each operation owns its reply channel. Never retry a submitted mutation."""

    def __init__(self, backend, endpoint):
        self.backend = backend
        self.endpoint = endpoint

    @staticmethod
    def call(sock, method, params, timeout=30, notify=None):
        rid = uuid.uuid4().hex
        sock.send(json.dumps({'id': rid, 'method': method, 'params': params}, ensure_ascii=False))
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            sock.settimeout(max(0.05, end - time.monotonic()))
            packet = sock.recv()
            if not packet:
                raise ConnectionError('原 App 已断开')
            reply = json.loads(packet)
            if reply.get('id') == rid and 'method' not in reply:
                if 'error' in reply:
                    raise RuntimeError(reply['error'].get('message') or '原 App 拒绝请求')
                return reply['result']
            if notify and reply.get('method'):
                notify(reply)
        raise TimeoutError('原 App 未返回回执')

    def open(self):
        if not self.backend.is_running():
            raise ConnectionError('原 App 后端已退出')
        sock = connect_socket(self.endpoint, self.backend.pid)
        try:
            self.call(sock, 'initialize', {'clientInfo': {'name': 'crb_desktop', 'version': '1'}, 'capabilities': {'experimentalApi': True}}, timeout=5)
            sock.send('{"method":"initialized"}')
            return sock
        except BaseException:
            sock.close(timeout=0.5)
            raise

    def request(self, method, params, timeout=30):
        sock = self.open()
        try:
            return self.call(sock, method, params, timeout)
        finally:
            sock.close(timeout=0.5)

def desktop_runtime(argv):
    """Keep the original stdio contract for Desktop. Forward one frame per line."""
    import sys
    pos = argv.index('app-server') if 'app-server' in argv else -1
    passthrough = pos < 0 or not os.environ.get('CODEX_APP_TOOLS_PIPE_PATH') or any((x in argv for x in ('--print-startup-config', '--help', '-h', '--version', '-V', 'proxy', 'daemon', 'generate-ts', 'generate-json-schema')))
    if '--listen' in argv or any((arg.startswith('--listen=') for arg in argv)):
        passthrough = True
    if passthrough:
        return subprocess.call([STOCK_CLI, *argv])
    with tempfile.TemporaryDirectory(prefix='crb-owner-', dir=runtime_root()) as tmp:
        path = Path(tmp) / SOCKET_NAME
        cmd = [STOCK_CLI, *(arg for arg in argv if arg != '--stdio'), '--listen', 'unix://' + str(path)]
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        ws = None
        stopped = threading.Event()

        def terminate(*_):
            stopped.set()
        old_term = signal.signal(signal.SIGTERM, terminate)
        try:
            deadline = time.monotonic() + 12
            while not path.is_socket():
                if stopped.is_set() or proc.poll() is not None or time.monotonic() >= deadline:
                    return 1
                time.sleep(0.025)
            ws = connect_socket(path, proc.pid)
            ws.settimeout(None)

            def receive():
                try:
                    while not stopped.is_set():
                        frame = ws.recv()
                        if not frame:
                            break
                        data = frame.encode('utf-8') if isinstance(frame, str) else frame
                        sys.stdout.buffer.write(data + b'\n')
                        sys.stdout.buffer.flush()
                except (OSError, websocket.WebSocketException):
                    pass
                finally:
                    stopped.set()
            reader = threading.Thread(target=receive, daemon=True)
            reader.start()
            line = bytearray()
            while not stopped.is_set() and proc.poll() is None:
                if not select.select([0], [], [], 0.3)[0]:
                    continue
                chunk = os.read(0, 65536)
                if not chunk:
                    return 0
                lines = chunk.split(b'\n')
                for piece in lines[:-1]:
                    line.extend(piece)
                    if line:
                        ws.send(bytes(line), opcode=websocket.ABNF.OPCODE_TEXT)
                    line.clear()
                line.extend(lines[-1])
            return proc.returncode or 0
        finally:
            stopped.set()
            if ws is not None:
                ws.shutdown()
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            signal.signal(signal.SIGTERM, old_term)
