"""Same-user original-App RPC for reviewed post-IPC Desktop builds.

Uses an already enabled loopback debugger. It never enables debugging, starts
another App/server, changes authentication, or retries a submitted operation.
"""
import json
from pathlib import Path
import queue
import threading
import urllib.request
from urllib.parse import urlsplit
import uuid
from crb.codex.question_ui import discover_debugger, verify_debugger
PROGRAM = Path(__file__).with_suffix('.js').read_text(encoding='utf-8')

def legacy_endpoint_absent(error, platform):
    """Only a missing listener permits switching transports, never a timeout.

    WaitNamedPipeW reports Win32 errors through OSError.errno in our wrapper;
    native Python file errors may instead populate winerror.
    """
    if platform == 'win32':
        code = getattr(error, 'winerror', None)
        if code is None:
            code = getattr(error, 'errno', None)
        return code in (2, 3)
    return platform in ('linux', 'darwin') and isinstance(error, (FileNotFoundError, ConnectionRefusedError))

class NativeDesktopRPC:
    native_rpc = True
    supports_side_workbench = True

    def __init__(self, home, on_event):
        self.home = str(home)
        self.on_event = on_event
        self.key = '__crb_rpc_' + uuid.uuid4().hex
        self.client_id = None
        self.socket = None
        self.endpoint = None
        self.pending = {}
        self.sequence = 0
        self.writer = threading.Lock()
        self.lock = threading.Lock()
        self.delivery_lock = threading.RLock()
        self.stopped = threading.Event()
        self.events = queue.Queue(maxsize=128)
        self.event_bytes = 0
        self.followed = set()
        self.follow_order = []
        self.follow_lock = threading.RLock()
        self.sock = self

    @property
    def connected(self):
        return self.client_id is not None and (not self.stopped.is_set())

    def connect(self):
        import websocket
        self.endpoint = endpoint = discover_debugger()
        if not endpoint:
            raise ConnectionError('新版原 App 的本机联动通道不可用；请检查客户端连接状态')
        port = endpoint['port']
        addresses = endpoint.get('addresses', ['127.0.0.1'])
        if not addresses or any((a not in ('127.0.0.1', '::1') for a in addresses)):
            raise ConnectionError('Non-local native debugger rejected')
        address = '127.0.0.1' if '127.0.0.1' in addresses else '::1'
        authority = f'[{address}]' if address == '::1' else address
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://{authority}:{port}/json/list', timeout=3) as response:
            body = response.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            raise ConnectionError('Native target list too large')
        targets = sorted((t for t in json.loads(body) if t.get('type') == 'page' and t.get('url') == 'app://-/index.html'), key=lambda t: t['id'])
        if not targets or not verify_debugger(endpoint):
            raise ConnectionError('Original App surface unavailable')
        url = targets[0].get('webSocketDebuggerUrl', '')
        parsed = urlsplit(url)
        if parsed.scheme != 'ws' or parsed.hostname not in addresses or parsed.port != port or parsed.username or parsed.password or parsed.query or parsed.fragment or (not parsed.path.startswith('/devtools/page/')):
            raise ConnectionError('Non-local native debugger rejected')
        self.socket = websocket.create_connection(url, timeout=2, suppress_origin=True, http_proxy_host=None, http_no_proxy=['127.0.0.1', 'localhost', '::1'])
        threading.Thread(target=self._read, daemon=True, name='native-rpc-reader').start()
        threading.Thread(target=self._events, daemon=True, name='native-rpc-events').start()
        try:
            self._cdp('Runtime.addBinding', {'name': self.key + '_event'})
            result = self._call('install', timeout=15)
            if result != {'ok': True, 'protocol': 1}:
                raise ConnectionError('Invalid native RPC handshake')
            self.client_id = self.key
            threading.Thread(target=self._heartbeat, daemon=True, name='native-rpc-lease').start()
        except Exception:
            self.close()
            raise

    def _cdp(self, method, params, timeout=12):
        ready, box = (threading.Event(), {})
        with self.lock:
            if self.stopped.is_set():
                raise ConnectionError('原 App 连接已断开；操作未自动重发')
            self.sequence += 1
            rid = self.sequence
            self.pending[rid] = (ready, box)
        try:
            with self.writer:
                self.socket.send(json.dumps({'id': rid, 'method': method, 'params': params}))
            if not ready.wait(timeout):
                raise TimeoutError('未收到原 App 确认；不会自动重发，请先核对原对话')
            message = box.get('message')
            if message is None:
                raise ConnectionError('原 App 连接中断；操作结果未确认，不会自动重发')
            if message.get('error'):
                raise RuntimeError('原 App 拒绝本机 RPC 请求')
            return message.get('result', {})
        finally:
            with self.lock:
                self.pending.pop(rid, None)

    def _call(self, operation, thread_id=None, arguments=None, timeout=12):
        request = {'key': self.key, 'operation': operation, 'home': self.home}
        if thread_id is not None:
            request['threadId'] = thread_id
        if arguments is not None:
            request['arguments'] = arguments
        expression = PROGRAM + '(' + json.dumps(request, ensure_ascii=True) + ')'
        result = self._cdp('Runtime.evaluate', {'expression': expression, 'awaitPromise': True, 'returnByValue': True}, timeout)
        if result.get('exceptionDetails'):
            if operation not in {'install', 'ping', 'follow', 'unfollow', 'owner', 'history', 'close'}:
                raise ConnectionError('原 App 操作结果未确认；请核对原对话，不会自动重发')
            raise RuntimeError('原 App 本机操作未完成；请检查原对话，未自动重试')
        value = result.get('result', {}).get('value')
        if not isinstance(value, dict) or value.get('ok') is not True:
            raise RuntimeError('Invalid native RPC result')
        return value

    def _read(self):
        import websocket
        try:
            while not self.stopped.is_set():
                try:
                    raw = self.socket.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if not raw or len(raw) > 24 * 1024 * 1024:
                    break
                message = json.loads(raw)
                with self.lock:
                    slot = self.pending.get(message.get('id'))
                    if slot:
                        slot[1]['message'] = message
                        slot[0].set()
                if message.get('method') == 'Runtime.bindingCalled' and message.get('params', {}).get('name') == self.key + '_event':
                    payload = message['params']['payload']
                    size = len(payload.encode('utf-8'))
                    with self.lock:
                        if self.event_bytes + size > 16 * 1024 * 1024:
                            raise ValueError('Native event buffer limit')
                        self.event_bytes += size
                    self.events.put_nowait((size, json.loads(payload)))
        except (ValueError, OSError, websocket.WebSocketException, queue.Full):
            pass
        finally:
            self._disconnect()

    def _events(self):
        while not self.stopped.is_set():
            try:
                size, value = self.events.get(timeout=0.5)
            except queue.Empty:
                continue
            with self.lock:
                self.event_bytes -= size
            try:
                with self.delivery_lock:
                    if not self.stopped.is_set():
                        self._event(value)
            except Exception:
                self._disconnect()

    def _event(self, value):
        if not isinstance(value, dict) or value.get('threadId') not in self.followed:
            return
        tid = value['threadId']
        if value.get('type') == 'snapshot':
            state = value.get('state')
            if not isinstance(state, dict) or state.get('id') != tid or state.get('hostId') != 'local' or (type(value.get('attached')) is not bool) or (type(value.get('revision')) is not int):
                raise ValueError('Invalid native conversation snapshot')
            if not value['attached']:
                self.on_event({'type': 'native-rpc-unloaded', 'conversationId': tid})
                return
            self.on_event({'type': 'native-rpc-snapshot', 'conversationId': tid, 'state': state, 'revision': value['revision'], 'owner': self.key if value['attached'] else None})
        elif value.get('type') == 'queue':
            messages = value.get('messages')
            if not isinstance(messages, list) or not all((isinstance(m, dict) for m in messages)):
                raise ValueError('Invalid native queue snapshot')
            self.on_event({'type': 'broadcast', 'method': 'thread-queued-followups-changed', 'version': 3, 'params': {'hostId': 'local', 'conversationId': tid, 'messages': messages}})
        elif value.get('type') in {'invalidated', 'unloaded'}:
            self.on_event({'type': 'native-rpc-unloaded', 'conversationId': tid})

    def follow(self, tid, following=True):
        uuid.UUID(tid)
        with self.follow_lock:
            if following:
                if tid not in self.followed and len(self.followed) >= 40:
                    self.follow(self.follow_order[0], following=False)
                self.followed.add(tid)
                if tid in self.follow_order:
                    self.follow_order.remove(tid)
                self.follow_order.append(tid)
                try:
                    return self._call('follow', tid)
                except Exception:
                    self.followed.discard(tid)
                    self.follow_order.remove(tid)
                    raise
            self.followed.discard(tid)
            if tid in self.follow_order:
                self.follow_order.remove(tid)
            result = self._call('unfollow', tid)
            self.on_event({'type': 'native-rpc-unloaded', 'conversationId': tid})
            return result

    def request(self, method, params, version=1, target=None, timeout=15):
        tid = params.get('conversationId')
        uuid.UUID(tid)
        if params.get('hostId', 'local') != 'local' or target not in (None, self.key):
            raise RuntimeError('Native receiver changed; operation not submitted')
        if method == 'thread-owner-discovery':
            if not self._call('owner', tid).get('attached'):
                raise RuntimeError('no-client-found')
            return {'handledByClientId': self.key, 'resultType': 'success'}
        operations = {'thread-follower-start-turn': ('start', params.get('turnStart')), 'thread-follower-steer-turn': ('steer', params), 'thread-follower-interrupt-turn': ('interrupt', params), 'thread-follower-edit-last-user-turn': ('edit', params), 'thread-follower-update-thread-settings': ('settings', params), 'thread-follower-load-complete-history': ('history', params), 'thread-follower-command-approval-decision': ('command-approval', params), 'thread-follower-file-approval-decision': ('file-approval', params), 'thread-follower-permissions-request-approval-response': ('permissions-approval', params), 'thread-follower-submit-user-input': ('user-input', params), 'thread-follower-submit-mcp-server-elicitation-response': ('elicitation', params), 'thread-follower-remove-queued-message': ('queue-remove-receipt', params)}
        if method not in operations:
            raise RuntimeError('新版原 App 的此项操作尚未完成兼容；未提交，请暂时在原 App 操作')
        operation, arguments = operations[method]
        result = self._call(operation, tid, arguments, timeout)
        return {'method': method, 'resultType': 'success', 'handledByClientId': self.key, 'result': result}

    def create_side_chat(self, tid):
        uuid.UUID(tid)
        return self._call('side-create', tid, timeout=30)

    def update_queue(self, tid, before, after, discarded_ids):
        return {'result': self._call('queue', tid, {'before': before, 'after': after, 'discardedIds': discarded_ids}, timeout=30)}

    def resume_queue(self, tid, messages):
        self._call('queue-resume', tid, {'before': messages}, timeout=40)
        return {'status': 'queue-resumed'}

    def send_queue_now(self, tid, messages, message_id):
        return self._call('queue-send-now', tid, {'before': messages, 'messageId': message_id}, timeout=45)

    def broadcast(self, method, params, version=1, target_client_ids=None):
        if method == 'thread-stream-following-changed':
            return self.follow(params['conversationId'], params.get('following') is True)
        if method in {'thread-archived', 'thread-unarchived'}:
            return
        raise RuntimeError('Unsupported native notification')

    def _heartbeat(self):
        while not self.stopped.wait(10):
            try:
                if not verify_debugger(self.endpoint):
                    raise ConnectionError('Native App identity changed')
                self._call('ping', timeout=5)
            except Exception:
                self.close()

    def _disconnect(self):
        with self.lock:
            if self.stopped.is_set():
                return
            self.stopped.set()
            self.client_id = None
            for ready, _ in self.pending.values():
                ready.set()
        try:
            self.socket.close()
        except Exception:
            pass
        with self.delivery_lock:
            self.on_event({'type': 'disconnected'})

    def close(self):
        if not self.stopped.is_set() and self.socket:
            try:
                self._call('close', timeout=3)
            except Exception:
                pass
            try:
                self._cdp('Runtime.removeBinding', {'name': self.key + '_event'}, timeout=3)
            except Exception:
                pass
        self._disconnect()
