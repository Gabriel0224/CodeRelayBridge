"""Isolated browser gateway and scoped engine callbacks over a private wire."""
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler
import json
import re
import select
import socket
import threading
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit
from crb.server.office import EngineHTTP, MAX_DOCUMENT, jwt_sign
from crb.server.office_wire import Wire
PREFIX = '/office-engine'
CALLBACK_PORT = 7900

class ManagedHTTP(EngineHTTP):

    def __init__(self, settings, gateway):
        super().__init__(settings)
        self.gateway = gateway

    def request(self, url, payload=None, limit=MAX_DOCUMENT):
        for _ in range(4):
            target = urlsplit(self.url(url))
            body = None if payload is None else json.dumps(dict(payload, token=jwt_sign(payload, self.settings['jwt_secret']))).encode()
            connection = self.gateway.connection(self.settings)
            try:
                path = target.path + ('?' + target.query if target.query else '')
                headers = {'Host': '127.0.0.1', 'Connection': 'close', 'Accept-Encoding': 'identity'}
                if body is not None:
                    headers.update({'Content-Type': 'application/json', 'Content-Length': str(len(body))})
                connection.sendall((('POST' if body is not None else 'GET') + ' ' + path + ' HTTP/1.1\r\n' + ''.join((name + ': ' + value + '\r\n' for name, value in headers.items())) + '\r\n').encode() + (body or b''))
                response = http.client.HTTPResponse(connection)
                response.begin()
                if response.status in (301, 302, 303, 307, 308):
                    if payload is not None:
                        raise ValueError('文档引擎命令发生意外重定向')
                    url = self.url(urljoin(url, response.getheader('Location', '')))
                    continue
                if response.status != 200:
                    raise ValueError('文档引擎响应异常')
                data = response.read(limit + 1)
                if len(data) > limit:
                    raise ValueError('文档引擎响应超过上限')
                return data
            except (OSError, http.client.HTTPException):
                raise ValueError('文档引擎通道暂不可用，请在服务端检查引擎状态') from None
            finally:
                connection.close()
        raise ValueError('文档引擎重定向过多')

class ManagedGateway:

    def __init__(self, office, connector=None):
        self.office = office
        self.connector = connector
        self.guard = threading.RLock()
        self.wire = None
        self.broker = None
        self.identity = None

    def http(self, settings):
        return ManagedHTTP(settings, self)

    def connection(self, settings):
        with self.guard:
            identity = settings['managed_owner']
            if self.identity != identity or self.wire is None or self.wire.closed.is_set():
                self.close()
                if self.connector is None:
                    from crb.server.office_engine import open_broker
                    broker = open_broker(self.office.config, identity)
                else:
                    broker = self.connector(identity)
                self.broker = broker
                self.identity = identity
                self.wire = Wire(broker.reader, broker.writer, self.callback_connection, first=1)
            wire = self.wire
        connection = wire.open('engine')
        connection.settimeout(45)
        return connection

    def callback_connection(self, role):
        if role != 'callback':
            raise ValueError('unsupported engine callback target')
        application, transport = socket.socketpair()
        application.settimeout(45)
        office = self.office

        class Callback(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *_):
                pass

            def reply(self, status, data, kind='application/json'):
                data = data if isinstance(data, bytes) else json.dumps(data).encode()
                self.send_response(status)
                self.send_header('Content-Type', kind)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(data)
                self.close_connection = True

            def do_GET(self):
                parsed = urlsplit(self.path)
                match = re.fullmatch('/office/source/([0-9a-f]{32})', parsed.path)
                if not match:
                    return self.reply(404, {'error': 1})
                try:
                    return self.reply(200, office.source(match[1], parse_qs(parsed.query).get('token', [''])[0]), 'application/octet-stream')
                except (ValueError, OSError):
                    return self.reply(403, {'error': 1})

            def do_POST(self):
                parsed = urlsplit(self.path)
                match = re.fullmatch('/office/callback/([0-9a-f]{32})', parsed.path)
                try:
                    if not match or self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
                        raise ValueError()
                    size = int(self.headers['Content-Length'])
                    if not 0 < size <= 1024 * 1024:
                        raise ValueError()
                    body = json.loads(self.rfile.read(size))
                    if not isinstance(body, dict):
                        raise ValueError()
                    result = office.callback(match[1], parse_qs(parsed.query).get('token', [''])[0], body, self.headers.get('Authorization', ''))
                    return self.reply(200, result)
                except (ValueError, OSError):
                    return self.reply(403, {'error': 1})

        def run():
            try:
                Callback(application, ('127.0.0.1', 0), None)
            except (OSError, ValueError):
                pass
            finally:
                application.close()
        threading.Thread(target=run, daemon=True, name='office-scoped-callback').start()
        return transport

    def close(self):
        with self.guard:
            if self.wire:
                self.wire.close()
            if self.broker:
                self.broker.close()
            self.wire = None
            self.broker = None
            self.identity = None

    @staticmethod
    def browser_path(path):
        value = unquote(urlsplit(path).path)
        if '%' in value or not value.startswith(PREFIX + '/') or '\\' in value or ('\x00' in value) or any((p in ('.', '..') for p in value.split('/'))):
            return None
        relative = value[len(PREFIX) + 1:]
        if re.search('(?:^|/)(?:CommandService\\.ashx|admin|adminpanel|controlpanel|internal)(?:/|$)', relative, re.I):
            return None
        plain = re.sub('^\\d+\\.\\d+\\.\\d+-[a-zA-Z0-9]+/', '', relative)
        if plain.split('/', 1)[0].lower() not in ('web-apps', 'sdkjs', 'sdkjs-plugins', 'fonts', 'cache', 'doc', 'coauthoring', 'downloadas', 'info', 'healthcheck', 'themes.json', 'dictionaries'):
            return None
        return relative

    def handle(self, handler, settings, web_root):
        """Engine pages always receive an opaque sandbox, also on direct visits."""
        path = self.browser_path(handler.path)
        if path is None:
            return handler.response(404, {'error': '没有找到此资源'})
        if handler.command == 'OPTIONS':
            handler.send_response(204)
            self.cors(handler)
            handler.send_header('Content-Length', '0')
            handler.end_headers()
            return
        if handler.command not in ('GET', 'HEAD', 'POST'):
            return handler.response(405, {'error': '不支持的文档请求'})
        if path == 'web-apps/apps/api/documents/crb-worker.js':
            data = (web_root / 'office-worker.js').read_bytes()
            handler.send_response(200)
            handler.send_header('Content-Type', 'application/javascript')
            handler.send_header('Content-Length', str(len(data)))
            handler.send_header('Cache-Control', 'public,max-age=86400')
            self.cors(handler)
            handler.end_headers()
            if handler.command != 'HEAD':
                handler.wfile.write(data)
            return
        if handler.headers.get('Transfer-Encoding'):
            return handler.response(400, {'error': '不支持的文档请求体'})
        if len(handler.headers.get_all('Content-Length', [])) > 1:
            return handler.response(400, {'error': '请求长度无效'})
        size = int(handler.headers.get('Content-Length', '0'))
        if not 0 <= size <= MAX_DOCUMENT:
            return handler.response(413, {'error': '文档请求超过上限'})
        body = handler.rfile.read(size) if size else b''
        handler._post_body_handled = True
        headers = {key.lower(): value for key, value in handler.headers.items() if key.lower() in ('content-type', 'content-length', 'accept', 'accept-encoding', 'range', 'if-range', 'if-none-match', 'if-modified-since', 'upgrade', 'sec-websocket-key', 'sec-websocket-version', 'sec-websocket-protocol', 'sec-websocket-extensions')}
        public = urlsplit(self.office.config['public_url'])
        headers.update({'Host': '127.0.0.1', 'Connection': 'Upgrade' if headers.get('upgrade') else 'close', 'X-Forwarded-Host': public.netloc + public.path.rstrip('/') + PREFIX, 'X-Forwarded-Proto': public.scheme})
        html = handler.command != 'HEAD' and unquote(urlsplit(handler.path).path).endswith('.html')
        if html:
            headers['accept-encoding'] = 'identity'
        parsed = urlsplit(handler.path)
        upstream = parsed.path[len(PREFIX):] + ('?' + parsed.query if parsed.query else '')
        connection = self.connection(settings)
        try:
            connection.sendall((handler.command + ' ' + upstream + ' HTTP/1.1\r\n' + ''.join((k + ': ' + v + '\r\n' for k, v in headers.items())) + '\r\n').encode('iso-8859-1') + body)
            response = b''
            while b'\r\n\r\n' not in response:
                block = connection.recv(65536)
                if not block:
                    raise OSError('engine closed before headers')
                response += block
                if len(response) > 256 * 1024:
                    raise ValueError('文档响应头过长')
            head, data = response.split(b'\r\n\r\n', 1)
            lines = head.decode('iso-8859-1').split('\r\n')
            status = int(lines[0].split()[1])
            fields = [row.split(':', 1) for row in lines[1:]]
            html = html and status == 200 and any((k.lower() == 'content-type' and 'text/html' in v.lower() for k, v in fields))
            if html:
                if any((k.lower() in ('transfer-encoding', 'content-encoding') for k, v in fields)):
                    raise ValueError('文档界面编码不受支持')
                while True:
                    block = connection.recv(65536)
                    if not block:
                        break
                    data += block
                    if len(data) > 2 * 1024 * 1024:
                        raise ValueError('文档界面响应过长')
                shim = (PREFIX + '/web-apps/apps/api/documents/crb-worker.js').encode()
                if b'<head>' not in data:
                    raise ValueError('文档界面版本不受支持')
                data = data.replace(b'<head>', b'<head><script src="' + shim + b'"></script>', 1)
            handler._engine_headers_sent = True
            handler.send_response(status)
            for key, value in fields:
                if key.lower() in ('connection', 'set-cookie', 'access-control-allow-origin', 'access-control-allow-credentials', 'content-security-policy', 'x-frame-options', 'cross-origin-resource-policy'):
                    continue
                if html and key.lower() in ('content-length', 'transfer-encoding', 'content-encoding', 'etag'):
                    continue
                if key.lower() == 'location':
                    location = urlsplit(value.strip())
                    if location.netloc and location.netloc not in ('127.0.0.1', 'localhost', public.netloc):
                        continue
                    if value.strip().startswith('/') and (not value.strip().startswith(PREFIX + '/')):
                        value = PREFIX + value.strip()
                handler.send_header(key, value.strip())
            if html:
                handler.send_header('Content-Length', str(len(data)))
                handler.send_header('ETag', '"crb-' + hashlib.sha256(data).hexdigest()[:24] + '"')
            self.cors(handler)
            handler.send_header('Connection', 'Upgrade' if status == 101 else 'close')
            handler.end_headers()
            handler.count_public_traffic(len(data) if handler.command != 'HEAD' else 0, count_request=True)
            if handler.command != 'HEAD':
                handler.wfile.write(data)
                handler.wfile.flush()
            if status == 101:
                connection.settimeout(None)
                while True:
                    readable, _, _ = select.select((connection, handler.connection), (), (), 60)
                    for source in readable:
                        block = source.recv(65536)
                        if not block:
                            return
                        (handler.connection if source is connection else connection).sendall(block)
                        if handler.public_edge_request():
                            handler.count_public_traffic(len(block)) if source is connection else handler.count_public_upload(len(block))
            elif not html and handler.command != 'HEAD':
                while True:
                    block = connection.recv(65536)
                    if not block:
                        break
                    handler.wfile.write(block)
                    handler.count_public_traffic(len(block))
        except (OSError, ValueError):
            if not getattr(handler, '_engine_headers_sent', False):
                handler.response(503, {'error': '文档引擎暂不可用，请检查服务端组件状态'})
        finally:
            connection.close()
            handler.close_connection = True

    @staticmethod
    def cors(handler):
        handler.send_header('Access-Control-Allow-Origin', 'null')
        handler.send_header('Access-Control-Allow-Credentials', 'true')
        handler.send_header('Access-Control-Allow-Headers', 'Content-Type,Authorization,Range,Cache-Control,X-Requested-With')
        handler.send_header('Access-Control-Allow-Methods', 'GET,HEAD,POST,OPTIONS')
        handler.send_header('Cross-Origin-Resource-Policy', 'cross-origin')
        handler.send_header('Content-Security-Policy', 'sandbox allow-scripts allow-forms allow-popups allow-downloads')
        handler.send_header('X-Content-Type-Options', 'nosniff')
        handler.send_header('Referrer-Policy', 'no-referrer')
