"""Same-user Codex Desktop IPC client. Never starts another Codex engine."""
import json
import os
from pathlib import Path
import socket
import shutil
import stat
import struct
import subprocess
import sys
import threading
import time
import uuid
import psutil

class AppToolsUnavailable(RuntimeError):
    pass

class WindowsNamedPipe:
    """Socket-shaped wrapper for a Windows byte-mode named pipe."""

    def __init__(self, path, timeout):
        import ctypes
        import msvcrt
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
        kernel32.WaitNamedPipeW.restype = wintypes.BOOL
        kernel32.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        kernel32.PeekNamedPipe.restype = wintypes.BOOL
        wait_ms = max(1, min(int(timeout * 1000), 4294967294))
        if not kernel32.WaitNamedPipeW(str(path), wait_ms):
            raise OSError(ctypes.get_last_error(), 'Windows named pipe is unavailable')
        self.stream = open(path, 'r+b', buffering=0)
        self.kernel32 = kernel32
        self.handle = wintypes.HANDLE(msvcrt.get_osfhandle(self.stream.fileno()))
        self.timeout = timeout

    def settimeout(self, timeout):
        self.timeout = timeout

    def recv(self, length):
        import ctypes
        from ctypes import wintypes
        deadline = None if self.timeout is None else time.monotonic() + self.timeout
        while True:
            available = wintypes.DWORD()
            if not self.kernel32.PeekNamedPipe(self.handle, None, 0, None, ctypes.byref(available), None):
                raise ConnectionError(ctypes.get_last_error(), 'Windows named pipe closed')
            if available.value:
                return self.stream.read(min(length, available.value))
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError('Windows named pipe read timed out')
            time.sleep(0.01)

    def sendall(self, data):
        data = memoryview(data)
        while data:
            written = self.stream.write(data)
            if not written:
                raise ConnectionError('Windows named pipe closed while writing')
            data = data[written:]

    def close(self):
        self.stream.close()

def connect_stream(path, timeout):
    path = str(path)
    if os.name == 'nt' and path.startswith('\\\\.\\pipe\\'):
        return WindowsNamedPipe(path, timeout)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    client.connect(path)
    return client

def windows_process_environment(pid):
    """Read a same-user 64-bit Windows process environment without extra packages."""
    if os.name != 'nt':
        return {}
    import ctypes
    from ctypes import wintypes

    class ProcessBasicInformation(ctypes.Structure):
        _fields_ = [('reserved1', ctypes.c_void_p), ('peb', ctypes.c_void_p), ('reserved2a', ctypes.c_void_p), ('reserved2b', ctypes.c_void_p), ('unique_pid', ctypes.c_void_p), ('reserved3', ctypes.c_void_p)]
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    ntdll = ctypes.WinDLL('ntdll')
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    kernel32.ReadProcessMemory.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    ntdll.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
    ntdll.NtQueryInformationProcess.restype = wintypes.LONG
    handle = kernel32.OpenProcess(4096 | 16, False, int(pid))
    if not handle:
        return {}

    def read(address, size, allow_partial=False):
        buffer = ctypes.create_string_buffer(size)
        received = ctypes.c_size_t()
        ok = kernel32.ReadProcessMemory(handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(received))
        if not ok and (not (allow_partial and received.value)):
            raise OSError(ctypes.get_last_error(), 'ReadProcessMemory')
        return buffer.raw[:received.value]
    try:
        info = ProcessBasicInformation()
        returned = wintypes.ULONG()
        status = ntdll.NtQueryInformationProcess(handle, 0, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(returned))
        if status or not info.peb:
            return {}
        pointer_size = ctypes.sizeof(ctypes.c_void_p)
        parameters = int.from_bytes(read(info.peb + 32, pointer_size), 'little')
        environment = int.from_bytes(read(parameters + 128, pointer_size), 'little')
        raw = bytearray()
        for offset in range(0, 1024 * 1024, 4096):
            try:
                chunk = read(environment + offset, 4096, allow_partial=True)
                raw.extend(chunk)
            except OSError:
                break
            end = next((index for index in range(0, len(raw) - 3, 2) if raw[index:index + 4] == b'\x00\x00\x00\x00'), -1)
            if end >= 0:
                raw = raw[:end]
                break
            if len(chunk) < 4096:
                break
        values = raw.decode('utf-16-le', errors='ignore').split('\x00')
        return dict((value.split('=', 1) for value in values if '=' in value and (not value.startswith('='))))
    except (OSError, ValueError):
        return {}
    finally:
        kernel32.CloseHandle(handle)

def desktop_app_tool_paths():
    """Discover the app-tools endpoint exported to Desktop's codex app-server."""
    if os.name == 'nt':
        try:
            owner = psutil.Process().username().casefold()
        except (psutil.Error, OSError):
            return
        for process in psutil.process_iter(('pid', 'name')):
            try:
                if str(process.info.get('name') or '').casefold() != 'codex.exe':
                    continue
                if process.username().casefold() != owner:
                    continue
                path = windows_process_environment(process.pid).get('CODEX_APP_TOOLS_PIPE_PATH')
            except (psutil.Error, OSError):
                continue
            if path:
                yield path
        return
    if sys.platform == 'darwin':
        try:
            processes = subprocess.check_output(['ps', 'axeww', '-o', 'pid=,command='], text=True, encoding='utf-8', errors='ignore', timeout=8).splitlines()
        except (OSError, subprocess.SubprocessError):
            return
        marker = 'CODEX_APP_TOOLS_PIPE_PATH='
        for command in processes:
            head = command.split(marker, 1)[0]
            if 'app-server' not in head or '/codex' not in head:
                continue
            for token in command.split():
                if token.startswith(marker):
                    yield token[len(marker):]

class DesktopAppTools:
    """Small client for the same-user Codex Desktop native app-tools pipe."""

    def __init__(self, path=None):
        self.configured_path = path
        self.cached_path = None

    def _paths(self):
        seen = set()
        for path in (self.configured_path, self.cached_path, os.environ.get('CODEX_APP_TOOLS_PIPE_PATH')):
            if path and path not in seen:
                seen.add(path)
                yield path
        for path in desktop_app_tool_paths():
            if path and path not in seen:
                seen.add(path)
                yield path
        proc = Path('/proc')
        if not proc.is_dir():
            return
        for env_file in proc.glob('[0-9]*/environ'):
            try:
                values = env_file.read_bytes().split(b'\x00')
            except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
                continue
            prefix = b'CODEX_APP_TOOLS_PIPE_PATH='
            for value in values:
                if not value.startswith(prefix):
                    continue
                path = value[len(prefix):].decode(errors='ignore').strip()
                if path and path not in seen:
                    seen.add(path)
                    yield path

    def _connect(self, timeout):
        last_error = None
        for path in self._paths():
            try:
                client = connect_stream(path, timeout)
            except OSError as exc:
                last_error = exc
                continue
            self.cached_path = path
            return client
        detail = f': {last_error}' if last_error else ''
        raise AppToolsUnavailable('没有找到 Codex Desktop app-tools 通道' + detail)

    @staticmethod
    def _exact(client, length):
        chunks = bytearray()
        while len(chunks) < length:
            part = client.recv(length - len(chunks))
            if not part:
                raise ConnectionError('Codex Desktop app-tools 通道已关闭')
            chunks.extend(part)
        return bytes(chunks)

    def call(self, tool, arguments, thread_id, timeout=30):
        client = self._connect(timeout)
        request_id = str(uuid.uuid4())
        request = {'id': request_id, 'jsonrpc': '2.0', 'method': 'tools/call', 'params': {'arguments': arguments, 'callerSource': 'codex', 'callId': 'relay-' + str(uuid.uuid4()), 'namespace': 'codex_app', 'threadId': thread_id, 'tool': tool, 'turnId': 'relay-' + str(uuid.uuid4())}}
        payload = json.dumps(request, ensure_ascii=False, separators=(',', ':')).encode()
        try:
            client.sendall(struct.pack('<I', len(payload)) + payload)
            length = struct.unpack('<I', self._exact(client, 4))[0]
            if not 0 < length <= 8 * 1024 * 1024:
                raise ValueError('Codex Desktop app-tools 响应大小异常')
            response = json.loads(self._exact(client, length))
        finally:
            client.close()
        if response.get('id') != request_id:
            raise RuntimeError('Codex Desktop app-tools 响应不匹配')
        if response.get('error'):
            error = response['error']
            raise RuntimeError(error.get('message', str(error)))
        result = response.get('result', {})
        if not result.get('success', False):
            messages = [item.get('text', '') for item in result.get('contentItems', []) if item.get('type') == 'inputText']
            raise RuntimeError('\n'.join(filter(None, messages)) or 'Codex Desktop 拒绝了该操作')
        return result

class MacNativeAppTools:
    """Private client of the user-enabled, App-managed MCP adapter.

    Only probe other endpoints before submission. A tool call is never replayed
    after a write, timeout or disconnect with uncertain native acceptance.
    """

    def __init__(self, directory, ensure_ready=None):
        self.directory = Path(directory).expanduser()
        self.ensure_ready = ensure_ready

    @staticmethod
    def _private(path, kind):
        value = path.lstat()
        matches = stat.S_ISDIR(value.st_mode) if kind == 'directory' else stat.S_ISREG(value.st_mode)
        if not matches or value.st_uid != os.getuid() or value.st_mode & 63:
            raise AppToolsUnavailable('Mac 原生桥接目录/凭据权限不安全')
        return value

    @staticmethod
    def _request(client, method, params):
        request_id = str(uuid.uuid4())
        body = json.dumps({'id': request_id, 'jsonrpc': '2.0', 'method': method, 'params': params}, ensure_ascii=False, separators=(',', ':')).encode()
        if len(body) > 256 * 1024:
            raise ValueError('原生桥接请求过大')
        client.sendall(struct.pack('<I', len(body)) + body)
        length = struct.unpack('<I', DesktopAppTools._exact(client, 4))[0]
        if not 0 < length <= 8 * 1024 * 1024:
            raise RuntimeError('Mac 原生桥接响应大小异常')
        response = json.loads(DesktopAppTools._exact(client, length))
        if response.get('id') != request_id:
            raise RuntimeError('Mac 原生桥接响应不匹配')
        if response.get('error'):
            raise RuntimeError(response['error'].get('message', '原生操作失败'))
        return response.get('result', {})

    def _connect(self, timeout):
        try:
            self._private(self.directory, 'directory')
            credential = self.directory / 'token'
            self._private(credential, 'file')
            token = credential.read_text(encoding='utf-8').strip()
            if len(token) != 64 or any((c not in '0123456789abcdef' for c in token)):
                raise AppToolsUnavailable('Mac 原生桥接凭据格式不正确')
            paths = sorted(self.directory.glob('*.sock'), key=lambda p: p.lstat().st_mtime, reverse=True)
        except FileNotFoundError as exc:
            raise AppToolsUnavailable('Mac 原 App 尚未启动本机工具桥接') from exc
        for path in paths[:8]:
            client = None
            try:
                value = path.lstat()
                if not stat.S_ISSOCK(value.st_mode) or value.st_uid != os.getuid() or value.st_mode & 63:
                    continue
                client = connect_stream(str(path), min(timeout, 2))
                result = self._request(client, 'status', {'token': token})
                if result.get('ready') is not True or result.get('version') != 1:
                    raise AppToolsUnavailable('Mac 原生桥接版本不匹配')
                client.settimeout(timeout + 2)
                return (client, token)
            except (OSError, RuntimeError, ValueError):
                if client is not None:
                    client.close()
        raise AppToolsUnavailable('Mac 原 App 的本机工具桥接未就绪；请刷新 App 工具连接')

    def call(self, tool, arguments, thread_id, timeout=30):
        try:
            client, token = self._connect(timeout)
        except AppToolsUnavailable:
            if self.ensure_ready is None:
                raise
            self.ensure_ready()
            client, token = self._connect(timeout)
        try:
            response = self._request(client, 'tools/call', {'token': token, 'tool': tool, 'arguments': arguments, 'threadId': thread_id, 'timeoutMs': int(timeout * 1000)})
        finally:
            client.close()
        content = response.get('content')
        if not isinstance(content, list):
            raise RuntimeError('Mac 原生工具返回格式不正确')
        texts = [item['text'] for item in content if isinstance(item, dict) and isinstance(item.get('text'), str)]
        if response.get('isError'):
            raise RuntimeError('\n'.join(texts) or 'Mac 原 App 拒绝了该操作')
        return {'success': True, 'contentItems': [{'type': 'inputText', 'text': text} for text in texts]}

class DesktopIPC:

    def __init__(self, path, on_event):
        self.path = path
        self.on_event = on_event
        self.sock = None
        self.client_id = None
        self.pending = {}
        self.lock = threading.RLock()
        self.writer = threading.Lock()

    @property
    def connected(self):
        return self.client_id is not None

    def connect(self):
        self.sock = connect_stream(self.path, 10)
        self.sock.settimeout(None)
        threading.Thread(target=self._read, daemon=True).start()
        result = self.request('initialize', {'clientType': 'codex-desktop-relay'}, version=0)
        self.client_id = result['result']['clientId']

    def send(self, message):
        data = json.dumps(message, ensure_ascii=False, separators=(',', ':')).encode()
        with self.writer:
            if self.sock is None:
                raise ConnectionError('桌面 App 已断开')
            self.sock.sendall(struct.pack('<I', len(data)) + data)

    def request(self, method, params, version=1, target=None, timeout=15):
        rid = str(uuid.uuid4())
        ready = threading.Event()
        box = {}
        with self.lock:
            self.pending[rid] = (ready, box)
        msg = {'type': 'request', 'requestId': rid, 'method': method, 'params': params, 'version': version, 'timeoutMs': int(timeout * 1000)}
        if self.client_id:
            msg['sourceClientId'] = self.client_id
        if target:
            msg['targetClientId'] = target
        try:
            self.send(msg)
            if not ready.wait(timeout + 1):
                raise TimeoutError('未收到桌面端确认；不会自动重发，请先核对原对话')
            response = box['response']
            if response.get('resultType') != 'success':
                error = response.get('error', '桌面端拒绝请求')
                if error == method + '-timeout':
                    raise TimeoutError(error)
                raise RuntimeError(error)
            return response
        finally:
            with self.lock:
                self.pending.pop(rid, None)

    def follow(self, tid, following=True):
        self.send({'type': 'broadcast', 'method': 'thread-stream-following-changed', 'version': 1, 'sourceClientId': self.client_id, 'params': {'conversationId': tid, 'hostId': 'local', 'following': following}})

    def broadcast(self, method, params, version=1, target_client_ids=None):
        message = {'type': 'broadcast', 'method': method, 'version': version, 'sourceClientId': self.client_id, 'params': params}
        if target_client_ids:
            message['targetClientIds'] = list(target_client_ids)
        self.send(message)

    def _exact(self, n):
        chunks = bytearray()
        while len(chunks) < n:
            part = self.sock.recv(n - len(chunks))
            if not part:
                raise ConnectionError('桌面 App 通信连接关闭')
            chunks.extend(part)
        return chunks

    def _read(self):
        try:
            while True:
                length = struct.unpack('<I', self._exact(4))[0]
                if not 0 < length <= 256 * 1024 * 1024:
                    raise ValueError('IPC frame size out of bounds')
                msg = json.loads(self._exact(length))
                if msg.get('type') == 'response':
                    with self.lock:
                        waiter = self.pending.get(msg.get('requestId'))
                    if waiter:
                        waiter[1]['response'] = msg
                        waiter[0].set()
                elif msg.get('type') == 'client-discovery-request':
                    self.send({'type': 'client-discovery-response', 'requestId': msg['requestId'], 'response': {'canHandle': False}})
                else:
                    self.on_event(msg)
        except (OSError, ValueError, ConnectionError, TypeError) as exc:
            self.client_id = None
            sock, self.sock = (self.sock, None)
            if sock is not None:
                sock.close()
            with self.lock:
                for ready, box in self.pending.values():
                    if ready.is_set():
                        continue
                    box['response'] = {'resultType': 'error', 'error': str(exc)}
                    ready.set()
            self.on_event({'type': 'disconnected'})

def apply_patches(document, patches, copy_on_write=False):
    """Apply the array-path JSON patches emitted by the installed Desktop."""
    for patch in patches:
        path = patch['path']
        op = patch['op']
        if not isinstance(path, list) or op not in ('add', 'replace', 'remove'):
            raise ValueError('Unsupported desktop patch')
        if not path:
            if op == 'remove':
                raise ValueError('Cannot remove root')
            document = patch['value']
            continue
        if copy_on_write:
            root = document.copy() if isinstance(document, (dict, list)) else document
            parent = root
            original = document
            for key in path[:-1]:
                child = original[key]
                if not isinstance(child, (dict, list)):
                    raise ValueError('Invalid patch parent')
                clone = child.copy()
                parent[key] = clone
                parent, original = (clone, child)
            document = root
        else:
            parent = document
            for key in path[:-1]:
                parent = parent[key]
        key = path[-1]
        if isinstance(parent, list):
            if not isinstance(key, int) or key < 0:
                raise ValueError('Invalid array index')
            if op == 'add':
                if key > len(parent):
                    raise IndexError(key)
                parent.insert(key, patch['value'])
            elif op == 'remove':
                parent.pop(key)
            else:
                parent[key] = patch['value']
        elif isinstance(parent, dict):
            if op == 'remove':
                del parent[key]
            else:
                parent[key] = patch['value']
        else:
            raise ValueError('Invalid patch parent')
    return document
