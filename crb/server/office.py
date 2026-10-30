"""Self-hosted ONLYOFFICE integration and recoverable remote document saves.

JWT secrets stay on the server. Narrow document capabilities authorize engine
downloads/callbacks; browser cookies never travel to the document service.
"""
import base64
import contextlib
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time
from urllib.parse import urlsplit, urlunsplit, quote
import urllib.request
import uuid
import zipfile
MAX_DOCUMENT = 32 * 1024 * 1024
EDITABLE = {'docx': 'word', 'xlsx': 'cell', 'pptx': 'slide'}
VIEWABLE = {**EDITABLE, 'doc': 'word', 'xls': 'cell', 'ppt': 'slide', 'odt': 'word', 'ods': 'cell', 'odp': 'slide', 'rtf': 'word'}

def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=448)
    fd, temporary = tempfile.mkstemp(prefix='.office-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def base_url(value, label):
    value = str(value or '').strip().rstrip('/')
    parsed = urlsplit(value)
    if not value or len(value) > 2048 or parsed.scheme not in ('http', 'https') or (not parsed.hostname) or parsed.username or parsed.password or parsed.query or parsed.fragment or (not value.isascii()) or re.search('[\\s<>\\"\'\\\\;\\x00-\\x1f]', value) or any((part in ('.', '..') for part in parsed.path.split('/'))):
        raise ValueError(label + '无效，请填写完整的 HTTP 或 HTTPS 地址')
    try:
        parsed.port
    except ValueError:
        raise ValueError(label + '端口无效') from None
    return value

def origin(url):
    parsed = urlsplit(url)
    return (parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80))

def settings_path(config):
    return Path(config['state_dir']) / 'document-engine.json'

@contextlib.contextmanager
def settings_lock(config):
    path = settings_path(config).parent / '.document-engine.lock'
    path.parent.mkdir(parents=True, exist_ok=True, mode=448)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 384)
    stream = os.fdopen(fd, 'r+b', buffering=0)
    locked = False
    try:
        if path.is_symlink():
            raise ValueError('文档配置锁身份无效')
        if os.fstat(fd).st_size == 0:
            stream.write(b'0')
        deadline = time.monotonic() + 10
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    stream.seek(0)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ValueError('文档配置正在使用，请稍后重试')
                time.sleep(0.05)
        yield
    finally:
        if locked:
            if os.name == 'nt':
                import msvcrt
                stream.seek(0)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        stream.close()

def load_settings(config):
    path = settings_path(config)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {'enabled': False}
    if not isinstance(data, dict):
        raise ValueError('文档引擎配置格式无效')
    if not data.get('enabled'):
        return {'enabled': False}
    managed = data.get('managed_owner')
    if managed:
        from crb.server.office_engine import state
        owned = state(config)
        if owned['owner'] != managed or owned['secret'] != data.get('jwt_secret') or owned['mode'] == 'pending':
            raise ValueError('文档组件身份不匹配，请在服务端修复安装')
        if owned.get('paused'):
            return {'enabled': False}
        return dict(enabled=True, managed_owner=managed, jwt_secret=owned['secret'], editor_url=base_url(config.get('public_url'), '面板地址') + '/office-engine', service_url='http://crb-office.internal', callback_url='http://127.0.0.1:7900')
    editor = base_url(data.get('editor_url'), '文档引擎网页地址')
    service = base_url(data.get('service_url') or editor, '文档引擎内部地址')
    callback = base_url(data.get('callback_url') or config.get('public_url'), '文档引擎访问面板地址')
    if any((origin(editor) == origin(value) for value in (callback, config.get('public_url')) if value)):
        raise ValueError('文档引擎须使用与面板不同的域名或端口')
    secret = data.get('jwt_secret')
    if not isinstance(secret, str) or not 32 <= len(secret) <= 512 or '\x00' in secret:
        raise ValueError('文档引擎 JWT 密钥至少需要 32 个字符')
    return dict(enabled=True, editor_url=editor, service_url=service, callback_url=callback, jwt_secret=secret)

def save_settings(config, enabled, editor_url, service_url, callback_url, secret='', managed_owner=None):
    with settings_lock(config):
        path = settings_path(config)
        previous = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        data = dict(enabled=bool(enabled), editor_url=editor_url.strip(), service_url=service_url.strip(), callback_url=callback_url.strip(), jwt_secret=secret or previous.get('jwt_secret', ''))
        directory = Path(config['state_dir']) / 'document-editing'
        if directory.exists():
            for metadata in directory.glob('*/session.json'):
                state = json.loads(metadata.read_text(encoding='utf-8'))
                untouched = not state.get('engineTouched') and state['state'] == 'editing' and (time.time() - state['created'] > 900)
                if untouched:
                    state['closed'] = True
                    atomic_json(metadata, state)
                if not untouched and (not state.get('closed')) and (not state.get('finalCallback')) and (not (state.get('closeRequested') and state['state'] in ('failed', 'conflict'))):
                    raise ValueError('仍有文档会话未结束，请先关闭文档编辑器并确认回存结果')
        if managed_owner:
            from crb.server.office_engine import state
            owned = state(config)
            if owned['owner'] != managed_owner or owned['secret'] != secret or owned['mode'] == 'pending':
                raise ValueError('文档组件身份不匹配')
            data['managed_owner'] = managed_owner
        if data['enabled'] and (not managed_owner):
            editor = base_url(data['editor_url'], '文档引擎网页地址')
            base_url(data['service_url'] or editor, '文档引擎内部地址')
            callback = base_url(data['callback_url'] or config.get('public_url'), '文档引擎访问面板地址')
            if any((origin(editor) == origin(value) for value in (callback, config.get('public_url')) if value)):
                raise ValueError('文档引擎须使用与面板不同的域名或端口')
            if not isinstance(data['jwt_secret'], str) or not 32 <= len(data['jwt_secret']) <= 512 or '\x00' in data['jwt_secret']:
                raise ValueError('文档引擎 JWT 密钥至少需要 32 个字符')
        atomic_json(path, data)
        return {'ok': True, 'enabled': data['enabled'], 'secretConfigured': bool(data['jwt_secret'])}

def jwt_sign(payload, secret):
    encode = lambda data: base64.urlsafe_b64encode(data).rstrip(b'=')
    header = encode(b'{"alg":"HS256","typ":"JWT"}')
    body = encode(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode())
    signing = header + b'.' + body
    return (signing + b'.' + encode(hmac.new(secret.encode(), signing, hashlib.sha256).digest())).decode()

def jwt_verify(token, secret):
    if not isinstance(token, str) or len(token) > 1024 * 1024:
        raise ValueError('文档引擎签名无效')
    try:
        head, body, signature = token.split('.')
        decode = lambda value: base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
        header = json.loads(decode(head))
        payload = json.loads(decode(body))
        if not isinstance(header, dict):
            raise ValueError()
        expected = hmac.new(secret.encode(), (head + '.' + body).encode(), hashlib.sha256).digest()
        if header.get('alg') != 'HS256' or not hmac.compare_digest(decode(signature), expected):
            raise ValueError()
        if not isinstance(payload, dict):
            raise ValueError()
        for claim in ('exp', 'nbf'):
            if claim in payload and (type(payload[claim]) not in (int, float) or not __import__('math').isfinite(payload[claim])):
                raise ValueError()
        if payload.get('exp', float('inf')) < time.time() or payload.get('nbf', 0) > time.time() + 30:
            raise ValueError()
        return payload
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ValueError('文档引擎签名无效或已过期') from None

def validate_document(data, extension):
    if not data or len(data) > MAX_DOCUMENT:
        raise ValueError('文档为空或超过 32 MB 编辑上限')
    if extension not in EDITABLE:
        return
    expected = {'docx': 'word/document.xml', 'xlsx': 'xl/workbook.xml', 'pptx': 'ppt/presentation.xml'}[extension]
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 10000 or sum((item.file_size for item in entries)) > 256 * 1024 * 1024:
                raise ValueError('文档解压体积过大')
            names = {item.filename for item in entries}
            if '[Content_Types].xml' not in names or expected not in names:
                raise ValueError('文档内容与扩展名不一致')
            for item in entries:
                if item.filename.startswith(('/', '\\')) or '..' in item.filename.replace('\\', '/').split('/') or item.flag_bits & 1:
                    raise ValueError('不支持此加密或异常文档')
    except zipfile.BadZipFile:
        raise ValueError('不是有效的 Office 文档；加密文档请先解除密码保护') from None

class EngineHTTP:

    def __init__(self, settings):
        self.settings = settings

    def url(self, url):
        settings = self.settings
        allowed = {origin(settings['editor_url']), origin(settings['service_url'])}
        parsed = urlsplit(url)
        if parsed.username or parsed.password or parsed.fragment or (origin(url) not in allowed):
            raise ValueError('文档引擎返回了不匹配的下载地址')
        if origin(url) == origin(settings['editor_url']):
            source, target = (urlsplit(settings['editor_url']), urlsplit(settings['service_url']))
            if not parsed.path.startswith(source.path.rstrip('/') + '/'):
                raise ValueError('文档下载地址超出已配置引擎路径')
            path = target.path.rstrip('/') + parsed.path[len(source.path.rstrip('/')):]
            return urlunsplit((target.scheme, target.netloc, path, parsed.query, ''))
        if not parsed.path.startswith(urlsplit(settings['service_url']).path.rstrip('/') + '/'):
            raise ValueError('文档下载地址超出已配置引擎路径')
        return url

    def request(self, url, payload=None, limit=MAX_DOCUMENT):
        owner = self

        class Redirect(urllib.request.HTTPRedirectHandler):

            def redirect_request(self, req, fp, code, message, headers, new):
                checked = owner.url(new)
                result = super().redirect_request(req, fp, code, message, headers, checked)
                if result:
                    for name in ('Authorization', 'Cookie'):
                        result.remove_header(name)
                return result
        checked = self.url(url)
        headers = {'User-Agent': 'CodeRelayBridge-Document-Editor', 'Accept-Encoding': 'identity'}
        if payload is not None:
            headers['Content-Type'] = 'application/json'
            payload = dict(payload, token=jwt_sign(payload, self.settings['jwt_secret']))
        request = urllib.request.Request(checked, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        try:
            with urllib.request.build_opener(Redirect()).open(request, timeout=30) as response:
                if response.status != 200:
                    raise ValueError('文档引擎响应异常')
                data = response.read(limit + 1)
                if len(data) > limit:
                    raise ValueError('文档引擎响应超过上限')
                return data
        except ValueError:
            raise
        except Exception as error:
            raise ValueError('连接文档引擎失败（' + type(error).__name__ + '）') from None

class OfficeManager:

    def __init__(self, config, files, writable, transport=EngineHTTP):
        self.config, self.files, self.writable, self.transport = (config, files, writable, transport)
        self.root = Path(config['state_dir']) / 'document-editing'
        self.guard = threading.RLock()
        self.locks = {}
        self.workers = {}
        self.gateway = None

    def settings(self):
        return load_settings(self.config)

    def http(self, settings):
        if settings.get('managed_owner'):
            if self.gateway is None:
                from crb.server.office_gateway import ManagedGateway
                self.gateway = ManagedGateway(self)
            return self.gateway.http(settings)
        return self.transport(settings)

    def public_status(self):
        settings = self.settings()
        return dict(enabled=settings['enabled'], engine='ONLYOFFICE Docs', editableFormats=sorted(EDITABLE), viewableFormats=sorted(VIEWABLE), maxBytes=MAX_DOCUMENT, editorUrl=settings.get('editor_url', ''), help='在服务端管理界面的“文档编辑”中点击安装并启用，所有客户端共用')

    def csp(self):
        try:
            settings = self.settings()
        except (OSError, ValueError):
            return ('', '')
        if not settings['enabled']:
            return ('', '')
        return (settings['editor_url'] + '/web-apps/apps/api/documents/api.js', settings['editor_url'] + '/')

    def lock(self, session):
        if not re.fullmatch('[0-9a-f]{32}', session):
            raise ValueError('文档会话标识无效')
        with self.guard:
            return self.locks.setdefault(session, threading.RLock())

    def path(self, session):
        self.lock(session)
        directory = self.root / session
        if directory.is_symlink():
            raise ValueError('文档会话目录无效')
        return directory

    def read(self, session):
        try:
            return json.loads((self.path(session) / 'session.json').read_text(encoding='utf-8'))
        except FileNotFoundError:
            raise ValueError('文档会话已结束或不存在，请重新打开') from None

    def persist(self, state):
        atomic_json(self.path(state['id']) / 'session.json', state)

    def capability(self, session, purpose):
        return hmac.new(self.config['session_secret'].encode(), ('office/' + purpose + '/' + session).encode(), hashlib.sha256).hexdigest()

    def authorize(self, session, purpose, token):
        if not isinstance(token, str) or not hmac.compare_digest(self.capability(session, purpose), token):
            raise ValueError('文档访问凭据无效')
        state = self.read(session)
        if time.time() - state['created'] > 7 * 86400:
            raise ValueError('文档会话已过期，请重新打开')
        return state

    def snapshot(self, host, path, edit):
        before = self.files({'host': host, 'action': 'document_probe', 'payload': {'path': path}}) if edit else None
        chunks = []
        offset = 0
        metadata = None
        while True:
            row = self.files({'host': host, 'action': 'read', 'payload': {'path': before['path'] if before else path, 'offset': offset, 'limit': 2 * 1024 * 1024}})
            block = base64.b64decode(row['data'], validate=True)
            if row['size'] > MAX_DOCUMENT or row['nextOffset'] != offset + len(block) or (not block and (not row['eof'])):
                raise ValueError('文档超过编辑上限或读取中断')
            if metadata and (row['size'], row['path']) != (metadata['size'], metadata['path']):
                raise ValueError('读取期间文档已变化')
            chunks.append(block)
            offset += len(block)
            metadata = row
            if offset > MAX_DOCUMENT:
                raise ValueError('文档超过 32 MB 编辑上限')
            if row['eof']:
                break
        data = b''.join(chunks)
        digest = hashlib.sha256(data).hexdigest()
        if edit:
            after = self.files({'host': host, 'action': 'document_probe', 'payload': {'path': before['path']}})
            if before['sha256'] != digest or after['sha256'] != digest:
                raise ValueError('读取期间文档已被修改，请重新打开')
        return (data, metadata['path'], digest, bool(before and before.get('writable')), metadata.get('name') or path.replace('\\', '/').rsplit('/', 1)[-1])

    def open(self, request):
        self.collect()
        settings = self.settings()
        if not settings['enabled']:
            raise ValueError('尚未配置文档引擎，请在服务端管理界面连接自己的 ONLYOFFICE Docs')
        host = request.get('host')
        path = request.get('path')
        viewer = request.get('viewer')
        if not isinstance(host, str) or not host or (not isinstance(path, str)) or (not isinstance(viewer, str)) or (not re.fullmatch('[0-9a-f]{32}', viewer)):
            raise ValueError('文档打开参数无效')
        name = request.get('name') or path.replace('\\', '/').rsplit('/', 1)[-1]
        if not isinstance(name, str) or len(name) > 255 or any((c in name for c in ('/', '\\', '\x00', '\r', '\n'))):
            raise ValueError('文档名称无效')
        extension = Path(name).suffix.lower().lstrip('.')
        if extension not in VIEWABLE:
            raise ValueError('此格式不支持文档编辑器')
        view_only = request.get('viewOnly') is True
        editable = extension in EDITABLE and (not view_only) and self.writable(host)
        with settings_lock(self.config), self.guard:
            if not self.settings()['enabled']:
                raise ValueError('文档引擎已停用，请重新打开')
            existing = []
            if self.root.exists():
                for item in self.root.glob('*/session.json'):
                    state = json.loads(item.read_text(encoding='utf-8'))
                    if not state.get('closed'):
                        existing.append(state)
            state = next((s for s in existing if s['host'] == host and s['requestedPath'] == path and (bool(s.get('requestedViewOnly')) == view_only)), None)
            if state is None:
                if len(existing) >= 32:
                    raise ValueError('打开的文档过多，请先关闭其他文档编辑器')
                data, canonical, digest, writable, actual_name = self.snapshot(host, path, editable)
                validate_document(data, extension)
                identifier = uuid.uuid4().hex
                directory = self.path(identifier)
                directory.mkdir(parents=True, exist_ok=False, mode=448)
                snapshot = directory / 'source'
                snapshot.write_bytes(data)
                if os.name != 'nt':
                    snapshot.chmod(384)
                state = dict(id=identifier, key='crb-' + identifier, host=host, path=canonical, requestedPath=path, name=name, requestedViewOnly=view_only, extension=extension, editable=editable and writable, expected=digest, size=len(data), created=time.time(), updated=time.time(), state='editing', closed=False, sequence=0, savedSequence=0, saveNonce='', savedSha256='', error='')
                state['readOnlyReason'] = '原 App 的附件副本以只读方式打开；另存到普通目录后可编辑' if view_only else '请升级目标客户端以启用 Office 文档回存' if extension in EDITABLE and (not editable) else '目标文件或所在目录不可写，请检查目标电脑上的文件权限' if extension in EDITABLE and (not writable) else ''
                self.persist(state)
            state['created'] = time.time()
            self.persist(state)
            return self.editor_config(state, viewer, request.get('language'), request.get('theme'))

    def editor_config(self, state, viewer, language, theme):
        settings = self.settings()
        session = state['id']
        base = settings['callback_url']
        if state.get('finalCallback') or (state['state'] in ('failed', 'conflict') and state.get('closeRequested')):
            return dict(ok=True, id=session, editable=state['editable'], recoveryOnly=True, status=self.public_state(state))
        config = dict(documentType=VIEWABLE[state['extension']], type='desktop', width='100%', height='100%', document=dict(fileType=state['extension'], key=state['key'], title=state['name'], url=base + '/office/source/' + session + '?token=' + self.capability(session, 'source'), permissions=dict(edit=state['editable'], download=True, print=True, review=state['editable'])), editorConfig=dict(mode='edit' if state['editable'] else 'view', lang='zh' if str(language).startswith('zh') else 'en', callbackUrl=base + '/office/callback/' + session + '?token=' + self.capability(session, 'callback'), user=dict(id=viewer, name='CRB'), customization=dict(forcesave=False, autosave=True, uiTheme='theme-dark' if theme == 'dark' else 'theme-light', plugins=False, macros=False)))
        config['token'] = jwt_sign(dict(config, exp=int(time.time()) + 3600), settings['jwt_secret'])
        return dict(ok=True, id=session, editable=state['editable'], extension=state['extension'], apiUrl=settings['editor_url'] + '/web-apps/apps/api/documents/api.js', opaqueGateway=bool(settings.get('managed_owner')), config=config, status=self.public_state(state))

    def public_state(self, state):
        return {key: state.get(key) for key in ('id', 'state', 'closed', 'editable', 'sequence', 'savedSequence', 'savedSha256', 'error', 'updated', 'readOnlyReason')}

    def status(self, session):
        return self.public_state(self.read(session))

    def source(self, session, token):
        with settings_lock(self.config), self.lock(session):
            state = self.authorize(session, 'source', token)
            path = self.path(session) / 'source'
            if state['closed'] or not path.is_file():
                raise ValueError('文档源快照已释放，请重新打开')
            state['engineTouched'] = True
            self.persist(state)
            return path.read_bytes()

    def edited(self, session):
        state = self.read(session)
        path = self.path(session) / 'edited'
        if not path.is_file():
            raise ValueError('当前没有可下载的编辑副本')
        return (path.read_bytes(), state['name'])

    def force_save(self, session, fresh=False):
        with self.lock(session):
            state = self.read(session)
            if not state['editable'] or state['closed']:
                raise ValueError('此文档会话不接受保存')
            if state['state'] in ('pending', 'saving', 'failed', 'conflict') and (self.path(session) / 'edited').exists() and (not (fresh and state['state'] == 'conflict' and (not state.get('transaction')))):
                state['error'] = ''
                state['state'] = 'pending'
                self.persist(state)
                self.start_worker(session)
                return self.public_state(state)
            if state['saveNonce'] and time.time() - state['updated'] < 90:
                return self.public_state(state)
            state['sequence'] += 1
            state['saveNonce'] = uuid.uuid4().hex
            state['state'] = 'assembling'
            state['error'] = ''
            state['updated'] = time.time()
            self.persist(state)
            command = dict(c='forcesave', key=state['key'], userdata=state['saveNonce'])
        try:
            result = json.loads(self.http(self.settings()).request(self.settings()['service_url'] + '/coauthoring/CommandService.ashx', command, limit=65536))
            if result.get('error') not in (0, 4):
                raise ValueError('文档引擎尚不能保存，请稍后重试')
            with self.lock(session):
                state = self.read(session)
                if result.get('error') == 4 and state['saveNonce'] == command['userdata']:
                    state.update(state='editing', saveNonce='', error='', updated=time.time())
                    self.persist(state)
                return self.public_state(state)
        except Exception:
            with self.lock(session):
                state = self.read(session)
                if state['saveNonce'] != command['userdata']:
                    return self.public_state(state)
                state.update(state='failed', error='保存请求结果尚未确认，请核对文档状态后重试', updated=time.time())
                self.persist(state)
            raise

    def callback(self, session, token, body, authorization):
        with self.lock(session):
            state = self.authorize(session, 'callback', token)
            encoded = authorization.removeprefix('Bearer ') if authorization.startswith('Bearer ') else body.get('token', '')
            signed = jwt_verify(encoded, self.settings()['jwt_secret'])
            trusted = signed.get('payload', signed)
            if not isinstance(trusted, dict) or trusted.get('key') != state['key'] or type(trusted.get('status')) is not int:
                raise ValueError('文档回调身份不匹配')
            status = trusted['status']
            if state['closed']:
                return {'error': 0}
            if status in (1, 4):
                if status == 4:
                    state.update(closed=True, updated=time.time())
                    self.persist(state)
                    self.release_saved_files(state)
                return {'error': 0}
            if status in (3, 7):
                state.update(state='failed', error='文档引擎生成编辑副本失败，请保留窗口后重试', updated=time.time())
                self.persist(state)
                return {'error': 0}
            if status not in (2, 6):
                raise ValueError('不支持的文档回调状态')
            if not state['editable']:
                raise ValueError('只读文档不能回存')
            if status == 6 and (trusted.get('forcesavetype') != 0 or trusted.get('userdata') != state['saveNonce']):
                return {'error': 0}
            try:
                if trusted.get('filetype', state['extension']) != state['extension']:
                    raise ValueError('文档引擎改变了文件格式，未覆盖原文件')
                data = self.http(self.settings()).request(trusted.get('url', ''))
                validate_document(data, state['extension'])
                digest = hashlib.sha256(data).hexdigest()
                directory = self.path(session)
                fd, temporary = tempfile.mkstemp(prefix='edited-', dir=directory)
                try:
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, directory / 'edited')
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            except Exception:
                state.update(state='failed', error='获取编辑副本失败，请保留编辑窗口并重试保存', updated=time.time())
                self.persist(state)
                raise
            state.update(state='pending', pendingSha256=digest, finalCallback=status == 2, saveNonce='', updated=time.time(), error='')
            self.persist(state)
            self.start_worker(session)
            return {'error': 0}

    def start_worker(self, session):
        with self.guard:
            if session in self.workers and self.workers[session].is_alive():
                return

            def run():
                try:
                    self.save_to_client(session)
                finally:
                    with self.lock(session):
                        try:
                            pending = self.read(session)['state'] == 'pending'
                        except (OSError, ValueError):
                            pending = False
                        with self.guard:
                            self.workers.pop(session, None)
                        if pending:
                            self.start_worker(session)
            worker = threading.Thread(target=run, daemon=True, name='crb-document-save')
            self.workers[session] = worker
            worker.start()

    def save_to_client(self, session):
        with self.lock(session):
            state = self.read(session)
            transaction = state.get('transaction') or uuid.uuid4().hex
            state['transaction'] = transaction
            state.update(state='saving', updated=time.time())
            self.persist(state)
            host = state['host']
            data = (self.path(session) / 'edited').read_bytes()
            digest = hashlib.sha256(data).hexdigest()

            def call(action, payload):
                return self.files(dict(host=host, action=action, payload=payload))
            try:
                begin = call('document_begin', dict(path=state['path'], expectedSha256=state['expected'], size=len(data), transaction=transaction))
                if begin['state'] == 'committed':
                    if begin['sha256'] != digest:
                        raise ValueError('文档事务回执与编辑副本不一致')
                else:
                    offset = begin['received']
                    while offset < len(data):
                        block = data[offset:offset + 128 * 1024]
                        row = call('document_chunk', dict(transaction=transaction, offset=offset, data=base64.b64encode(block).decode()))
                        if row['received'] != offset + len(block):
                            raise ValueError('文档分块回执不连续')
                        offset = row['received']
                    committed = call('document_commit', dict(transaction=transaction, sha256=digest))
                    if committed.get('state') != 'committed' or committed.get('sha256') != digest:
                        raise ValueError('文档保存尚未确认')
                state.update(expected=digest, savedSha256=digest, savedSequence=state['sequence'], state='saved', error='', updated=time.time(), transaction='')
                if state.get('finalCallback'):
                    state['closed'] = True
                self.persist(state)
                try:
                    call('document_forget', dict(transaction=transaction))
                except Exception:
                    pass
                self.release_saved_files(state)
            except Exception as error:
                message = str(error)
                state.update(state='conflict' if '修改' in message or '变化' in message else 'failed', error=message[:300], updated=time.time())
                self.persist(state)
                if state['state'] == 'conflict':
                    try:
                        call('document_abort', dict(transaction=transaction))
                        call('document_forget', dict(transaction=transaction))
                        state['transaction'] = ''
                        self.persist(state)
                    except Exception:
                        pass

    def release_saved_files(self, state):
        if state.get('closed') and state['state'] not in ('pending', 'saving', 'failed', 'conflict', 'assembling'):
            for name in ('source', 'edited', 'edited.new'):
                file = self.path(state['id']) / name
                if file.exists():
                    file.unlink()

    def close(self, session, retain=False):
        with self.lock(session):
            state = self.read(session)
            if retain and state['state'] in ('failed', 'conflict') and (self.path(session) / 'edited').is_file():
                state.update(closeRequested=time.time(), updated=time.time())
                self.persist(state)
                return self.public_state(state)
            if state['state'] in ('pending', 'saving', 'assembling', 'failed', 'conflict'):
                raise ValueError('文档回存尚未完成，请先保存或保留编辑副本')
            state.update(updated=time.time(), closeRequested=time.time())
            if not state.get('engineTouched') and state['state'] == 'editing':
                state['closed'] = True
            self.persist(state)
            self.release_saved_files(state)
            return self.public_state(state)

    def collect(self):
        """Collect closed snapshots; failed saves are recovery data, never caches."""
        if not self.root.exists():
            return
        with self.guard:
            for item in self.root.glob('*/session.json'):
                identifier = item.parent.name
                if not re.fullmatch('[0-9a-f]{32}', identifier) or item.parent.is_symlink():
                    continue
                lock = self.lock(identifier)
                if not lock.acquire(blocking=False):
                    continue
                try:
                    state = self.read(identifier)
                    if not state.get('engineTouched') and state['state'] == 'editing' and (time.time() - state['created'] > 900):
                        state.update(closed=True)
                        self.persist(state)
                    if state.get('closeRequested') and time.time() - state['closeRequested'] > 600 and (state['state'] in ('editing', 'saved')):
                        state.update(closed=True)
                        self.persist(state)
                    self.release_saved_files(state)
                    if state.get('closed') and time.time() - state['updated'] > 86400 and (state['state'] not in ('failed', 'conflict', 'saving', 'pending', 'assembling')):
                        item.unlink()
                        item.parent.rmdir()
                        self.locks.pop(identifier, None)
                        self.workers.pop(identifier, None)
                finally:
                    lock.release()
