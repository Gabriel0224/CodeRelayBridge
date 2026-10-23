"""Same-user, loopback-only question cleanup and explicit native queue resume.

Only acknowledge native IDs after the original IPC has accepted the answer.
Discover an explicitly enabled App debugger; never restart or enable it here.
Failures must not turn an accepted answer into an error or cause a resubmission.
"""
import json
from pathlib import Path
import plistlib
import sys
import threading
import time
import urllib.request
from urllib.parse import urlsplit
UI_PROGRAM = Path(__file__).with_suffix('.js').read_text(encoding='utf-8')

def debugger_port(args):
    ports = []
    for index, arg in enumerate(args):
        if arg.startswith('--remote-debugging-port='):
            ports.append(arg.split('=', 1)[1])
        elif arg == '--remote-debugging-port' and index + 1 < len(args):
            ports.append(args[index + 1])
    if len(ports) != 1 or not ports[0].isdigit() or (not 1 <= int(ports[0]) <= 65535):
        return None
    return int(ports[0])

def codex_image(path, platform=None):
    value = str(path).replace('\\', '/')
    platform = platform or sys.platform
    if platform == 'win32':
        return '/windowsapps/openai.codex_' in value.lower() and value.lower().endswith('/app/chatgpt.exe')
    if platform == 'darwin':
        if not value.endswith(('/Codex.app/Contents/MacOS/Codex', '/ChatGPT.app/Contents/MacOS/ChatGPT', '/Codex.app/Contents/MacOS/ChatGPT')):
            return False
        try:
            return plistlib.loads((Path(path).parents[1] / 'Info.plist').read_bytes()).get('CFBundleIdentifier') == 'com.openai.codex'
        except (OSError, ValueError, plistlib.InvalidFileException):
            return False
    return value == '/usr/lib/chatgpt/ChatGPT' or value.endswith(('/codex-desktop/codex-desktop', '/codex-desktop/ChatGPT', '/codex/ChatGPT'))

def discover_debugger():
    import psutil
    owner = psutil.Process().username()
    endpoints = []
    for proc in psutil.process_iter(['pid', 'exe']):
        try:
            info = proc.info
            if not codex_image(info.get('exe') or ''):
                continue
            args = proc.cmdline()
            if not codex_image(info.get('exe') or '') or any((arg.startswith('--type=') for arg in args)):
                continue
            username = proc.username()
            same_user = username == owner
            if sys.platform == 'win32':
                same_user = str(username).casefold() == owner.casefold()
            port = debugger_port(args)
            if not same_user or not port:
                continue
            listeners = [c for c in proc.net_connections(kind='tcp') if c.status == psutil.CONN_LISTEN and c.laddr.port == port]
            if not listeners or any((c.laddr.ip not in {'127.0.0.1', '::1'} for c in listeners)):
                continue
            endpoints.append({'pid': proc.pid, 'created': proc.create_time(), 'exe': info['exe'], 'port': port, 'owner': owner})
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    if len(endpoints) != 1:
        return None
    return endpoints[0]

def verify_debugger(endpoint):
    import psutil
    try:
        proc = psutil.Process(endpoint['pid'])
        compare = (lambda v: str(v).casefold()) if sys.platform == 'win32' else str
        if proc.create_time() != endpoint['created'] or compare(proc.exe()) != compare(endpoint['exe']) or compare(proc.username()) != compare(endpoint['owner']):
            return False
        if debugger_port(proc.cmdline()) != endpoint['port']:
            return False
        listeners = [c for c in proc.net_connections(kind='tcp') if c.status == psutil.CONN_LISTEN and c.laddr.port == endpoint['port']]
        return bool(listeners) and all((c.laddr.ip in {'127.0.0.1', '::1'} for c in listeners))
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, KeyError):
        return False

def validate_ack(thread_id, turn_id, replies):
    if not isinstance(thread_id, str) or not thread_id or len(thread_id) > 200 or (not isinstance(turn_id, str)) or (not turn_id) or (len(turn_id) > 200):
        raise ValueError('Invalid native question scope')
    if not isinstance(replies, list) or not 1 <= len(replies) <= 8:
        raise ValueError('Invalid native question answers')
    seen = set()
    for reply in replies:
        if not isinstance(reply, dict):
            raise ValueError('Invalid native answer')
        item_id = reply.get('questionItemId')
        identity = json.loads(item_id)
        answer = reply.get('answer')
        if not isinstance(identity, list) or len(identity) != 3 or identity[0] != 'request_user_input_async' or (not isinstance(identity[1], str)) or (not identity[1]) or (not isinstance(identity[2], int)) or isinstance(identity[2], bool) or (identity[2] < 0) or (not isinstance(answer, str)) or (not answer.strip()) or (len(answer) > 4000) or (item_id in seen):
            raise ValueError('Invalid native question identity')
        seen.add(item_id)
    return {'hostId': 'local', 'threadId': thread_id, 'turnId': turn_id, 'platform': sys.platform, 'replies': [{'questionItemId': r['questionItemId'], 'answer': r['answer'].strip()} for r in replies]}

class CodexQuestionUI:

    def __init__(self):
        self.lock = threading.Lock()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.retry_after = 0
        self.pending = {}
        self.pending_lock = threading.Lock()
        self.pending_busy = False
        self.pending_dirty = set()

    @staticmethod
    def pending_key(request):
        return (request['threadId'], request['turnId'], tuple(sorted((reply['questionItemId'] for reply in request['replies']))))

    def capacity_retry_state(self, thread_id, turn_id):
        if any((not isinstance(value, str) or not value or len(value) > 200 for value in (thread_id, turn_id))):
            raise ValueError('Invalid native retry scope')
        with self.lock:
            if time.monotonic() < self.retry_after:
                return {'status': 'unavailable'}
            try:
                endpoint = discover_debugger()
                if not endpoint or not verify_debugger(endpoint):
                    self.retry_after = time.monotonic() + 20
                    return {'status': 'unavailable'}
                return self._call(endpoint, {'operation': 'capacity-retry-state', 'hostId': 'local', 'threadId': thread_id, 'turnId': turn_id})
            except Exception:
                return {'status': 'unavailable'}

    def resume_queue(self, thread_id, message_ids):
        """Explicit user action, using the original coordinator, never a resend."""
        if not isinstance(thread_id, str) or not 0 < len(thread_id) <= 200 or (not isinstance(message_ids, list)) or (not 0 < len(message_ids) <= 1000) or any((not isinstance(value, str) or not 0 < len(value) <= 200 for value in message_ids)) or (len(set(message_ids)) != len(message_ids)):
            raise ValueError('队列标识无效')
        with self.lock:
            endpoint = discover_debugger()
            if not endpoint or not verify_debugger(endpoint):
                raise RuntimeError('原 App 尚未启用本机联动通道；请从 Relay 客户端启动 Codex 后重试')
            try:
                result = self._call(endpoint, {'operation': 'resume-interrupted-queue', 'platform': sys.platform, 'hostId': 'local', 'threadId': thread_id, 'messageIds': message_ids})
                if result.get('status') not in {'queue-resumed', 'queue-not-paused', 'queue-resume-pending'}:
                    raise RuntimeError('Unexpected native queue result')
                return result
            except Exception as exc:
                raise RuntimeError('未能确认原 App 的队列恢复状态，请刷新核对；不会自动重试') from exc

    def control(self, thread_id, turn_id, entity_key, operation, question_ids, current_id):
        """Explicit presentation action. Never submits an answer or starts a turn."""
        if operation not in ('close', 'skip', 'reopen') or any((not isinstance(value, str) or not 0 < len(value) <= 200 for value in (thread_id, turn_id, entity_key))) or (not isinstance(question_ids, list)) or (not 1 <= len(question_ids) <= 8) or any((not isinstance(value, str) or len(value) > 500 for value in question_ids)) or (len(set(question_ids)) != len(question_ids)) or (current_id not in question_ids):
            raise ValueError('问题标识无效')
        validate_ack(thread_id, turn_id, [{'questionItemId': value, 'answer': 'validate-only'} for value in question_ids])
        with self.lock:
            endpoint = discover_debugger()
            if not endpoint or not verify_debugger(endpoint):
                return {'status': 'unavailable', 'detail': '问题操作仅在面板生效；原 App 联动通道不可用，未提交答案。'}
            try:
                return self._call(endpoint, {'operation': 'question-control', 'platform': sys.platform, 'hostId': 'local', 'threadId': thread_id, 'turnId': turn_id, 'entityKey': entity_key, 'action': operation, 'questionIds': question_ids, 'questionId': current_id})
            except Exception:
                return {'status': 'unavailable', 'detail': '问题操作仅在面板生效；未确认原 App 的状态，未提交答案。'}

    def acknowledge(self, thread_id, turn_id, replies):
        request = validate_ack(thread_id, turn_id, replies)
        with self.lock:
            if time.monotonic() < self.retry_after:
                return {'status': 'unavailable', 'answerDelivered': True}
            try:
                endpoint = discover_debugger()
                if not endpoint or not verify_debugger(endpoint):
                    self.retry_after = time.monotonic() + 20
                    return {'status': 'unavailable', 'answerDelivered': True, 'detail': '答案已提交；原 App 尚未启用仅本机的提问窗口联动通道。'}
                result = self._call(endpoint, request)
                key = self.pending_key(request)
                with self.pending_lock:
                    if result.get('status') == 'awaiting-native-acceptance':
                        deadline = self.pending.get(key, (None, time.monotonic() + 600))[1]
                        self.pending[key] = (request, deadline)
                        while len(self.pending) > 16:
                            self.pending.pop(next(iter(self.pending)))
                    else:
                        current = self.pending.get(key)
                        if current and current[0] == request:
                            self.pending.pop(key, None)
                return result
            except Exception:
                self.retry_after = time.monotonic() + 20
                return {'status': 'unavailable', 'answerDelivered': True, 'detail': '答案已提交；原 App 窗口联动未确认，不会重发答案。'}

    def defer_ack(self, thread_id, turn_id, replies):
        """UI cleanup is asynchronous, never part of the send/receipt deadline."""
        request = validate_ack(thread_id, turn_id, replies)
        key = self.pending_key(request)
        with self.pending_lock:
            previous = self.pending.get(key)
            self.pending[key] = (request, previous[1] if previous else time.monotonic() + 600)
            while len(self.pending) > 16:
                self.pending.pop(next(iter(self.pending)))
        self.notify_native_change(thread_id)
        return {'status': 'pending-cleanup', 'answerDelivered': True}

    def notify_native_change(self, thread_id):
        """Finish delayed steering admission on existing native events only.

        No extra polling, network subscriptions, re-submission, or independent
        service. This short-lived thread dies with the existing client worker.
        Never block the native IPC reader while acknowledging the UI.
        """
        with self.pending_lock:
            now = time.monotonic()
            self.pending = {key: value for key, value in self.pending.items() if value[1] > now}
            if not any((key[0] == thread_id for key in self.pending)):
                return
            self.pending_dirty.add(thread_id)
            if self.pending_busy:
                return
            self.pending_busy = True

        def finish_pending():
            try:
                while True:
                    with self.pending_lock:
                        if not self.pending_dirty:
                            self.pending_busy = False
                            return
                        tid = self.pending_dirty.pop()
                        pending = [(key, value[0]) for key, value in self.pending.items() if key[0] == tid and value[1] > time.monotonic()]
                    for key, request in pending:
                        tid, turn = key[:2]
                        result = self.acknowledge(tid, turn, request['replies'])
            except Exception:
                with self.pending_lock:
                    self.pending_busy = False
        threading.Thread(target=finish_pending, name='codex-question-ui-ack', daemon=True).start()

    def _call(self, endpoint, request, *, program=None, statuses=None):
        import websocket
        port = endpoint['port']
        base = f'http://127.0.0.1:{port}'
        with self.opener.open(base + '/json/list', timeout=2) as response:
            payload = response.read(1024 * 1024 + 1)
        if len(payload) > 1024 * 1024:
            raise ValueError('Debugger response too large')
        targets = json.loads(payload)
        targets = [t for t in targets if t.get('type') == 'page' and urlsplit(t.get('url', '')).scheme == 'app' and (urlsplit(t.get('url', '')).netloc == '-') and (urlsplit(t.get('url', '')).path == '/index.html') and (not urlsplit(t.get('url', '')).query)]
        if len(targets) != 1 or not verify_debugger(endpoint):
            raise RuntimeError('Original App surface unavailable')
        url = targets[0].get('webSocketDebuggerUrl', '')
        parsed = urlsplit(url)
        if parsed.scheme != 'ws' or parsed.hostname != '127.0.0.1' or parsed.port != port or parsed.username or parsed.password or parsed.query or parsed.fragment or (not parsed.path.startswith('/devtools/page/')):
            raise ValueError('Non-local debugger rejected')
        queue_resume = request.get('operation') == 'resume-interrupted-queue'
        timeout = 42 if queue_resume else 10
        socket = websocket.create_connection(url, timeout=timeout, suppress_origin=True, http_proxy_host=None, http_no_proxy=['127.0.0.1', 'localhost', '::1'])
        try:
            socket.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate', 'params': {'expression': (program or UI_PROGRAM) + '(' + json.dumps(request, ensure_ascii=True) + ')', 'awaitPromise': True, 'returnByValue': True, 'timeout': 40000 if queue_resume else 8000}}))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                message = json.loads(socket.recv())
                if message.get('id') != 1:
                    continue
                result = message.get('result', {})
                if message.get('error') or result.get('exceptionDetails'):
                    raise RuntimeError('Native question UI acknowledgement failed')
                value = result.get('result', {}).get('value')
                if not isinstance(value, dict) or value.get('status') not in (statuses or {'closed', 'not-open', 'different-question-preserved', 'new-draft-preserved', 'unanswered-preserved', 'awaiting-native-acceptance', 'capacity-observed', 'capacity-not-rendered', 'queue-resumed', 'queue-not-paused', 'queue-resume-pending', 'question-closed', 'question-skipped', 'question-opened', 'question-not-open'}):
                    raise RuntimeError('Invalid acknowledgement result')
                if program is not None:
                    return value
                return value if request.get('operation') in ('capacity-retry-state', 'resume-interrupted-queue', 'question-control') else {**value, 'answerDelivered': True}
            raise TimeoutError('Native UI acknowledgement timed out')
        finally:
            socket.close()
