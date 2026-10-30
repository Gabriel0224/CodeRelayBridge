"""Host-scoped loopback web previews over on-demand authenticated client wires."""
import base64
from html import escape
from html.parser import HTMLParser
import hashlib
import http.client
from http.cookies import SimpleCookie
from email.utils import parsedate_to_datetime
from datetime import timezone
import json
import re
import secrets
import socket
import threading
import time
import zlib
from urllib.parse import urlsplit, urlunsplit, urljoin, unquote
from crb.common.ws_stream import ServerWebSocket
from crb.server.office_wire import Wire
PREFIX = '/local-preview/'
MAX_BODY = 16 * 1024 * 1024

def local_url(value):
    if not isinstance(value, str) or len(value) > 4096 or re.search('[\\s\\\\\\x00-\\x1f]', value):
        raise ValueError('请输入完整的本机网页地址')
    parsed = urlsplit(value)
    try:
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    except ValueError:
        raise ValueError('本机网页端口无效') from None
    if parsed.scheme not in ('http', 'https') or parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or parsed.username or parsed.password:
        raise ValueError('只支持目标电脑的 localhost、127.0.0.1 或 [::1] 网页')
    path = parsed.path or '/'
    if not path.startswith('/') or path.startswith('//'):
        raise ValueError('本机网页路径无效')
    return {'scheme': parsed.scheme, 'host': parsed.hostname, 'port': port, 'path': path, 'query': parsed.query, 'fragment': parsed.fragment}

def origin(target):
    host = '[' + target['host'] + ']' if ':' in target['host'] else target['host']
    return target['scheme'] + '://' + host + ':' + str(target['port'])

class HTMLRewrite(HTMLParser):

    def __init__(self, convert, injection, base=None):
        super().__init__(convert_charrefs=False)
        self.callback = convert
        self.base = base
        self.injection = injection
        self.parts = []
        self.inserted = False
        self.context = None

    def convert(self, value):
        if self.base and value and (not value.startswith(('#', 'data:', 'blob:', 'javascript:', 'mailto:'))):
            value = urljoin(self.base, value)
        return self.callback(value)

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.context = tag
        values = []
        for key, value in attrs:
            if tag == 'base' and key == 'href' and (value is not None) and self.base:
                self.base = urljoin(self.base, value)
                value = self.callback(self.base)
            elif value is not None and key in ('src', 'href', 'action', 'poster', 'data', 'formaction'):
                value = self.convert(value)
            if value is not None and key == 'srcset':
                value = ', '.join((' '.join([self.convert(fields[0]), *fields[1:]]) for item in value.split(',') if (fields := item.strip().split()))) if not value.lstrip().startswith('data:') else value
            values.append(key if value is None else key + '="' + escape(value, quote=True) + '"')
        self.parts.append('<' + tag + (' ' + ' '.join(values) if values else '') + '>')
        if tag == 'head' and (not self.inserted):
            self.parts.append(self.injection)
            self.inserted = True

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == self.context:
            self.context = None
        self.parts.append('</' + tag + '>')

    def handle_data(self, value):
        if self.context == 'script':
            value = re.sub('((?:\\bfrom\\s*|\\bimport\\s*(?:\\(\\s*)?)[\\"\\x27])(/[^/\\"\\x27]*)([\\"\\x27])', lambda m: m[1] + self.convert(m[2]) + m[3], value)
        elif self.context == 'style':
            value = re.sub('(url\\(\\s*[\\"\\x27]?)(/[^/][^\\s\\)\\"\\x27]*)([\\"\\x27]?\\s*\\))', lambda m: m[1] + self.convert(m[2]) + m[3], value)
        self.parts.append(value)

    def handle_comment(self, value):
        self.parts.append('<!--' + value + '-->')

    def handle_decl(self, value):
        self.parts.append('<!' + value + '>')

    def handle_entityref(self, value):
        self.parts.append('&' + value + ';')

    def handle_charref(self, value):
        self.parts.append('&#' + value + ';')

    def unknown_decl(self, value):
        pass

    def result(self):
        value = ''.join(self.parts)
        return value if self.inserted else self.injection + value

class PreviewSession:

    def __init__(self, host, target):
        self.id = secrets.token_hex(24)
        self.host = host
        self.target = target
        self.created = self.touched = time.monotonic()
        self.views = 0
        self.connections = set()
        self.cookies = {}
        self.storage = {}
        self.guard = threading.RLock()
        self.closed = False

    @property
    def prefix(self):
        return PREFIX + self.id

    def convert(self, value):
        if not value or value.startswith(('#', 'data:', 'blob:', 'javascript:', 'mailto:')):
            return value
        if value.startswith(self.prefix + '/'):
            return value
        if value.startswith('/') and (not value.startswith('//')):
            return self.prefix + value
        try:
            parsed = urlsplit(value)
            port = parsed.port or (443 if parsed.scheme in ('https', 'wss') else 80)
        except ValueError:
            return value
        if parsed.hostname in ('localhost', '127.0.0.1', '::1') and port == self.target['port'] and ({'ws': 'http', 'wss': 'https'}.get(parsed.scheme, parsed.scheme) == self.target['scheme']):
            return self.prefix + (parsed.path or '/') + ('?' + parsed.query if parsed.query else '') + ('#' + parsed.fragment if parsed.fragment else '')
        return value

    def cookie(self, path):
        with self.guard:
            for key, row in list(self.cookies.items()):
                if row.get('expires') is not None and row['expires'] <= time.time():
                    self.cookies.pop(key, None)
            return '; '.join((name + '=' + row['value'] for (name, scope), row in self.cookies.items() if (path == scope or path.startswith(scope if scope.endswith('/') else scope + '/')) and (not row['secure'] or self.target['scheme'] == 'https')))

    def set_cookie(self, value, javascript=False):
        if len(value) > 8192:
            return
        try:
            parsed = SimpleCookie(value)
        except Exception:
            return
        with self.guard:
            for name, row in parsed.items():
                if row['domain'] and row['domain'].lstrip('.').lower() not in (self.target['host'], 'localhost', '127.0.0.1', '::1'):
                    continue
                if javascript and row['httponly']:
                    continue
                if re.search('[\\x00-\\x1f\\x7f-\\uffff]', row.coded_value):
                    continue
                path = row['path'] if row['path'].startswith('/') else '/'
                key = (name, path)
                if javascript and self.cookies.get(key, {}).get('httpOnly'):
                    continue
                expires = None
                if row['max-age']:
                    try:
                        expires = time.time() + int(row['max-age'])
                    except ValueError:
                        pass
                elif row['expires']:
                    try:
                        date = parsedate_to_datetime(row['expires'])
                        expires = (date if date.tzinfo else date.replace(tzinfo=timezone.utc)).timestamp()
                    except (ValueError, TypeError, OverflowError):
                        pass
                if expires is not None and expires <= time.time():
                    self.cookies.pop(key, None)
                    continue
                if len(self.cookies) >= 64 and key not in self.cookies:
                    continue
                self.cookies[key] = {'value': row.coded_value, 'secure': bool(row['secure']), 'httpOnly': bool(row['httponly']), 'expires': expires}

    def close(self):
        with self.guard:
            self.closed = True
            connections = list(self.connections)
            self.cookies.clear()
            self.storage.clear()
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()

class PreviewManager:

    def __init__(self, app):
        self.app = app
        self.guard = threading.RLock()
        self.sessions = {}
        self.channels = {}
        self.closed = threading.Event()
        self.reaper = None

    def reap(self):
        while not self.closed.wait(5):
            now = time.monotonic()
            with self.guard:
                expired = [s.id for s in self.sessions.values() if now - s.created > 8 * 3600 or (not s.views and now - s.touched > 20)]
            for identity in expired:
                self.close_session(identity)

    def open(self, host, url):
        target = local_url(url)
        physical = self.app.client_access_host(host)
        if not physical:
            raise ValueError('目标客户端不支持本地网页预览，请升级客户端')
        with self.app.lock:
            caps = self.app.hosts.get(physical, {}).get('capabilities', {})
            if caps.get('localWebPreview') is not True:
                raise ValueError('目标客户端尚未升级本地网页预览功能')
        with self.guard:
            if self.closed.is_set():
                raise ValueError('目标电脑连接已断开，请稍后刷新')
            if len(self.sessions) >= 32:
                raise ValueError('本地网页预览数量已达上限，请先关闭不用的预览')
            if not self.reaper:
                self.reaper = threading.Thread(target=self.reap, daemon=True, name='local-preview-expiry')
                self.reaper.start()
            session = PreviewSession(physical, target)
            self.sessions[session.id] = session
            channel = self.channels.get(physical)
            if channel is None:
                channel = {'key': secrets.token_hex(24), 'wire': None, 'socket': None, 'ready': threading.Event(), 'queued': time.monotonic()}
                self.channels[physical] = channel
                try:
                    self.app.queue(physical, '', 'client_web_preview', json.dumps({'channel': channel['key']}))
                except BaseException:
                    self.sessions.pop(session.id, None)
                    self.channels.pop(physical, None)
                    raise
        return {'ok': True, 'id': session.id, 'host': host, 'url': url, 'previewUrl': session.prefix + target['path'] + ('?' + target['query'] if target['query'] else '') + ('#' + target['fragment'] if target['fragment'] else '')}

    def get(self, identity):
        if not re.fullmatch('[0-9a-f]{48}', identity):
            raise ValueError('本地网页预览已结束')
        with self.guard:
            session = self.sessions.get(identity)
            if session is None or session.closed:
                raise ValueError('本地网页预览已结束，请重新打开')
            session.touched = time.monotonic()
            return session

    def close_session(self, identity):
        with self.guard:
            session = self.sessions.pop(identity, None)
            if not session:
                return
            session.close()
            if not any((row.host == session.host for row in self.sessions.values())):
                channel = self.channels.pop(session.host, None)
                if channel:
                    if channel['wire']:
                        channel['wire'].close()
                    if channel['socket']:
                        channel['socket'].close()

    def invalidate_host(self, host):
        with self.guard:
            identities = [row.id for row in self.sessions.values() if row.host == host]
        for identity in identities:
            self.close_session(identity)

    def close(self):
        self.closed.set()
        with self.guard:
            identities = list(self.sessions)
        for identity in identities:
            self.close_session(identity)

    def agent(self, handler, host, key):
        with self.guard:
            channel = self.channels.get(host)
            if not channel or not secrets.compare_digest(channel['key'], key):
                return handler.response(403, {'error': '网页通道已失效'})
            if channel['wire'] and (not channel['wire'].closed.is_set()):
                return handler.response(409, {'error': '网页通道已连接'})

        def valid():
            with self.guard:
                wanted = self.channels.get(host) is channel
            if not wanted:
                return False
            try:
                authorized = handler.agent_identity()[0] == host
            except Exception:
                authorized = False
            if not authorized:
                self.invalidate_host(host)
            return authorized
        stream = ServerWebSocket.upgrade(handler, valid)
        wire = Wire(stream, stream, lambda role: (_ for _ in ()).throw(ValueError('unsupported reverse target')), first=1)
        with self.guard:
            if self.channels.get(host) is not channel:
                stream.close()
                return
            channel.update(wire=wire, socket=stream, valid=valid)
            channel['ready'].set()
        try:
            while not wire.closed.wait(0.5) and (not stream.closed.is_set()):
                pass
        finally:
            wire.close()
            stream.close()
            with self.guard:
                if channel['wire'] is wire:
                    channel.update(wire=None, socket=None)
                    channel['ready'].clear()

    def lease(self, handler, session):
        stream = ServerWebSocket.upgrade(handler, lambda: not session.closed and (not self.closed.is_set()))
        with self.guard:
            session.views += 1
        try:
            while stream.read(1):
                pass
        finally:
            stream.close()
            with self.guard:
                session.views -= 1
                session.touched = time.monotonic()

    def connection(self, session):
        with self.guard:
            channel = self.channels.get(session.host)
        if channel and channel.get('valid') and (not channel['valid']()):
            raise ValueError('目标客户端授权已失效，请重新打开预览')
        if channel and (not channel['ready'].is_set()):
            with self.guard:
                if time.monotonic() - channel['queued'] > 30:
                    self.app.queue(session.host, '', 'client_web_preview', json.dumps({'channel': channel['key']}))
                    channel['queued'] = time.monotonic()
        if not channel or not channel['ready'].wait(12):
            raise ValueError('目标电脑的网页通道尚未连接，请稍后刷新')
        with self.guard:
            wire = channel['wire']
        if wire is None or wire.closed.is_set():
            raise ValueError('目标电脑连接已断开，请稍后刷新')
        connection = wire.open(json.dumps({key: session.target[key] for key in ('scheme', 'host', 'port')}))
        connection.settimeout(15)
        with session.guard:
            if session.closed:
                connection.close()
                raise ValueError('预览已结束')
            session.connections.add(connection)
        return connection

    def handle(self, handler, web_root):
        parsed = urlsplit(handler.path)
        match = re.fullmatch('/local-preview/([0-9a-f]{48})(/.*)?', parsed.path)
        if not match:
            return self.error(handler, 404, '本地网页预览地址无效')
        try:
            session = self.get(match[1])
        except ValueError as error:
            return self.error(handler, 410, str(error))
        path = match[2] or '/'
        if path == '/__crb/lease' and handler.command == 'GET':
            return self.lease(handler, session)
        if handler.command == 'OPTIONS':
            handler.send_response(204)
            self.headers(handler)
            requested = handler.headers.get('Access-Control-Request-Headers', '')
            if requested and len(requested) < 1024 and re.fullmatch('[A-Za-z0-9_, -]+', requested):
                handler.send_header('Access-Control-Allow-Headers', requested)
            handler.send_header('Content-Length', '0')
            handler.end_headers()
            return
        if path == '/__crb/runtime.js':
            return self.data(handler, 200, (web_root / 'local-preview-runtime.js').read_bytes(), 'application/javascript', cache=True)
        if path in ('/__crb/cookie', '/__crb/storage') and handler.command == 'POST':
            try:
                size = int(handler.headers.get('Content-Length', '0'))
                if not 0 < size <= 65536 or handler.headers.get('Transfer-Encoding') or len(handler.headers.get_all('Content-Length', [])) != 1:
                    raise ValueError()
                data = json.loads(handler.rfile.read(size))
                handler._post_body_handled = True
                if path.endswith('/cookie'):
                    if not isinstance(data.get('cookie'), str) or len(data['cookie']) > 8192:
                        raise ValueError()
                    session.set_cookie(data['cookie'], javascript=True)
                else:
                    if data.get('name') not in ('localStorage', 'sessionStorage') or not isinstance(data.get('values'), dict) or len(data['values']) > 256 or any((not isinstance(k, str) or not isinstance(v, str) for k, v in data['values'].items())):
                        raise ValueError()
                    with session.guard:
                        session.storage[data['name']] = data['values']
                return self.data(handler, 200, b'{}', 'application/json')
            except (ValueError, TypeError):
                return self.error(handler, 400, '本地网页 Cookie 参数无效')
        if path.startswith('/__crb/'):
            return self.error(handler, 404, '未找到预览资源')
        if '\\' in path or '\x00' in path or any((piece in ('.', '..') for piece in unquote(path).split('/'))):
            return self.error(handler, 400, '本地网页路径无效')
        return self.proxy(handler, session, path + ('?' + parsed.query if parsed.query else ''))

    @staticmethod
    def headers(handler):
        handler.send_header('Content-Security-Policy', 'sandbox allow-scripts allow-forms allow-popups allow-downloads')
        handler.send_header('Access-Control-Allow-Origin', 'null')
        handler.send_header('Access-Control-Allow-Credentials', 'true')
        handler.send_header('Access-Control-Allow-Methods', 'GET,HEAD,POST,PUT,PATCH,DELETE,OPTIONS')
        handler.send_header('Access-Control-Allow-Headers', 'Content-Type,Authorization,Range,X-Requested-With,Cache-Control')
        handler.send_header('Access-Control-Expose-Headers', 'Content-Type,Content-Length,ETag,Location')
        handler.send_header('Referrer-Policy', 'no-referrer')
        handler.send_header('X-Content-Type-Options', 'nosniff')
        handler.send_header('Cross-Origin-Resource-Policy', 'cross-origin')

    def data(self, handler, status, data, mime, cache=False):
        handler.send_response(status)
        self.headers(handler)
        handler.send_header('Content-Type', mime)
        handler.send_header('Content-Length', str(len(data)))
        handler.send_header('Cache-Control', 'private,max-age=3600' if cache else 'no-store')
        handler.end_headers()
        if handler.command != 'HEAD':
            handler.wfile.write(data)
        handler.count_public_traffic(len(data) if handler.command != 'HEAD' else 0, count_request=True)

    def error(self, handler, status, message):
        handler.close_connection = True
        handler._post_body_handled = True
        from crb.i18n.ui_translations import MESSAGES
        translate = lambda value: MESSAGES.get(value, value) if not handler.headers.get('Accept-Language', 'zh').lower().startswith('zh') else value
        data = ('<!doctype html><meta charset="utf-8"><title>' + escape(translate('本地网页预览')) + '</title><h2>' + escape(translate(message)) + '</h2><p>' + escape(translate('请确认目标电脑在线、网页服务仍在运行，然后刷新预览。')) + '</p><button onclick="location.reload()">' + escape(translate('刷新预览')) + '</button>').encode()
        return self.data(handler, status, data, 'text/html; charset=utf-8')

    def proxy(self, handler, session, path):
        if handler.command not in ('GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE'):
            return self.error(handler, 405, '不支持的网页请求')
        connection = None
        response = None
        started = False
        try:
            if handler.headers.get('Transfer-Encoding') or len(handler.headers.get_all('Content-Length', [])) > 1:
                raise ValueError('不支持此网页请求体')
            size = int(handler.headers.get('Content-Length', '0'))
            if not 0 <= size <= MAX_BODY:
                raise ValueError('网页请求超过 16 MB 上限')
            body = handler.rfile.read(size) if size else b''
            handler._post_body_handled = True
            excluded = {'host', 'cookie', 'authorization', 'origin', 'referer', 'connection', 'accept-encoding', 'transfer-encoding', 'expect', 'forwarded', 'proxy-authorization', 'proxy-connection'}
            request = {name.lower(): value for name, value in handler.headers.items() if name.lower() not in excluded and (not name.lower().startswith(('x-codex-', 'x-relay-', 'x-forwarded-', 'sec-fetch-', 'sec-ch-')))}
            if handler.headers.get('Origin') == 'null' and handler.headers.get('Authorization'):
                request['authorization'] = handler.headers['Authorization']
            target_origin = origin(session.target)
            request.update({'host': urlsplit(target_origin).netloc, 'connection': 'Upgrade' if request.get('upgrade') else 'close', 'accept-encoding': 'identity'})
            if handler.headers.get('Origin'):
                request['origin'] = target_origin
            request['referer'] = target_origin + path
            cookie = session.cookie(urlsplit(path).path)
            if cookie:
                request['cookie'] = cookie
            connection = self.connection(session)
            connection.sendall((handler.command + ' ' + path + ' HTTP/1.1\r\n' + ''.join((k + ': ' + v + '\r\n' for k, v in request.items())) + '\r\n').encode('iso-8859-1') + body)
            response = http.client.HTTPResponse(connection, method=handler.command)
            response.begin()
            fields = response.getheaders()
            for key, value in fields:
                if key.lower() == 'set-cookie':
                    session.set_cookie(value)
            mime = response.getheader('Content-Type', 'application/octet-stream')
            kind = 'html' if 'text/html' in mime else 'css' if 'text/css' in mime else 'js' if 'javascript' in mime or 'ecmascript' in mime else ''
            changed = bool(kind and handler.command != 'HEAD' and (response.status == 200))
            data = None
            if changed:
                encoding = response.getheader('Content-Encoding', 'identity').lower()
                data = response.read(MAX_BODY + 1)
                if encoding not in ('identity', ''):
                    if encoding not in ('gzip', 'deflate'):
                        raise ValueError('不支持的网页资源编码')
                    decoder = zlib.decompressobj(31 if encoding == 'gzip' else 15)
                    data = decoder.decompress(data, MAX_BODY + 1)
                    if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                        raise ValueError('网页资源解压超过上限或编码无效')
                if len(data) > MAX_BODY:
                    raise ValueError('网页资源超过 16 MB 上限')
                charset = re.search('charset=([\\w-]+)', mime, re.I)
                text = data.decode(charset.group(1) if charset else 'utf-8')
                if kind == 'html':
                    config = {'prefix': session.prefix, 'target': target_origin, 'id': session.id, 'storage': session.storage, 'cookie': '; '.join((name + '=' + row['value'] for (name, scope), row in session.cookies.items() if not row['httpOnly'] and urlsplit(path).path.startswith(scope)))}
                    encoded = base64.b64encode(json.dumps(config).encode()).decode()
                    rewrite = HTMLRewrite(session.convert, '<script src="' + session.prefix + '/__crb/runtime.js" data-preview="' + encoded + '"></script>', target_origin + path)
                    rewrite.feed(text)
                    text = rewrite.result()
                elif kind == 'css':
                    text = re.sub('(url\\(\\s*[\\"\\x27]?)(/[^/][^\\s\\)\\"\\x27]*)([\\"\\x27]?\\s*\\))', lambda m: m[1] + session.convert(m[2]) + m[3], text)
                else:
                    text = re.sub('((?:\\bfrom\\s*|\\bimport\\s*(?:\\(\\s*)?)[\\"\\x27])(/[^/\\"\\x27]*)([\\"\\x27])', lambda m: m[1] + session.convert(m[2]) + m[3], text)
                data = text.encode()
                mime = mime.split(';')[0] + '; charset=utf-8'
            started = True
            handler.send_response(response.status)
            for key, value in fields:
                lower = key.lower()
                if lower == 'set-cookie':
                    continue
                if lower in ('connection', 'transfer-encoding', 'content-security-policy', 'content-security-policy-report-only', 'x-frame-options', 'access-control-allow-origin', 'access-control-allow-credentials', 'access-control-expose-headers', 'referrer-policy', 'x-content-type-options', 'cross-origin-opener-policy', 'cross-origin-embedder-policy', 'cross-origin-resource-policy', 'www-authenticate'):
                    continue
                if lower == 'content-length' and response.chunked:
                    continue
                if lower == 'cache-control':
                    continue
                if changed and lower in ('content-length', 'content-type', 'content-encoding', 'etag'):
                    continue
                if lower == 'location':
                    value = session.convert(urljoin(target_origin + path, value))
                    if urlsplit(value).netloc:
                        continue
                handler.send_header(key, value)
            if changed:
                handler.send_header('Content-Type', mime)
                handler.send_header('Content-Length', str(len(data)))
                handler.send_header('ETag', '"crb-preview-' + hashlib.sha256(data).hexdigest()[:24] + '"')
            original_cache = response.getheader('Cache-Control', 'max-age=0, must-revalidate')
            cache = 'private, no-store' if kind == 'html' or 'json' in mime or 'event-stream' in mime else 'private, ' + re.sub('(?i)\\bpublic\\s*,?\\s*', '', original_cache)
            handler.send_header('Cache-Control', cache)
            self.headers(handler)
            handler.send_header('Connection', 'Upgrade' if response.status == 101 else 'close')
            handler.end_headers()
            handler.count_public_traffic(0, count_request=True)
            if response.status == 101:
                connection.settimeout(None)
                handler.connection.settimeout(None)

                def downstream():
                    try:
                        while True:
                            block = response.fp.read1(65536)
                            if not block:
                                break
                            handler.wfile.write(block)
                            handler.wfile.flush()
                            handler.count_public_traffic(len(block))
                    except OSError:
                        pass
                    finally:
                        try:
                            handler.connection.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                threading.Thread(target=downstream, daemon=True, name='local-preview-websocket').start()
                while not session.closed:
                    block = handler.rfile.read1(65536)
                    if not block:
                        break
                    connection.sendall(block)
                    handler.count_public_upload(len(block))
            elif handler.command != 'HEAD':
                if data is not None:
                    handler.wfile.write(data)
                    handler.count_public_traffic(len(data))
                else:
                    connection.settimeout(None)
                    while not session.closed:
                        block = response.read1(65536)
                        if not block:
                            break
                        handler.wfile.write(block)
                        handler.wfile.flush()
                        handler.count_public_traffic(len(block))
        except (OSError, ValueError, UnicodeError, LookupError, http.client.HTTPException):
            if not started:
                self.error(handler, 503, '目标网页暂不可用或连接已断开')
        finally:
            if connection:
                with session.guard:
                    session.connections.discard(connection)
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()
            if response:
                response.close()
            handler.close_connection = True
