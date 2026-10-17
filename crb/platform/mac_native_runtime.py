"""On-demand lifecycle of an authorized, native-App-owned MCP connection.

No inference, periodic polling, App restart, or takeover of user conversations.
Only the durable developer-only context owned by this integration is opened.
"""
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import threading
import time
import tomllib
from crb.codex.ipc import AppToolsUnavailable, DesktopIPC, MacNativeAppTools

def mac_native_registration(home):
    try:
        config = tomllib.loads((Path(home) / 'config.toml').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise AppToolsUnavailable('无法读取原 App 的 Mac 本机桥接配置，未覆盖。') from error
    return config.get('mcp_servers', {}).get('codex_relay_native_bridge')

def mac_native_bridge_directory(home):
    """The App owns one bridge across client installs, not a pairing's data dir."""
    home = Path(home).expanduser()
    try:
        entry = mac_native_registration(home) or {}
        directory = entry.get('env', {}).get('CODEX_RELAY_NATIVE_BRIDGE_DIR')
        args = entry.get('args', [])
        if isinstance(directory, str) and Path(directory).is_absolute() and isinstance(args, list) and (len(args) == 1) and isinstance(args[0], str) and (Path(args[0]).name == 'mac_native_bridge.mjs'):
            return Path(directory)
    except (FileNotFoundError, ValueError, AttributeError, TypeError):
        pass
    return home / 'crb-native'

class MacNativeRuntime:

    def __init__(self, directory, home, server_factory):
        self.directory = Path(directory)
        self.home = Path(home)
        self.server_factory = server_factory
        self.record = self.directory / 'runtime.json'
        self.lock = threading.Lock()
        self.retry_after = 0
        self.unarchived = False
        self.registration_ready = False

    def ensure_registration(self):
        if self.registration_ready:
            return
        server = self.server_factory()
        entry = mac_native_registration(self.home)
        if entry is not None:
            if not isinstance(entry, dict) or entry.get('enabled', True) is not True or entry.get('env', {}).get('CODEX_RELAY_NATIVE_BRIDGE_DIR') != str(self.directory) or (not isinstance(entry.get('args'), list)) or (len(entry['args']) != 1) or (not isinstance(entry['args'][0], str)) or (Path(entry['args'][0]).name != 'mac_native_bridge.mjs'):
                raise AppToolsUnavailable('现有 Mac 本机工具桥接已禁用或配置不同，未覆盖；请在原 App 的 MCP 设置中检查。')
            MacNativeAppTools._private(self.directory, 'directory')
            MacNativeAppTools._private(self.directory / 'token', 'file')
            self.registration_ready = True
            return
        if len(os.fsencode(str(self.directory))) + 30 > 100:
            raise AppToolsUnavailable('Mac 本机工具桥接路径过长，请选择更短的 Codex 用户目录。')
        script = Path(__file__).with_name('mac_native_bridge.mjs')
        node = Path(server.binary).parent / 'cua_node/bin/node'
        if not script.is_file() or not node.is_file():
            raise AppToolsUnavailable('Mac 安装包或原 App 缺少本机工具桥接依赖，未修改配置。')
        try:
            self.directory.mkdir(mode=448, parents=True, exist_ok=False)
        except FileExistsError:
            pass
        MacNativeAppTools._private(self.directory, 'directory')
        token = self.directory / 'token'
        try:
            fd = os.open(token, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 384)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                output.write(secrets.token_hex(32))
                output.flush()
                os.fsync(output.fileno())
        MacNativeAppTools._private(token, 'file')
        value = token.read_text(encoding='utf-8').strip()
        if not re.fullmatch('[a-f0-9]{64}', value):
            raise AppToolsUnavailable('Mac 本机工具桥接凭据格式不正确，未覆盖。')
        registration = {'command': str(node), 'args': [str(script)], 'enabled': True, 'env': {'CODEX_RELAY_NATIVE_BRIDGE_DIR': str(self.directory)}}
        server.request('config/value/write', {'keyPath': 'mcp_servers.codex_relay_native_bridge', 'mergeStrategy': 'replace', 'value': registration, 'filePath': str(self.home / 'config.toml')}, timeout=15)
        actual = mac_native_registration(self.home)
        if not isinstance(actual, dict) or any((actual.get(key) != item for key, item in registration.items())):
            raise AppToolsUnavailable('Mac 本机工具桥接配置尚未确认，未提交聊天操作。')
        self.registration_ready = True

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
        self.ensure_registration()
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
