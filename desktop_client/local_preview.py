"""On-demand outbound tunnel to explicitly selected loopback web services."""
import json
import re
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit, urlunsplit, quote
from crb.common.ws_stream import ClientWebSocket
from crb.server.office_wire import Wire

def endpoint(value):
    if not isinstance(value, dict) or set(value) - {'scheme', 'host', 'port'}:
        raise ValueError('本地网页目标无效')
    scheme, host, port = (value.get('scheme'), value.get('host'), value.get('port'))
    if scheme not in ('http', 'https') or host not in ('127.0.0.1', 'localhost', '::1') or type(port) is not int or (not 1 <= port <= 65535):
        raise ValueError('只允许目标电脑的本机网页地址')
    return (scheme, host, port)

def disconnect(connection):
    transport = getattr(connection, 'sock', None)
    if transport:
        try:
            transport.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    try:
        connection.close(timeout=0.5)
    except TypeError:
        connection.close()
    except Exception:
        pass

class LocalPreviewClient:

    def __init__(self, config):
        self.config = config
        self.guard = threading.RLock()
        self.closed = threading.Event()
        self.key = None
        self.thread = None
        self.connection = None

    def connect(self, request):
        if not isinstance(request, dict) or set(request) != {'channel'} or (not re.fullmatch('[0-9a-f]{48}', request.get('channel', ''))):
            raise ValueError('本地网页通道参数无效')
        key = request['channel']
        with self.guard:
            if self.closed.is_set():
                raise ValueError('客户端正在关闭')
            if key == self.key and self.thread and self.thread.is_alive():
                return {'ok': True, 'accepted': True}
            self.key = key
            if self.connection:
                disconnect(self.connection)
            self.thread = threading.Thread(target=self.run, args=(key,), daemon=True, name='local-web-preview')
            self.thread.start()
        return {'ok': True, 'accepted': True}

    def incoming(self, role):
        try:
            data = json.loads(role)
        except (TypeError, ValueError):
            raise ValueError('本地网页目标无效') from None
        scheme, host, port = endpoint(data)
        addresses = ['::1'] if host == '::1' else ['127.0.0.1', '::1'] if host == 'localhost' else ['127.0.0.1']
        last = None
        for address in addresses:
            try:
                connection = socket.create_connection((address, port), timeout=8)
                break
            except OSError as error:
                last = error
        else:
            raise OSError('目标网页尚未启动') from last
        if scheme == 'https':
            try:
                connection = ssl.create_default_context().wrap_socket(connection, server_hostname=host)
            except BaseException:
                connection.close()
                raise
        connection.settimeout(None)
        return connection

    def run(self, key):
        import websocket
        from crb.common.ws_stream import ClientWebSocket
        base = urlsplit(self.config['relay_url'].rstrip('/'))
        target = urlunsplit(('wss' if base.scheme == 'https' else 'ws', base.netloc, base.path + '/agent/local-preview', 'channel=' + quote(key), ''))
        until = time.monotonic() + 120
        delay = 1
        while not self.closed.is_set() and key == self.key:
            connection = None
            wire = None
            stream = None
            try:
                connection = websocket.create_connection(target, header=['Authorization: Bearer ' + self.config['agent_token']], timeout=15, origin=urlunsplit((base.scheme, base.netloc, '', '', '')), enable_multithread=True)
                connection.settimeout(None)
                stream = ClientWebSocket(connection)
                with self.guard:
                    if key != self.key or self.closed.is_set():
                        disconnect(connection)
                        return
                    self.connection = connection
                wire = Wire(stream, stream, self.incoming, first=2)
                healthy = wire.ready.wait(10) and (not wire.closed.is_set())
                while not self.closed.wait(0.2) and (not wire.closed.is_set()) and (key == self.key):
                    pass
                if stream.normal:
                    return
                if healthy:
                    until = time.monotonic() + 120
                    delay = 1
            except websocket.WebSocketBadStatusException as error:
                if error.status_code in (401, 403, 404, 410):
                    return
            except (OSError, ValueError, websocket.WebSocketException):
                pass
            finally:
                if wire:
                    wire.close()
                if connection:
                    disconnect(connection)
                with self.guard:
                    if self.connection is connection:
                        self.connection = None
            if time.monotonic() > until:
                return
            self.closed.wait(delay)
            delay = min(delay * 2, 10)

    def close(self):
        self.closed.set()
        with self.guard:
            if self.connection:
                disconnect(self.connection)
