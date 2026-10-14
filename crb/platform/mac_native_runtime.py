"""On-demand lifecycle of an authorized, native-App-owned MCP connection.

No inference, periodic polling, App restart, or takeover of user conversations.
Only the durable developer-only context owned by this integration is opened.
"""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import threading
import time
from crb.codex.ipc import AppToolsUnavailable, DesktopIPC, MacNativeAppTools

class MacNativeRuntime:

    def __init__(self, directory, home, server_factory):
        self.directory = Path(directory)
        self.home = Path(home)
        self.server_factory = server_factory
        self.record = self.directory / 'runtime.json'
        self.lock = threading.Lock()
        self.retry_after = 0
        self.unarchived = False

    @property
    def context_id(self):
        try:
            info = self.record.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 63:
                raise AppToolsUnavailable('内部桥接状态权限不安全')
            value = json.loads(self.record.read_text())
            tid = value.get('threadId', '')
            return tid if re.fullmatch('[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', tid) else ''
        except FileNotFoundError:
            return ''

    def _save_context(self, tid):
        candidate = self.record.with_suffix('.next')
        fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 384)
        with os.fdopen(fd, 'w') as out:
            json.dump({'threadId': tid, 'noUserTurn': True}, out)
        os.replace(candidate, self.record)

    def ensure_context(self):
        """Materialize only our caller context; never resume a user's writer."""
        MacNativeAppTools._private(self.directory, 'directory')
        self.unarchived = False
        tid = self.context_id
        server = self.server_factory()
        if tid:
            try:
                current = server.request('thread/read', {'threadId': tid, 'includeTurns': True}, timeout=10)['thread']
            except RuntimeError as exc:
                if not any((word in str(exc).lower() for word in ('not found', 'no rollout', 'does not exist'))):
                    raise
                current = None
            if current and (not current.get('turns')):
                archived_path = self.home / 'archived_sessions'
                if current.get('archived') or (current.get('path') and Path(current['path']).is_relative_to(archived_path)):
                    server.request('thread/unarchive', {'threadId': tid}, timeout=10)
                    self.unarchived = True
                return tid
        with server.session(timeout=15) as request:
            tid = request('thread/start', {'cwd': str(self.directory), 'ephemeral': False, 'serviceName': 'codex-relay-native-bridge-runtime'})['thread']['id']
            self._save_context(tid)
            request('thread/inject_items', {'threadId': tid, 'items': [{'type': 'message', 'role': 'developer', 'content': [{'type': 'input_text', 'text': 'Internal App-managed Codex Relay MCP lifecycle context. No user task, inference, tools or background work. Only keep the authorized local bridge available.'}]}]})
            request('thread/name/set', {'threadId': tid, 'name': 'Codex Relay 本机桥接'})
        return tid

    def ensure(self):
        with self.lock:
            probe = MacNativeAppTools(self.directory)
            try:
                stream, _ = probe._connect(2)
                stream.close()
                return
            except AppToolsUnavailable:
                pass
            if time.monotonic() < self.retry_after:
                raise AppToolsUnavailable('Mac 原生桥接恢复尚未成功，请稍后再试')
            self.retry_after = time.monotonic() + 30
            tid = self.ensure_context()
            ipc = DesktopIPC(str(self.home / 'ipc/ipc.sock'), lambda _event: None)
            try:
                ipc.connect()
                if self.unarchived:
                    ipc.broadcast('thread-unarchived', {'hostId': 'local', 'conversationId': tid}, version=1)
                ipc.follow(tid)
                subprocess.run(['/usr/bin/open', '-g', 'codex://threads/' + tid], check=True, timeout=10)
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    try:
                        stream, _ = probe._connect(2)
                        stream.close()
                        self.retry_after = 0
                        return
                    except AppToolsUnavailable:
                        time.sleep(0.2)
                raise AppToolsUnavailable('Mac 原 App 尚未完成本机桥接初始化；未提交操作')
            finally:
                if ipc.connected:
                    try:
                        ipc.follow(tid, following=False)
                    except OSError:
                        pass
                if ipc.sock:
                    ipc.sock.close()
