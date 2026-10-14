"""Authenticated RC2 Desktop client with identity-checked random-port discovery.

The Linux Electron wrapper uses `web --port 0`. Discover its original process;
never spawn a second engine, guess a port, or bypass the native browser grant.
"""
import os
from pathlib import Path
import re
import threading
import time
from crb.deepseek.harness_client import HarnessClient, HarnessError

def discover_modern_desktop_port(home, payload, proc=Path('/proc'), uid=None):
    home, payload = (Path(home).resolve(), Path(payload).resolve())
    uid = os.getuid() if uid is None else uid
    node = (payload / 'node/bin/node').resolve()
    entry = str(payload / 'runtime/node_modules/@deepseek-ai/dsh/lib/bin.js')
    ports = set()
    for process in proc.iterdir():
        if not process.name.isdecimal():
            continue
        try:
            if process.stat().st_uid != uid or (process / 'exe').resolve() != node:
                continue
            args = (process / 'cmdline').read_bytes().decode().split('\x00')
            if len(args) < 3 or Path(args[0]).resolve() != node or args[1:3] != [entry, 'web']:
                continue
            if '--host' in args and args[args.index('--host') + 1] != '127.0.0.1':
                continue
            env = dict((item.split('=', 1) for item in (process / 'environ').read_bytes().decode().split('\x00') if '=' in item))
            data_home = env.get('DSH_HOME', '').strip() or str(Path(env.get('HOME', '/')) / '.dsh')
            if Path(data_home).expanduser().resolve() != home:
                continue
            sockets = set()
            for fd in (process / 'fd').iterdir():
                try:
                    match = re.fullmatch('socket:\\[(\\d+)\\]', os.readlink(fd))
                    if match:
                        sockets.add(match[1])
                except OSError:
                    pass
            for line in (process / 'net/tcp').read_text().splitlines()[1:]:
                fields = line.split()
                address, port = fields[1].split(':')
                if fields[3] == '0A' and address == '0100007F' and (fields[9] in sockets):
                    ports.add(int(port, 16))
        except (OSError, ValueError, UnicodeError, IndexError):
            continue
    if len(ports) != 1:
        raise HarnessError('原 DeepSeek Harness Desktop 未运行，或存在多个本地引擎；不会另启引擎', 'harness/desktop-port')
    return ports.pop()

class ModernDesktopHarnessClient(HarnessClient):

    def __init__(self, home, payload, opener=None, port_resolver=None):
        super().__init__(home, opener=opener)
        self.payload = Path(payload).resolve()
        self.resolve_port = port_resolver or (lambda: discover_modern_desktop_port(self.home, self.payload))
        self.port_lock = threading.RLock()
        self.port_checked = 0.0

    def _headers(self):
        with self.port_lock:
            if time.monotonic() - self.port_checked >= 1.0:
                port = self.resolve_port()
                self.base = f'http://127.0.0.1:{port}'
                self.authority = f'127.0.0.1:{port}'
                self.port_checked = time.monotonic()
            return super()._headers()

    def rpc(self, endpoint, args=None, timeout=12, rpc_id=None):
        with self.port_lock:
            self._headers()
            return super().rpc(endpoint, args, timeout, rpc_id)
