"""Authenticated client enrollment, separate from legacy server configuration."""
import hashlib
import hmac
import re
import sqlite3
import time
import uuid
import http.cookiejar
from html.parser import HTMLParser
import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager

class ClientPairing:

    def __init__(self, database, secret, legacy_hosts=()):
        self.database, self.secret = (str(database), secret.encode())
        self.legacy_hosts = set(legacy_hosts)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS client_devices (host_id TEXT PRIMARY KEY, physical_host_id TEXT NOT NULL, application_id TEXT NOT NULL, installation_id TEXT NOT NULL, label TEXT NOT NULL, token_sha256 TEXT NOT NULL UNIQUE, created REAL NOT NULL, updated REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS client_credential_revocations (token_sha256 TEXT PRIMARY KEY, host_id TEXT NOT NULL, application_id TEXT NOT NULL, revoked REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS client_pairing_epochs (host_id TEXT PRIMARY KEY, epoch INTEGER NOT NULL)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def enroll(self, request):
        if not isinstance(request, dict):
            raise ValueError('配对参数格式错误')
        installation = str(uuid.UUID(str(request.get('installationId', ''))))
        label = request.get('label')
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80 or any((ord(c) < 32 for c in label)):
            raise ValueError('电脑名称需要为 1–80 个字符，不能包含控制字符')
        apps = request.get('applications')
        if not isinstance(apps, list) or not apps or len(apps) > 3 or any((a not in ('codex', 'deepseek', 'claude') for a in apps)) or (len(set(apps)) != len(apps)):
            raise ValueError('请选择 Codex、DeepSeek 或 Claude，不允许重复应用')
        physical = 'pc-' + installation.replace('-', '')
        connections = {}
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT count(*) FROM client_devices').fetchone()[0] >= 256:
                known = db.execute('SELECT 1 FROM client_devices WHERE installation_id=?', (installation,)).fetchone()
                if not known:
                    raise ValueError('已达到客户端配对数量上限')
            for app in apps:
                host = physical if app == 'codex' else physical + '.' + app
                if host in self.legacy_hosts:
                    raise ValueError('新设备 ID 与已有设备冲突，未覆盖已有配对')
                row = db.execute('SELECT epoch FROM client_pairing_epochs WHERE host_id=?', (host,)).fetchone()
                epoch = row[0] if row else 0
                identity = 'relay-client-v1:' + installation + ':' + app
                if epoch:
                    identity += ':epoch:' + str(epoch)
                token = hmac.new(self.secret, identity.encode(), hashlib.sha256).hexdigest()
                now = time.time()
                db.execute('INSERT INTO client_devices VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(host_id) DO UPDATE SET label=excluded.label, token_sha256=excluded.token_sha256, updated=excluded.updated', (host, physical, app, installation, label.strip(), hashlib.sha256(token.encode()).hexdigest(), now, now))
                connections[app] = {'host_id': host, 'physical_host_id': physical, 'label': label.strip(), 'agent_token': token}
        return {'protocol': 1, 'connections': connections}

    def authenticate(self, authorization):
        if not isinstance(authorization, str) or not re.fullmatch('Bearer [a-f0-9]{64}', authorization):
            return None
        digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
        with self.connect() as db:
            if db.execute('SELECT 1 FROM client_credential_revocations WHERE token_sha256=?', (digest,)).fetchone():
                return None
            row = db.execute('SELECT host_id, physical_host_id, application_id FROM client_devices WHERE token_sha256=?', (digest,)).fetchone()
        return {'host_id': row[0], 'physicalHostId': row[1], 'applicationId': row[2]} if row else None

    @staticmethod
    def credential_hash(authorization):
        if not isinstance(authorization, str) or not authorization.startswith('Bearer ') or (not 1 <= len(authorization[7:]) <= 4096) or any((c.isspace() for c in authorization[7:])):
            return None
        return hashlib.sha256(authorization[7:].encode()).hexdigest()

    def is_revoked(self, authorization):
        digest = self.credential_hash(authorization)
        if not digest:
            return False
        with self.connect() as db:
            return db.execute('SELECT 1 FROM client_credential_revocations WHERE token_sha256=?', (digest,)).fetchone() is not None

    def _revoke(self, db, digest, host, app):
        db.execute('INSERT OR IGNORE INTO client_credential_revocations VALUES(?,?,?,?)', (digest, host, app, time.time()))
        db.execute('INSERT INTO client_pairing_epochs VALUES(?,1) ON CONFLICT(host_id) DO UPDATE SET epoch=epoch+1', (host,))
        db.execute('DELETE FROM client_devices WHERE host_id=? AND token_sha256=?', (host, digest))

    def revoke(self, authorization, legacy_identity=None, expected_host=None):
        """A device credential can revoke only itself, never another application."""
        digest = self.credential_hash(authorization)
        if not digest:
            return None
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT host_id,application_id FROM client_credential_revocations WHERE token_sha256=?', (digest,)).fetchone()
            if old:
                if expected_host is not None and expected_host != old[0]:
                    raise ValueError('设备身份不匹配，未撤销其他设备')
                return {'hostId': old[0], 'applicationId': old[1], 'revoked': True, 'alreadyRevoked': True}
            row = db.execute('SELECT host_id,application_id FROM client_devices WHERE token_sha256=?', (digest,)).fetchone()
            if not row and legacy_identity:
                row = (legacy_identity['host_id'], legacy_identity['applicationId'])
            if not row:
                return None
            if expected_host is not None and expected_host != row[0]:
                raise ValueError('设备身份不匹配，未撤销其他设备')
            self._revoke(db, digest, row[0], row[1])
            return {'hostId': row[0], 'applicationId': row[1], 'revoked': True, 'alreadyRevoked': False}

    def contains(self, host):
        with self.connect() as db:
            return db.execute('SELECT 1 FROM client_devices WHERE host_id=?', (host,)).fetchone() is not None

    def forget(self, installation):
        installation = str(uuid.UUID(str(installation)))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute('SELECT host_id,application_id,token_sha256 FROM client_devices WHERE installation_id=?', (installation,)).fetchall()
            hosts = [row[0] for row in rows]
            for host, app, digest in rows:
                self._revoke(db, digest, host, app)
        return hosts

class LoginForm(HTMLParser):

    def __init__(self):
        super().__init__()
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('type') == 'hidden' and (attrs.get('name') in ('csrf', 'next')):
            self.fields[attrs['name']] = attrs.get('value', '')

class SameOriginRedirect(urllib.request.HTTPRedirectHandler):

    def __init__(self, base):
        self.origin = urllib.parse.urlsplit(base)[:2]

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl)[:2] != self.origin:
            raise ValueError('登录服务器尝试跨站重定向')
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def verify_panel_login(configuration, username, password):
    """Validate against the existing public login, using normal HTTPS verification.

    No password/account hashes are copied, no TLS verification is disabled, no
    session cookie is saved, and this only runs on an explicit enrollment.
    """
    if not isinstance(username, str) or not username.strip() or len(username) > 128 or (not isinstance(password, str)) or (not 1 <= len(password) <= 256):
        return False
    path = Path(configuration)
    if not path.exists():
        return False
    base = json.loads(path.read_text())['url'].rstrip('/')
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.path:
        raise ValueError('管理员配置的配对授权地址无效')
    cookies = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(cookies), SameOriginRedirect(base))

    def read(request):
        with opener.open(request, timeout=10) as reply:
            data = reply.read(64 * 1024 + 1)
            if len(data) > 64 * 1024:
                raise ValueError('面板登录响应过大')
            return data
    try:
        form = LoginForm()
        form.feed(read(base + '/login?next=/api/panel/status').decode('utf-8', errors='replace'))
        if not form.fields.get('csrf'):
            raise ValueError('面板登录接口不兼容')
        data = urllib.parse.urlencode({'csrf': form.fields['csrf'], 'next': '/api/panel/status', 'username': username.strip(), 'password': password}).encode()
        read(urllib.request.Request(base + '/login', data=data, headers={'Content-Type': 'application/x-www-form-urlencoded', 'Origin': base, 'Referer': base + '/login'}))
        raw = read(base + '/api/panel/status')
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            return False
        return isinstance(result, dict) and isinstance(result.get('generation'), str) and isinstance(result.get('hostIds'), list)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 429):
            return False
        raise ValueError('面板授权服务未确认登录，请稍后重试') from None
    except (OSError, urllib.error.URLError):
        raise ValueError('无法验证公网面板登录；请检查服务器网络与 HTTPS 证书') from None
    finally:
        cookies.clear()
