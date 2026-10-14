"""Private connection configuration is user data, never a bundled resource."""
import datetime
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit

def client_root():
    if getattr(sys, 'frozen', False):
        executable = Path(sys.executable)
        profile = executable.parent.parent
        if executable.parent.name == 'app' and profile.name.startswith('CodexRelayClient-SetupTest-'):
            return profile
    from desktop_client.platform_support import data_root
    return data_root()

def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    import uuid
    temp = path.with_name(path.name + f'.{os.getpid()}.{uuid.uuid4().hex}.tmp')
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 384)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        for delay in (0.01, 0.02, 0.04, 0.08, 0.16, None):
            try:
                os.replace(temp, path)
                break
            except OSError as exc:
                if sys.platform != 'win32' or getattr(exc, 'winerror', None) not in {5, 32, 33} or delay is None:
                    raise
                time.sleep(delay)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass

def atomic_bytes(path, value):
    """Commit a settings/startup snapshot with the same bounded I/O policy."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    import uuid
    temp = path.with_name(path.name + f'.{os.getpid()}.{uuid.uuid4().hex}.tmp')
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 384)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(value)
        for delay in (0.01, 0.02, 0.04, 0.08, 0.16, None):
            try:
                os.replace(temp, path)
                break
            except OSError as exc:
                if sys.platform != 'win32' or getattr(exc, 'winerror', None) not in {5, 32, 33} or delay is None:
                    raise
                time.sleep(delay)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass

def validate_relay_url(value):
    parsed = urlsplit(str(value).strip())
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('服务器地址需要是 http / https 地址，不包含账号、密码或查询参数。')
    parsed.port
    return str(value).strip().rstrip('/')

class ConnectionStore:

    def __init__(self, root=None, home=None):
        self.root = Path(root or client_root())
        self.home = Path(home or Path.home())

    def path(self, key):
        if key not in {'codex', 'deepseek', 'claude'}:
            raise ValueError('未知应用。')
        return self.root / 'connections' / f'{key}.json'

    def legacy_path(self, key):
        if self.root.name.startswith('CodexRelayClient-SetupTest-'):
            return self.root / 'legacy-not-imported' / f'{key}.json'
        if key == 'deepseek' and sys.platform != 'win32':
            native = self.home / 'codex-desktop-relay/applications/deepseek/config.json'
            if native.exists():
                return native
        folder = self.home / '.config/codex-desktop-relay'
        normal = folder / {'codex': 'agent.json', 'deepseek': 'harness-agent.json', 'claude': 'claude-agent.json'}[key]
        regressed = folder / 'crb.codex.agent.json'
        return regressed if key == 'codex' and (not normal.exists()) and regressed.exists() else normal

    def marker_path(self, key):
        self.path(key)
        return self.root / 'connections' / (key + '.unpaired.json')

    def unpaired(self, key):
        return self.marker_path(key).exists()

    def disconnect_path(self, key):
        self.path(key)
        return self.root / 'connections' / (key + '.disconnect.json')

    def disconnect_pending(self, key):
        return self.disconnect_path(key).exists()

    def disconnect_record(self, key):
        value = json.loads(self.disconnect_path(key).read_text(encoding='utf-8'))
        if not isinstance(value, dict) or value.get('application') != key or (not isinstance(value.get('remoteRevoked'), bool)):
            raise ValueError('解除配对操作记录异常；不会恢复旧授权。')
        self._backup_name(key, value)
        return value

    @staticmethod
    def _backup_name(key, record):
        name = record.get('backupName', '')
        if not isinstance(name, str) or Path(name).name != name or (not name.startswith(key + '-unpaired-')) or (not name.endswith('.json')):
            raise ValueError('解除配对记录无效，未读取其他文件。')
        return name

    def disconnect_config(self, key):
        record = self.disconnect_record(key)
        data = json.loads((self.root / 'backups' / self._backup_name(key, record)).read_text(encoding='utf-8'))
        self.validate(data)
        if data['host_id'] != record.get('hostId') or data['relay_url'] != record.get('relayUrl'):
            raise ValueError('解除配对操作身份不匹配，未使用其他设备凭证。')
        return data

    def begin_disconnect(self, key, data):
        self.validate(data)
        if self.disconnect_pending(key):
            raise ValueError('解除配对尚未完成，请继续原操作。')
        import hashlib
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        name = key + '-unpaired-' + stamp + '.json'
        path = self.path(key)
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        atomic_json(self.root / 'backups' / name, data)
        atomic_json(self.disconnect_path(key), {'application': key, 'backupName': name, 'hostId': data['host_id'], 'relayUrl': data['relay_url'], 'remoteRevoked': False, 'configSHA256': digest})

    def record_disconnect_revoked(self, key):
        record = self.disconnect_record(key)
        record['remoteRevoked'] = True
        atomic_json(self.disconnect_path(key), record)

    def abort_disconnect(self, key):
        self.disconnect_record(key)
        self.disconnect_path(key).unlink()

    def complete_disconnect(self, key):
        import hashlib
        record = self.disconnect_record(key)
        path = self.path(key)
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != record.get('configSHA256'):
            raise ValueError('连接配置已被其他操作修改，未删除；请检查待完成的解除记录。')
        atomic_json(self.marker_path(key), {name: record[name] for name in ('backupName', 'hostId', 'relayUrl', 'remoteRevoked')})
        path.unlink(missing_ok=True)
        self.disconnect_path(key).unlink()

    def pairing_marker(self, key):
        value = json.loads(self.marker_path(key).read_text(encoding='utf-8'))
        if not isinstance(value, dict) or not isinstance(value.get('remoteRevoked', False), bool):
            raise ValueError('解除配对记录损坏；不会自动恢复旧配置或授权。')
        return value

    def available(self, key):
        return not self.unpaired(key) and (not self.disconnect_pending(key)) and (self.path(key).exists() or self.legacy_path(key).exists())

    def load(self, key):
        if self.disconnect_pending(key):
            raise FileNotFoundError('解除配对尚未完成；请继续解除，不会恢复旧授权。')
        if self.unpaired(key):
            raise FileNotFoundError('已解除本机连接，请通过配对管理重新接入。')
        path = self.path(key)
        if not path.exists():
            path = self.legacy_path(key)
        return json.loads(path.read_text(encoding='utf-8-sig'))

    def import_existing(self, key):
        if self.disconnect_pending(key):
            raise FileNotFoundError('解除配对尚未完成；不会导入旧授权。')
        if self.unpaired(key):
            raise FileNotFoundError('已解除连接，不会自动恢复旧配置；请重新配对。')
        if not self.path(key).exists():
            data = self.load(key)
            self.validate(data)
            atomic_json(self.path(key), data)
        return self.load(key)

    def disconnected_config(self, key):
        marker = self.pairing_marker(key)
        name = self._backup_name(key, marker)
        data = json.loads((self.root / 'backups' / name).read_text(encoding='utf-8'))
        self.validate(data)
        return data

    def detach(self, key, data, revoked):
        self.validate(data)
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        name = key + '-unpaired-' + stamp + '.json'
        atomic_json(self.root / 'backups' / name, data)
        atomic_json(self.marker_path(key), {'backupName': name, 'hostId': data['host_id'], 'relayUrl': data['relay_url'], 'remoteRevoked': bool(revoked)})
        self.path(key).unlink(missing_ok=True)

    def finish_revoke(self, key):
        marker = self.pairing_marker(key)
        marker['remoteRevoked'] = True
        atomic_json(self.marker_path(key), marker)

    @staticmethod
    def validate(data):
        validate_relay_url(data.get('relay_url', ''))
        if not re.fullmatch('[A-Za-z0-9_.-]{1,128}', str(data.get('host_id', ''))):
            raise ValueError('设备 ID 只允许字母、数字、点、横线、下划线。')
        if not isinstance(data.get('agent_token'), str) or not data['agent_token'].strip():
            raise ValueError('缺少服务器配对凭证。')
        if not str(data.get('label', '')).strip() or len(str(data['label'])) > 80:
            raise ValueError('电脑名称需要为 1–80 个字符。')
        if not data.get('state_dir'):
            raise ValueError('缺少桥接数据目录。')

    def update(self, key, relay_url, label, token=''):
        data = self.load(key)
        data['relay_url'] = validate_relay_url(relay_url)
        data['label'] = label.strip()
        if token.strip():
            data['agent_token'] = token.strip()
        self.validate(data)
        path = self.path(key)
        if path.exists():
            stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            atomic_json(self.root / 'backups' / f'{key}-connection-{stamp}.json', self.load(key))
        atomic_json(path, data)
        return data

    def public(self, key):
        data = self.load(key)
        return {'host_id': data.get('host_id', ''), 'label': data.get('label', ''), 'relay_url': data.get('relay_url', ''), 'paired': bool(data.get('agent_token'))}
