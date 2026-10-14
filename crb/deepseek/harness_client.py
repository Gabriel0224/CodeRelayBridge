"""Same-user client for the existing DeepSeek Harness Desktop Host.

No model API keys, new engine, application patch, credential writes or public
listener. Use only the browser-session grant from the user's private Harness
home, and the Host's unchanged authority-bound cookie and RPC/stream protocol.
"""
import base64
import hashlib
import hmac
import json
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import yaml

class HarnessError(RuntimeError):

    def __init__(self, message, code='', unknown=False):
        super().__init__(message)
        self.code = code
        self.unknown = unknown

def b64url(value):
    return base64.urlsafe_b64encode(value).decode('ascii').rstrip('=')

class StrictLoader(yaml.SafeLoader):
    pass

def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError('Duplicate credential field')
        result[key] = loader.construct_object(value_node, deep=deep)
    return result
StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)

class HarnessClient:

    def __init__(self, home=None, base='http://127.0.0.1:19387', opener=None):
        self.home = Path(home or Path.home() / '.dsh').expanduser().resolve()
        url = urllib.parse.urlsplit(base)
        if url.scheme != 'http' or url.hostname != '127.0.0.1' or (not url.port) or url.username or url.password or (url.path not in ('', '/')) or url.query or url.fragment:
            raise ValueError('Harness 必须连接本机 127.0.0.1 的明确端口')
        self.base = f'http://127.0.0.1:{url.port}'
        self.authority = f'127.0.0.1:{url.port}'
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.closed = threading.Event()

    def _headers(self):
        try:
            path = self.home / '.credentials.yaml'
            if path.stat().st_size > 1024 * 1024:
                raise ValueError('Oversized credentials')
            document = yaml.load(path.read_text(encoding='utf-8'), Loader=StrictLoader)
            if not isinstance(document, dict) or document.get('version') != 1:
                raise ValueError('Unsupported document')
            record = document['records']['client-connection/browser-session']
            payload = record['payload']
            secret = payload['secret']
            if record['kind'] != 'grant' or payload['version'] != 1 or (not isinstance(secret, str)) or (not re.fullmatch('[A-Za-z0-9_-]{43}', secret)):
                raise ValueError('Unsupported browser grant')
            secret_bytes = base64.urlsafe_b64decode(secret + '=')
            if len(secret_bytes) != 32 or b64url(secret_bytes) != secret:
                raise ValueError('Invalid browser grant')
        except Exception:
            raise HarnessError('无法读取当前用户的 Harness 本地会话授权', 'harness/local-grant') from None
        issued = int(time.time() * 1000)
        body = b64url(json.dumps({'version': 1, 'authority': self.authority, 'issuedAt': issued, 'expiresAt': issued + 300000}, separators=(',', ':')).encode())
        signed = b64url(hmac.new(secret_bytes, body.encode(), hashlib.sha256).digest())
        name = 'dsh-auth-' + b64url(hashlib.sha256(self.authority.encode()).digest())
        return {'Cookie': f'{name}=v1.{body}.{signed}', 'Origin': self.base, 'Content-Type': 'application/json', 'Accept': 'application/json'}

    def rpc(self, endpoint, args=None, timeout=12, rpc_id=None):
        if endpoint != '$events/result' and (not re.fullmatch('[a-zA-Z][\\w-]*/[a-zA-Z][\\w-]*', endpoint)):
            raise ValueError('Invalid Harness endpoint')
        identity = rpc_id or str(uuid.uuid4())
        data = json.dumps({'type': 'client-request', 'rpcId': identity, 'method': endpoint, 'payload': {'args': args or {}}}, ensure_ascii=False).encode()
        request = urllib.request.Request(self.base + '/api/' + endpoint, data=data, headers=self._headers())
        try:
            with self.opener.open(request, timeout=timeout) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise HarnessError('Harness 响应超过安全读取上限', 'harness/response-limit')
            envelope = json.loads(raw)
        except urllib.error.HTTPError as error:
            raise HarnessError(f'Harness 本地服务返回 HTTP {error.code}', 'harness/http') from None
        except (TimeoutError, OSError, urllib.error.URLError):
            raise HarnessError('Harness 本地服务未确认请求；不会自动重发', 'harness/connection', True) from None
        except (ValueError, TypeError):
            raise HarnessError('Harness 本地服务返回无效响应', 'harness/protocol', True) from None
        if not isinstance(envelope, dict) or envelope.get('type') != 'server-response' or envelope.get('rpcId') != identity or (not isinstance(envelope.get('result'), dict)):
            raise HarnessError('Harness 请求确认不匹配', 'harness/protocol', True)
        result = envelope['result']
        if result.get('ok') is not True:
            error = result.get('error') or {}
            if not isinstance(error, dict):
                error = {'message': str(error)}
            raise HarnessError(str(error.get('message') or 'Harness 拒绝请求'), str(error.get('code') or 'harness/rejected'))
        return result.get('value')

    def stream(self, endpoint, args=None, stop=None):
        import websocket
        headers = self._headers()
        socket = websocket.create_connection(self.base.replace('http:', 'ws:', 1) + '/api/remote.mux', timeout=5, cookie=headers['Cookie'], origin=self.base, http_no_proxy=['127.0.0.1'])
        identity = str(uuid.uuid4())
        try:
            socket.send(json.dumps({'type': 'open', 'streamId': identity, 'endpoint': endpoint, 'payload': {'args': args or {}}}))
            socket.settimeout(1)
            while not self.closed.is_set() and (not (stop and stop.is_set())):
                try:
                    raw = socket.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if not raw:
                    raise HarnessError('Harness 事件连接已断开', 'harness/stream')
                if len(raw) > 16 * 1024 * 1024:
                    raise HarnessError('Harness 事件超过安全上限', 'harness/stream-limit')
                frame = json.loads(raw)
                if frame.get('streamId') != identity:
                    continue
                if frame.get('type') == 'item':
                    yield frame['value']
                elif frame.get('type') == 'end':
                    return
                elif frame.get('type') == 'error':
                    error = frame.get('error') or {}
                    raise HarnessError(str(error.get('message') or 'Harness 事件订阅失败'), str(error.get('code') or 'harness/stream'))
        finally:
            socket.close()

    def close(self):
        self.closed.set()
