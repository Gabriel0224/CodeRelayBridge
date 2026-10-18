"""DSH Desktop 0.2.x's bundled Harness 0.1 native transport.

Uses the existing same-user engine, its native dot RPC and two downlinks.
No new engine, provider secret, anonymous public listener, or fallback from
the modern authenticated protocol. Random loopback ports are rediscovered
from the original Node process, never guessed or read from browser history.
"""
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import queue
import re
import threading
import urllib.error
import urllib.request
import uuid
from crb.deepseek.harness_client import HarnessClient, HarnessError

def discover_desktop_port(home, runtime, proc=Path('/proc'), uid=None):
    home, runtime = (Path(home).resolve(), Path(runtime).resolve())
    uid = os.getuid() if uid is None else uid
    ports = set()
    for process in proc.iterdir():
        if not process.name.isdecimal():
            continue
        try:
            if process.stat().st_uid != uid or (process / 'exe').resolve() != runtime / 'node':
                continue
            args = (process / 'cmdline').read_bytes().decode().split('\x00')
            program = str(runtime / 'harness/node_modules/@deepseek-ai/dsh/lib/bin.js')
            if args[:3] != [str(runtime / 'node'), program, 'web'] or '--host' not in args:
                continue
            if args[args.index('--host') + 1] != '127.0.0.1':
                continue
            environment = (process / 'environ').read_bytes().split(b'\x00')
            if ('DSH_HOME=' + str(home)).encode() not in environment:
                continue
            sockets = set()
            for fd in (process / 'fd').iterdir():
                try:
                    target = os.readlink(fd)
                    match = re.fullmatch('socket:\\[(\\d+)\\]', target)
                    if match:
                        sockets.add(match[1])
                except OSError:
                    pass
            for line in (process / 'net/tcp').read_text().splitlines()[1:]:
                fields = line.split()
                address, port = fields[1].split(':')
                if fields[3] == '0A' and address == '0100007F' and (fields[9] in sockets):
                    ports.add(int(port, 16))
        except (OSError, ValueError, UnicodeError, IndexError):
            continue
    if len(ports) != 1:
        raise HarnessError('原 DSH Desktop 未运行，或发现多个本地引擎；不会另启引擎', 'harness/desktop-port')
    return ports.pop()

class DesktopHarnessClient(HarnessClient):
    METHODS = {name.replace('.', '/'): name for name in ('session.list', 'session.create', 'session.rename', 'session.fork', 'session.selectModel', 'session.prompt', 'session.cancel', 'session.updateQueue', 'session.attachment', 'workspace.create', 'workspace.rename', 'workspace.delete', 'workspace.insertSessionBefore', 'workspace.archiveSession')}

    def __init__(self, home, runtime, opener=None, port_resolver=None):
        super().__init__(home, opener=opener)
        self.runtime = Path(runtime).resolve()
        self.resolve_port = port_resolver or (lambda: discover_desktop_port(self.home, self.runtime))
        self.port_lock = threading.Lock()
        self.port = None
        self.hub_lock = threading.RLock()
        self.subscribers = set()
        self.pumps = set()
        self.ready = {'mux': threading.Event(), 'host': threading.Event()}
        self.pending = {}
        self.inboxes = {}
        self.selections = {}
        self.responses = {}
        self.reply_watermarks = {}
        self.read_receipts = lambda: {}

    def _headers(self):
        with self.port_lock:
            if self.port is None:
                self.port = self.resolve_port()
            self.base = f'http://127.0.0.1:{self.port}'
            self.authority = f'127.0.0.1:{self.port}'
        return {'Origin': self.base, 'Content-Type': 'application/json', 'Accept': 'application/json'}

    def native_rpc(self, method, payload=None, timeout=12, rpc_id=None, remote=False):
        grammar = '[a-zA-Z][\\w-]*/[a-zA-Z][\\w-]*' if remote else '[a-zA-Z][\\w-]*\\.[a-zA-Z][\\w-]*'
        if not re.fullmatch(grammar, method):
            raise ValueError('Invalid DSH Desktop endpoint')
        identity = rpc_id or str(uuid.uuid4())
        headers = self._headers()
        envelope = {'type': 'client-request', 'rpcId': identity, 'method': method, 'payload': {'args': payload or {}} if remote else payload or {}}
        req = urllib.request.Request(self.base + '/api/' + method, data=json.dumps(envelope, ensure_ascii=False).encode(), headers=headers)
        try:
            with self.opener.open(req, timeout=timeout) as reply:
                raw = reply.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise HarnessError('原 DSH Desktop 响应超过读取上限', 'harness/response-limit')
            result = json.loads(raw)
        except urllib.error.HTTPError as error:
            raise HarnessError(f'Harness 本地服务返回 HTTP {error.code}', 'harness/http') from None
        except (TimeoutError, OSError, urllib.error.URLError):
            with self.port_lock:
                self.port = None
            raise HarnessError('原 DSH Desktop 未确认操作；不会自动重发', 'harness/connection', True) from None
        except (ValueError, TypeError):
            raise HarnessError('原 DSH Desktop 返回无效响应', 'harness/protocol', True) from None
        if result.get('type') != 'server-response' or result.get('rpcId') != identity or (not isinstance(result.get('result'), dict)):
            raise HarnessError('原 DSH Desktop 请求确认不匹配', 'harness/protocol', True)
        result = result['result']
        if result.get('ok') is not True:
            error = result.get('error') or {}
            code = str(error.get('code') or 'rejected')
            if code == 'attachment-error' and (error.get('details') or {}).get('reason') == 'MODEL_DOES_NOT_SUPPORT_IMAGES':
                raise HarnessError('当前原生模型不支持图片输入，请先选择支持图片的视觉模型', code)
            raise HarnessError('原 DSH Desktop 拒绝操作：' + code, code)
        return result.get('value')

    def history(self, sid, before=None):
        """Read complete native execution events for at least five whole turns."""
        request = {'sessionId': sid, 'maxMessages': 64}
        if before is not None:
            request['beforeSeq'] = before
        page = self.native_rpc('session.history', request)
        entries, more = (page['events'], page['hasMore'])
        while more and sum((r['event']['type'] == 'turn/start' for r in entries)) < 5:
            cursor = min((r['event']['seq'] for r in entries)) if entries else 0
            if not cursor:
                break
            earlier = self.native_rpc('session.history', {**request, 'beforeSeq': cursor})
            if not earlier['events'] or min((r['event']['seq'] for r in earlier['events'])) >= cursor:
                raise HarnessError('原生历史游标未前进', 'harness/history')
            entries = earlier['events'] + entries
            more = earlier['hasMore']
            if len(json.dumps(entries)) > 16 * 1024 * 1024:
                raise HarnessError('五轮原生历史超过读取上限', 'harness/response-limit')
        starts = [r['event']['seq'] for r in entries if r['event']['type'] == 'turn/start']
        if len(starts) > 5:
            entries = [r for r in entries if r['event']['seq'] >= starts[-5]]
            more = True
        return {**page, 'events': entries, 'hasMore': more}

    def rpc(self, endpoint, args=None, timeout=12, rpc_id=None):
        args = args or {}
        request = copy.deepcopy(args.get('request') or args.get('_request') or {})
        if endpoint == 'session/modelCatalog':
            value = self.native_rpc('llm.models', {})
            descriptions = self.native_rpc('settings.describe', {})
            default = next((s['value'] for s in descriptions['namespaces'] if s['ns'] == 'agent-default-model'), {})
            return {**value, 'default': {k: default[k] for k in ('provider', 'model', 'reasoningEffort') if k in default}}
        if endpoint == 'session/projections':
            value = copy.deepcopy(self.history(request['sessionId']).get('projections', {'values': {}, 'asOfSeq': -1}))
            values = value.setdefault('values', {})
            if self.ready['host'].is_set():
                with self.hub_lock:
                    frame = copy.deepcopy(self.inboxes.get(request['sessionId']))
                inbox = {'queued': [], 'steering': [], 'context': []}
                for occurrence in (frame or {}).get('payload', {}).get('items', []):
                    inbox[occurrence['placement']].append(occurrence['message'])
                values['inbox'] = inbox
            else:
                values.pop('inbox', None)
            return value
        if endpoint == 'session/page':
            page = self.history(request['address']['sessionId'], request.get('beforeSeq'))
            return {'records': [{'type': 'event', 'event': r['event']} for r in page['events']], 'hasMore': page['hasMore']}
        if endpoint == 'skills/list':
            return self.native_rpc('skill.list', request)
        if endpoint == 'commands/execute':
            if not re.fullmatch('/permission [a-z-]+', args.get('line', '')):
                raise ValueError('只有原生权限命令由此接口执行')
            return self.native_rpc('commands/execute', {'agentId': args['agentId'], 'line': args['line'], 'images': []}, remote=True, rpc_id=rpc_id)
        method = self.METHODS.get(endpoint)
        if not method:
            raise HarnessError('Harness 本地服务返回 HTTP 404', 'harness/http')
        if endpoint == 'session/prompt':
            rpc_id = request.pop('requestId', rpc_id)
            if any((x.get('type') == 'file' for x in request.get('content', []))):
                raise ValueError('此版本原生支持图片附件，不支持通用文件上传')
        value = self.native_rpc(method, request, timeout=timeout, rpc_id=rpc_id)
        if endpoint == 'session/selectModel':
            self.selections[request['sessionId']] = value['selected']
        elif endpoint == 'session/list':
            reads = self.read_receipts()
            with self.hub_lock:
                watched = {sid for _, _, kind, sid in self.subscribers if kind == 'session/follow'}
            for row in value['items']:
                sid = row['sessionId']
                if sid in watched and sid not in self.selections:
                    self.selections[sid] = self.native_rpc('session.models', {'sessionId': sid})['current']
                projection = row.setdefault('projections', {'values': {}, 'asOfSeq': -1})['values']
                if sid in self.selections:
                    projection['modelSelection'] = {'next': self.selections[sid]}
                with self.hub_lock:
                    inbox = self.inboxes.get(sid)
                if inbox:
                    pending = {'queued': [], 'steering': [], 'context': []}
                    for occurrence in inbox['payload']['items']:
                        pending[occurrence['placement']].append(occurrence['message'])
                    projection['inbox'] = pending
                watermark = row.get('projections', {}).get('asOfSeq', -1)
                if sid in reads and (not row.get('running')) and (not row.get('blank')) and (watermark > reads[sid]) and (self.responses.get(sid, -1) <= reads[sid]) and (self.reply_watermarks.get(sid) != watermark):
                    tail = self.native_rpc('session.history', {'sessionId': sid, 'maxMessages': 1})
                    for record in tail['events']:
                        if record['event']['type'] == 'assistant/message':
                            self.responses[sid] = max(self.responses.get(sid, -1), record['event']['seq'])
                    self.reply_watermarks[sid] = watermark
                if sid in self.responses:
                    row['projections']['values']['turnOutline'] = [{'response': True, 'seq': self.responses[sid]}]
        return value

    def _publish(self, channel, value):
        with self.hub_lock:
            payload = value.get('payload', {})
            kind = payload.get('type')
            if kind in ('question/requested', 'approval/requested'):
                self.pending[value['rpcId']] = value
            elif kind == 'session/queue':
                if payload['items']:
                    self.inboxes[payload['sessionId']] = value
                else:
                    self.inboxes.pop(payload['sessionId'], None)
            elif kind == 'session/event' and payload['event']['type'] == 'assistant/message':
                self.responses[payload['sessionId']] = payload['event']['seq']
            elif kind == 'question/resolved':
                self.pending.pop(payload['questionRpcId'], None)
            elif kind == 'approval/resolved':
                self.pending = {k: v for k, v in self.pending.items() if v['payload'].get('approvalId') != payload['approvalId']}
            elif kind == 'host/session-removed':
                for cache in (self.selections, self.inboxes, self.responses, self.reply_watermarks):
                    cache.pop(payload['sessionId'], None)
            if kind == 'host/remote-event' and payload.get('event') in ('settings/document-updated', 'llm/adapters-updated'):
                self.selections.clear()
            for sink, channels, endpoint, sid in list(self.subscribers):
                if channel not in channels:
                    continue
                if kind:
                    if endpoint == 'session/follow' and payload.get('sessionId') != sid:
                        continue
                    if endpoint == 'workspace/follow' and (not kind.startswith('host/')):
                        continue
                    if endpoint == '$events' and kind not in ('approval/requested', 'approval/resolved', 'question/requested', 'question/resolved', 'host/remote-event', 'stream/error'):
                        continue
                    if endpoint == 'session/control' and kind == 'session/event' and (payload['event']['type'] != 'assistant/message'):
                        continue
                try:
                    sink.put_nowait(value)
                except queue.Full:
                    while not sink.empty():
                        sink.get_nowait()
                    sink.put_nowait({'failure': True})

    def _pump(self, channel):
        import websocket
        while not self.closed.is_set():
            socket = None
            try:
                self._headers()
                socket = websocket.create_connection(self.base.replace('http:', 'ws:') + '/api/events.' + channel, timeout=5, origin=self.base, http_no_proxy=['127.0.0.1'])
                socket.settimeout(1)
                self.ready[channel].set()
                self._publish(channel, {'connected': True})
                while not self.closed.is_set():
                    try:
                        raw = socket.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    if not raw or len(raw) > 16 * 1024 * 1024:
                        raise HarnessError('DSH Desktop 本地事件已断开', 'harness/stream')
                    value = json.loads(raw)
                    if value.get('type') != 'server-request' or not isinstance(value.get('payload'), dict):
                        raise ValueError('Invalid native downlink')
                    self._publish(channel, value)
            except Exception:
                self.ready[channel].clear()
                with self.port_lock:
                    self.port = None
                self._publish(channel, {'failure': True})
                self.closed.wait(3)
            finally:
                if socket:
                    socket.close()

    @contextmanager
    def subscribe(self, channels, endpoint, sid=None):
        sink = queue.Queue(maxsize=4096)
        channels = frozenset(channels)
        with self.hub_lock:
            subscription = (sink, channels, endpoint, sid)
            self.subscribers.add(subscription)
            if endpoint == '$events':
                for value in self.pending.values():
                    sink.put_nowait(value)
            if endpoint == 'session/follow' and sid in self.inboxes:
                sink.put_nowait(self.inboxes[sid])
            for channel in channels:
                if channel not in self.pumps:
                    self.pumps.add(channel)
                    threading.Thread(target=self._pump, args=(channel,), daemon=True).start()
        try:
            for channel in channels:
                if not self.ready[channel].wait(7):
                    raise HarnessError('原 DSH Desktop 事件尚未连接', 'harness/stream')
            yield sink
        finally:
            with self.hub_lock:
                self.subscribers.discard(subscription)

    def messages(self, sink, stop):
        while not self.closed.is_set() and (not (stop and stop.is_set())):
            try:
                value = sink.get(timeout=0.5)
            except queue.Empty:
                continue
            if value.get('failure'):
                raise HarnessError('原 DSH Desktop 事件需重新同步', 'harness/stream')
            if value.get('connected'):
                continue
            yield value

    def stream(self, endpoint, args=None, stop=None):
        args = args or {}
        channels = ('host',) if endpoint == 'workspace/follow' else ('mux',) if endpoint == 'session/follow' else ('mux', 'host')
        sid = args.get('request', {}).get('address', {}).get('sessionId')
        with self.subscribe(channels, endpoint, sid) as sink:
            if endpoint == 'workspace/follow':
                baseline = self.native_rpc('workspace.list', {})
                yield {'type': 'baseline', 'value': {**baseline, 'pinnedSessionIds': []}}
            elif endpoint == 'session/control':
                yield {'type': 'baseline'}
            elif endpoint == '$events':
                yield {'type': 'ready', 'clientId': 'dsh-desktop-native'}
            elif endpoint == 'session/follow':
                sid = args['request']['address']['sessionId']
                page = self.history(sid)
                row = next((r for r in self.native_rpc('session.list', {})['items'] if r['sessionId'] == sid))
                projection = page.get('projections', {'asOfSeq': -1, 'values': {}})
                selected = self.native_rpc('session.models', {'sessionId': sid})['current']
                projection['values']['modelSelection'] = {'next': selected}
                self.selections[sid] = selected
                yield {'type': 'snapshot', 'header': {'id': sid, 'cwd': row.get('cwd', '')}, 'cursor': max(projection['asOfSeq'], max((r['event']['seq'] for r in page['events']), default=-1)), 'records': [{'type': 'event', 'event': r['event']} for r in page['events']], 'projections': projection, 'hasMore': page['hasMore']}
            else:
                raise ValueError('Unsupported Desktop stream')
            for envelope in self.messages(sink, stop):
                p = envelope['payload']
                kind = p['type']
                if kind == 'stream/error':
                    raise HarnessError('原 DSH Desktop 事件错误', 'harness/stream')
                if endpoint == 'workspace/follow':
                    mappings = {'host/workspace-changed': 'upsert', 'host/workspace-removed': 'remove', 'host/archived-sessions-changed': 'archived'}
                    if kind in mappings:
                        yield {**p, 'type': mappings[kind]}
                elif endpoint == 'session/control':
                    if kind == 'session/projection':
                        yield {**p, 'type': 'projection'}
                    elif kind in ('host/session-added', 'host/session-removed', 'host/session-status'):
                        yield {'type': 'baseline'}
                    elif kind == 'session/event' and p['event']['type'] == 'assistant/message':
                        yield {'type': 'projection', 'sessionId': p['sessionId'], 'key': 'turnOutline', 'value': [{'response': True, 'seq': p['event']['seq']}]}
                elif endpoint == 'session/follow' and p.get('sessionId') == sid:
                    if kind == 'session/event':
                        yield {'type': 'event', 'event': p['event']}
                    elif kind == 'session/queue':
                        yield {'type': 'legacy-inbox', 'items': p['items']}
                    elif kind == 'session/projection':
                        yield {**p, 'type': 'legacy-projection'}
                elif endpoint == '$events':
                    if kind == 'host/remote-event':
                        yield {'type': 'emit', 'event': p['event']}
                    elif kind in ('approval/requested', 'question/requested'):
                        yield {'type': 'waterfall', 'event': 'approval/request' if kind == 'approval/requested' else 'user-questions/request', 'agentId': p['sessionId'], 'eventId': envelope['rpcId'], 'request': p}
                    elif kind == 'question/resolved':
                        yield {'type': 'cancel', 'eventId': p['questionRpcId']}
                    elif kind == 'approval/resolved':
                        yield {'type': 'legacy-approval-resolved', 'approvalId': p['approvalId']}

    def respond(self, identity, payload):
        with self.hub_lock:
            original = self.pending.get(identity)
        if not original or original['payload']['sessionId'] != payload.get('sessionId'):
            raise ValueError('原生问题已结束或不属于该会话')
        headers = self._headers()
        data = {'type': 'client-response', 'rpcId': identity, 'result': {'ok': True, 'value': payload}}
        req = urllib.request.Request(self.base + '/api/respond', data=json.dumps(data).encode(), headers=headers)
        try:
            with self.opener.open(req, timeout=12) as reply:
                receipt = json.load(reply)
        except Exception:
            raise HarnessError('原 DSH Desktop 未确认回答；不会自动重发', 'harness/connection', True) from None
        return receipt
