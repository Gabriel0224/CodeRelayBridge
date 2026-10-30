import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sys
import time
import urllib.request
from desktop_client.backend import BridgeSpec, ClientError, WindowsTasks, Preferences, redact
from desktop_client.connections import atomic_json
from desktop_client.platform_support import data_root, installed_executable

def server_root():
    if getattr(sys, 'frozen', False):
        executable = Path(sys.executable)
        profile = executable.parents[4] if sys.platform == 'darwin' else executable.parent.parent
        if profile.name.startswith('CodexRelayServer-SetupTest-'):
            return profile
    return data_root('CodexRelayServer')

def suggested_panel_url(bind, port):
    import ipaddress
    address = ipaddress.ip_address(str(bind).strip())
    port = int(port)
    if not 1024 <= port <= 65535:
        raise ClientError('端口需要在 1024–65535 之间。')
    if address.is_unspecified:
        address = ipaddress.ip_address('::1' if address.version == 6 else '127.0.0.1')
    host = '[' + str(address) + ']' if address.version == 6 else str(address)
    return 'http://' + host + ':' + str(port)

class ServerManager:

    def document_settings(self):
        from crb.server.office import settings_path
        path = settings_path(self.config())
        data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        return {key: data.get(key, False if key == 'enabled' else '') for key in ('enabled', 'editor_url', 'service_url', 'callback_url')} | {'managed': bool(data.get('managed_owner'))} | {'secretConfigured': bool(data.get('jwt_secret'))}

    def save_document_settings(self, **fields):
        from crb.server.office import save_settings
        save_settings(self.config(), **fields)
        return '文档引擎配置已保存，无需重启服务。'

    def managed_document(self, action, progress=lambda text: None):
        from crb.server.office_engine import Setup
        setup = Setup(self.config(), progress)
        return setup.install() if action == 'install' else setup.status() if action == 'status' else setup.action(action)

    def check_document_engine(self):
        from crb.server.office import load_settings, OfficeManager
        settings = load_settings(self.config())
        if not settings['enabled']:
            raise ClientError('请先安装并启用文档组件，或保存已有引擎配置。')
        if settings.get('managed_owner'):
            if not self.managed_document('status').get('healthy'):
                raise ClientError('文档引擎尚未就绪，请点击安装并启用继续检查。')
            return '文档引擎连接正常。'
        manager = OfficeManager(self.config(), None, None)
        try:
            if manager.http(settings).request(settings['service_url'] + '/healthcheck', limit=1024).strip() != b'true':
                raise ClientError('文档引擎健康检查未通过。')
        finally:
            if manager.gateway:
                manager.gateway.close()
        return '文档引擎连接正常。'

    def __init__(self, root=None, home=None):
        self.root, self.home = (Path(root or server_root()), Path(home or Path.home()))
        self.root.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.root / 'manager.json'

    def config_path(self):
        if self.settings_path.exists():
            return Path(json.loads(self.settings_path.read_text(encoding='utf-8'))['config']).expanduser().resolve()
        if self.root.name.startswith('CodexRelayServer-SetupTest-'):
            return self.root / 'relay.json'
        legacy = self.home / '.config/codex-desktop-relay/relay.json'
        if legacy.exists():
            return legacy
        normal, regressed = (self.root / 'relay.json', self.root / 'crb.server.relay.json')
        return regressed if not normal.exists() and regressed.exists() else normal

    def config(self):
        try:
            return json.loads(self.config_path().read_text(encoding='utf-8-sig'))
        except FileNotFoundError as error:
            raise ClientError('尚未配置服务器，请先在“连接与首次设置”创建或导入配置。') from error

    def use_existing(self, path):
        if self.config_path().exists():
            from crb.server.office_engine import assert_idle
            assert_idle(self.config())
        path = Path(path).expanduser().resolve()
        cfg = json.loads(path.read_text(encoding='utf-8-sig'))
        if not all((k in cfg for k in ('bind', 'port', 'state_dir', 'session_secret', 'password_sha256', 'agents'))):
            raise ClientError('不是完整 Relay 服务配置，未导入。')
        atomic_json(self.settings_path, {'config': str(path)})
        return '已选择现有服务配置，账号、设备配对和历史记录未改变。'

    def create(self, bind, port, url, username, password):
        import ipaddress
        from desktop_client.backend import Preferences
        bind = str(bind).strip()
        ipaddress.ip_address(bind)
        port = int(port)
        if not 1024 <= port <= 65535:
            raise ClientError('端口需要在 1024–65535 之间。')
        url = Preferences.validate_url(url or suggested_panel_url(bind, port)).rstrip('/')
        if not username.strip() or not 8 <= len(password) <= 256:
            raise ClientError('请填写账号和至少 8 位密码。')
        path = self.root / 'relay.json'
        if path.exists() or self.config_path().exists():
            raise ClientError('已有服务器配置，首次创建不会覆盖。请导入或管理现有服务。')
        if getattr(sys, 'frozen', False):
            from desktop_server.install import install
            install(self.root)
        atomic_json(path, {'bind': bind, 'port': port, 'public_url': url, 'state_dir': str(self.root / 'data'), 'username': username.strip(), 'password_sha256': hashlib.sha256(password.encode()).hexdigest(), 'session_secret': secrets.token_urlsafe(48), 'agents': {}, 'ssh_hosts': {}, 'application_nodes': {}, 'client_login_mode': 'local'})
        if os.name != 'nt':
            path.chmod(384)
        atomic_json(self.settings_path, {'config': str(path)})
        return '服务器已配置。尚未开放路由器或公网；点击启动后，可通过网页账号为客户端配对。'

    def spec(self):
        root = self.root
        suffix = ' - SetupTest ' + hashlib.sha256(str(root).encode()).hexdigest()[:8] if root.name.startswith('CodexRelayServer-SetupTest-') else ''
        return BridgeSpec('server', 'Relay Server', 'Codex Relay Server' + suffix, root / 'unused.py', Path(sys.executable), self.config_path(), root / 'server.log', installed_executable(root, 'RelayServer'), root / 'runtime/server.json')

    def save(self, bind, port, url, username, password):
        if not self.config_path().exists():
            return self.create(bind, port, url, username, password)
        cfg = self.config()
        from crb.server.office_engine import assert_idle
        assert_idle(cfg)
        if cfg.get('client_login_mode') != 'local':
            raise ClientError('现有服务使用外部登录；请在外部登录入口管理密码，未覆盖原配置。')
        generated = suggested_panel_url(bind, port)
        bind = str(bind).strip()
        url = Preferences.validate_url(url or generated).rstrip('/')
        username = username.strip()
        if not username or (password and (not 8 <= len(password) <= 256)):
            raise ClientError('请填写账号和至少 8 位密码。')
        updated = dict(cfg, bind=bind, port=int(port), public_url=url, username=username)
        if password:
            updated['password_sha256'] = hashlib.sha256(password.encode()).hexdigest()
        running = self.status()['running']
        atomic_json(self.config_path(), updated)
        if os.name != 'nt':
            self.config_path().chmod(384)
        if running:
            try:
                self.control('restart')
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if self.status()['running']:
                        if password:
                            self.request('/api/login', {'password': password})
                        return '配置已保存并已应用到服务；客户端配对和历史保持不变。'
                    time.sleep(0.25)
            except (OSError, ValueError, ClientError):
                pass
            raise ClientError('新配置已保存，但服务尚未确认应用；请检查日志并重启服务，不要继续使用旧页面判断密码。')
        return '配置已保存；启动服务后生效，客户端配对和历史保持不变。'

    def tasks(self):
        if sys.platform == 'win32':
            return WindowsTasks()
        from desktop_client.posix_backend import PosixTasks
        return PosixTasks(self.home)

    def legacy_service(self):
        if not sys.platform.startswith('linux') or self.root.name.startswith('CodexRelayServer-SetupTest-'):
            return False
        path = self.home / '.config/systemd/user/codex-desktop-relay.service'
        if not path.exists():
            return False
        if path.is_symlink() or path.stat().st_uid != os.getuid():
            raise ClientError('原服务启动入口不属于本用户，未控制')
        text = path.read_text()
        import shlex
        entries = [line[len('ExecStart='):] for line in text.splitlines() if line.startswith('ExecStart=')]
        if len(entries) == 1 and shlex.split(entries[0]) == [str(self.spec().managed_executable), '--bridge', 'server']:
            return True
        if 'ExecStart=' not in text or str(self.config_path()) not in text or '/relay.py --config ' not in text:
            raise ClientError('原服务入口不匹配，未控制其他服务。')
        return True

    def migrate(self):
        """Linux legacy server replacement; original config/state stays in place."""
        if not self.legacy_service():
            raise ClientError('没有匹配的旧服务')
        status = self.status()
        if status.get('terminals'):
            raise ClientError('网页终端仍打开，请先关闭后接管服务器')
        cfg = self.config()
        from crb.server.office_engine import assert_idle
        assert_idle(cfg)
        import sqlite3, subprocess
        from crb.server.persistence import relay_database
        database = relay_database(cfg['state_dir'])
        with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as db:
            if db.execute('SELECT COUNT(*) FROM commands WHERE result IS NULL AND created>?', (time.time() - 180,)).fetchone()[0]:
                raise ClientError('面板请求尚未处理完，请稍后接管')
        spec = self.spec()
        if not spec.managed_executable.is_file():
            raise ClientError('请先安装服务端独立程序')
        from desktop_client.posix_backend import unit_text
        path = self.home / '.config/systemd/user/codex-desktop-relay.service'
        if path.read_text() == unit_text(spec):
            return '已使用独立服务端运行核心。'
        original = path.read_bytes()
        backup = self.root / 'backups' / ('server-service-' + str(time.time_ns()) + '.service')
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(original)
        backup.chmod(384)

        def control(action):
            subprocess.run(['systemctl', '--user', action, 'codex-desktop-relay.service'], check=True, timeout=25, capture_output=True)
        control('stop')
        try:
            path.write_text(unit_text(spec))
            path.chmod(384)
            subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True, timeout=15, capture_output=True)
            control('start')
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                if self.status()['running']:
                    return '已接管为独立服务端运行核心，账号、配对和历史保持不变。'
                time.sleep(0.5)
            raise ClientError('独立服务端未通过健康检查，恢复原服务')
        except Exception:
            control('stop')
            path.write_bytes(original)
            subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True, timeout=15, capture_output=True)
            control('start')
            raise

    def request(self, path, payload=None):
        cfg = self.config()
        cookie = str(int(time.time()) + 60) + '.' + secrets.token_hex(12)
        cookie += '.' + hmac.new(cfg['session_secret'].encode(), cookie.encode(), hashlib.sha256).hexdigest()
        bind = cfg['bind']
        if bind in {'0.0.0.0', '::'}:
            bind = '127.0.0.1' if bind == '0.0.0.0' else '::1'
        host = '[' + bind + ']' if ':' in bind else bind
        headers = {'Cookie': 'relay_session=' + cookie}
        if payload is not None:
            headers.update({'Content-Type': 'application/json', 'Origin': cfg['public_url']})
        request = urllib.request.Request(f"http://{host}:{cfg['port']}" + path, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=4) as response:
            return json.loads(response.read(2 * 1024 * 1024))

    def status(self):
        if not self.config_path().exists():
            return {'configured': False, 'running': False, 'hosts': []}
        cfg = self.config()
        result = {'configured': True, 'running': False, 'bind': cfg['bind'], 'port': cfg['port'], 'url': cfg['public_url'], 'username': cfg.get('username', 'relay'), 'config': str(self.config_path()), 'hosts': []}
        try:
            value = self.request('/api/lite/catalog')
            result.update(running=True, hosts=[{k: h.get(k) for k in ('id', 'label', 'applicationId', 'online', 'desktopConnected')} for h in value.get('hosts', [])])
            result['terminals'] = self.request('/api/panel/status').get('activeTerminals', 0)
        except (OSError, ValueError):
            pass
        return result

    def control(self, operation):
        if operation not in {'start', 'pause', 'restart'}:
            raise ClientError('未知服务操作。')
        cfg = self.config()
        if operation in ('pause', 'restart'):
            from crb.server.office_engine import assert_idle
            assert_idle(cfg)
        if self.legacy_service():
            import subprocess
            action = {'start': 'start', 'pause': 'stop', 'restart': 'restart'}[operation]
            subprocess.run(['systemctl', '--user', action, 'codex-desktop-relay.service'], check=True, timeout=25, capture_output=True, text=True)
        else:
            tasks, spec = (self.tasks(), self.spec())
            if not tasks.exists(spec):
                if operation == 'pause':
                    return '服务未安装，无需停止。'
                tasks.create(spec)
            if operation in {'pause', 'restart'}:
                tasks.pause(spec)
            if operation in {'start', 'restart'}:
                tasks.start(spec)
        return {'start': '服务器已启动。', 'pause': '服务器已停止。', 'restart': '服务器已重启。'}[operation]

    def log_tail(self):
        path = self.root / 'server.log'
        if not path.exists():
            return '暂无本程序日志；导入的现有服务使用原系统服务日志。'
        with path.open('rb') as handle:
            handle.seek(max(0, path.stat().st_size - 32768))
            return redact(handle.read().decode('utf-8', errors='replace'))
