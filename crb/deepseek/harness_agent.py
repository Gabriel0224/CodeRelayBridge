"""Independent DeepSeek Harness adapter; never starts a second Host/engine.

Only the original same-user localhost RPC/streams are used. Application node
identity is distinct from the physical SSH host and the existing Codex agent.
"""
import argparse
import base64
import copy
from contextlib import contextmanager
import gzip
import hashlib
import json
import math
import mimetypes
import os
from pathlib import Path
import queue
import re
import sqlite3
import threading
import time
import urllib.request
import uuid
from crb.deepseek.harness_client import HarnessClient, HarnessError
from crb.common.live_view_cache import NativeReadCache, NATIVE_READ_TTLS
APP_ID = 'deepseek'
MAX_UPLOAD = 16 * 1024 * 1024
ACTIONS = {'watch', 'attach_thread', 'mark_read', 'send', 'steer', 'stop_turn', 'new_thread', 'rename_thread', 'fork_thread', 'archive', 'unarchive', 'project_create', 'project_rename', 'project_delete', 'update_settings', 'load_older', 'queue_edit', 'queue_delete', 'queue_send_now', 'upload_asset_chunk', 'attach_local_file', 'resolve_request', 'fetch_asset', 'native_read', 'update_permissions', 'native_action'}
PUBLIC_EVENTS = {'turn/start', 'turn/end', 'user/message', 'assistant/message', 'assistant/attempt', 'tool/call', 'tool/result', 'compaction/start', 'compaction/end', 'approval/asked', 'approval/decided', 'todo/write', 'llm/retry', 'llm/retry-started'}
QUEUE_ATTACHMENT_EDIT_REASON = '原 App 不支持编辑含附件的待发送消息；原附件保持不变'

def queue_text_editable(message):
    content = message.get('content')
    return bool(isinstance(content, list) and content and all((isinstance(block, dict) and block.get('type') == 'text' for block in content)))

def numeric_fields(value, names):
    """Publish only named native numbers, not arbitrary projection/config fields."""
    if not isinstance(value, dict):
        return {}
    return {name: value[name] for name in names if isinstance(value.get(name), (int, float)) and (not isinstance(value[name], bool)) and math.isfinite(value[name]) and (value[name] >= 0)}

def public_metadata(projections):
    return {'nativeUsage': numeric_fields(projections.get('tokenUsage'), ('uncachedInputTokens', 'outputTokens', 'cacheReadTokens', 'cacheWriteTokens')), 'contextPressure': numeric_fields(projections.get('contextPressure'), ('pressureTokens', 'projectedTokens', 'contextWindow')), 'contextBreakdown': numeric_fields(projections.get('contextBreakdown'), ('systemTokens', 'toolsTokens', 'messageTokens')), 'stats': numeric_fields(projections.get('sessionStats'), ('turns', 'steps', 'llmMs', 'toolMs', 'ttftMs', 'ttftSteps', 'decodeMs', 'decodeTokens'))}

def schedule_prompt(content):
    """Decode only the native framing of a source= schedule message."""
    value = text_blocks(content)
    try:
        if value.startswith('[SCHEDULE REMINDER]\n'):
            line = next((line for line in value.splitlines() if line.startswith('reminder_prompt_json: ')))
            decoded = json.loads(line.split(': ', 1)[1])
            return decoded if isinstance(decoded, str) else value
        if value.startswith('[SCHEDULE REMINDER BATCH]\n'):
            line = next((line for line in value.splitlines() if line.startswith('reminders_json: ')))
            records = json.loads(line.split(': ', 1)[1])
            if isinstance(records, list) and all((isinstance(r.get('reminder_prompt'), str) for r in records)):
                return '\n\n'.join((r['reminder_prompt'] for r in records))
    except (ValueError, StopIteration, TypeError, AttributeError):
        pass
    return value

def text_blocks(content):
    return '\n'.join((str(b.get('text', '')) for b in content or [] if isinstance(b, dict) and b.get('type') == 'text'))

def model_id(selection):
    return f"{selection['provider']}::{selection['model']}" if selection else ''

def public_models(catalog):
    default = model_id(catalog.get('default'))
    values = [{'id': f"{g['id']}::{m['id']}", 'label': f"{m.get('name', m['id'])} · {g['name']}", 'shortLabel': m.get('name', m['id']), 'description': m.get('description', ''), 'efforts': [{'id': e['id'], 'description': e.get('description', '')} for e in m.get('reasoning', {}).get('efforts', [])], 'defaultEffort': m.get('reasoning', {}).get('defaultEffort', '')} for g in catalog.get('groups', []) for m in g.get('models', [])]
    return sorted(values, key=lambda m: m['id'] != default)

class SessionView:
    """Native sequence numbers are authoritative; streamed blocks never duplicate commits."""

    def __init__(self, sid):
        self.sid = sid
        self.events = {}
        self.cursor = -1
        self.header = {}
        self.projections = {}
        self.attempt = None
        self.blocks = {}
        self.stream_revision = -1
        self.next_index = 0
        self.has_more = False
        self.connected = False
        self.revision = 0

    def chunk(self, chunk):
        kind, index = (chunk.get('type'), chunk.get('index', 0))
        if kind == 'block-start':
            self.blocks[index] = {'type': chunk['blockType'], **({'arguments': ''} if chunk['blockType'] == 'tool-call' else {'text': ''})}
        elif kind in ('text-delta', 'reasoning-delta'):
            b = self.blocks.setdefault(index, {'type': 'reasoning' if kind == 'reasoning-delta' else 'text', 'text': ''})
            b['text'] = b.get('text', '') + chunk.get('text', '')
        elif kind == 'tool-call-delta':
            b = self.blocks.setdefault(index, {'type': 'tool-call', 'arguments': ''})
            b['id'] = chunk['id']
            if chunk.get('name'):
                b['name'] = chunk['name']
            b['arguments'] = b.get('arguments', '') + chunk.get('argumentsDelta', '')
        elif kind == 'block-end':
            self.blocks[index] = copy.deepcopy(chunk['block'])

    def apply(self, frame):
        kind = frame['type']
        if kind == 'snapshot':
            self.header = frame['header']
            self.events = {r['event']['seq']: r['event'] for r in frame['records'] if r['type'] == 'event' and r['event']['type'] in PUBLIC_EVENTS}
            self.cursor = frame['cursor']
            self.has_more = frame['hasMore']
            self.projections = copy.deepcopy(frame.get('projections', {}).get('values', {}))
            a = frame.get('assistantStream', {})
            self.stream_revision = a.get('revision', 0)
            self.attempt = copy.deepcopy(a.get('activeAttempt'))
            self.blocks = {}
            self.next_index = self.attempt.get('nextIndex', 0) if self.attempt else 0
            for record in (self.attempt or {}).get('stream', []):
                if record['type'] == 'chunk':
                    self.chunk(record['chunk'])
                elif record['type'] in ('text-chunks', 'reasoning-chunks'):
                    self.chunk({'type': 'reasoning-delta' if record['type'] == 'reasoning-chunks' else 'text-delta', 'index': record['index'], 'text': ''.join(record['texts'])})
                elif record['type'] == 'tool-call-chunks':
                    self.chunk({'type': 'tool-call-delta', 'index': record['index'], 'id': record['id'], 'name': record.get('name'), 'argumentsDelta': ''.join(record['args'])})
        elif kind == 'event':
            e = frame['event']
            if e['seq'] <= self.cursor:
                return False
            if e['type'] in PUBLIC_EVENTS:
                self.events[e['seq']] = e
            self.cursor = e['seq']
            if e['type'] == 'turn/end':
                starts = sorted((n for n, event in self.events.items() if event['type'] == 'turn/start'))
                if len(starts) > 5:
                    self.events = {n: event for n, event in self.events.items() if n >= starts[-5]}
                    self.has_more = True
        elif kind == 'assistant-stream':
            f = frame['frame']
            if f['revision'] <= self.stream_revision:
                return False
            self.stream_revision = f['revision']
            if f['type'] == 'start':
                self.attempt, self.blocks, self.next_index = (copy.deepcopy(f), {}, 0)
            elif self.attempt and self.attempt['attemptId'] == f['attemptId']:
                if f['index'] != self.next_index:
                    raise HarnessError('Harness 流式序号中断，重新读取原生快照', 'harness/stream-gap')
                self.next_index += 1
                if f['type'] == 'chunk':
                    self.chunk(f['chunk'])
                else:
                    self.attempt, self.blocks = (None, {})
        else:
            return False
        self.connected = True
        self.revision += 1
        return True

    def items(self):
        items, turn, calls, seq = ([], 0, {}, self.cursor)

        def add(identity, kind, text='', detail=None, at=None, phase=None, **extra):
            item = {'id': str(identity), 'turnId': str(turn), 'type': kind, 'text': text, 'timestamp': at, 'nativeSeq': seq, **extra}
            if detail is not None:
                item['detail'] = detail
            if phase:
                item['phase'] = phase
            items.append(item)
            return item
        for seq, e in sorted(self.events.items()):
            t, d, at = (e['type'], e.get('data') or {}, e['time'])
            turn = d.get('turn', turn)
            if t == 'user/message' and d.get('source', {}).get('kind') in ('user', 'schedule'):
                scheduled = d['source']['kind'] == 'schedule'
                item = add(d['id'], 'userMessage', schedule_prompt(d.get('content')) if scheduled else text_blocks(d.get('content')), at=at)
                if scheduled:
                    item['scheduledTrigger'] = True
                item['attachments'] = self.attachments(d.get('content'))
                item['clientUserMessageId'] = d.get('source', {}).get('rpcId')
            elif t == 'assistant/message':
                m = d['message']
                has_tool = any((b['type'] == 'tool-call' for b in m['content']))
                for index, b in enumerate(m['content']):
                    if b['type'] in ('text', 'reasoning') and b.get('text'):
                        add(f"{m['id']}:{index}", 'reasoning' if b['type'] == 'reasoning' else 'agentMessage', b['text'], at=at, phase='commentary' if has_tool else 'final_answer')
            elif t == 'tool/call':
                try:
                    args = json.loads(d['arguments'])
                except (ValueError, TypeError):
                    args = {}
                name = d['name']
                command = args.get('command') or args.get('cmd')
                kind = 'commandExecution' if command else 'fileChange' if name in ('edit', 'write') else 'mcpToolCall'
                calls[d['callId']] = add(d['callId'], kind, str(command or name), detail=d['arguments'], at=at, status='inProgress')
            elif t == 'tool/result':
                m = d['message']
                item = calls.get(m.get('toolCallId'))
                if not item:
                    item = add(m['id'], 'mcpToolCall', '工具结果', at=at)
                item['status'] = 'failed' if m.get('isError') else 'completed'
                item['detail'] = (item.get('detail', '') + '\n\n' + text_blocks(m.get('content'))).strip()
            elif t in ('compaction/start', 'compaction/end'):
                add('compaction:' + str(seq), 'contextCompaction', '正在压缩上下文…' if t.endswith('start') else '上下文已压缩', at=at)
            elif t == 'todo/write':
                labels = {'pending': '待做', 'in_progress': '进行中', 'completed': '完成'}
                text = '\n'.join(('[' + labels.get(row.get('status'), row.get('status', '')) + '] ' + row.get('content', '') for row in d.get('todos', [])))
                add('todos:' + str(seq), 'plan', text or '计划已清空', at=at)
            elif t == 'llm/retry':
                failure = d.get('failure') if isinstance(d.get('failure'), dict) else {}
                delay = d.get('delayMs')
                text = '模型请求暂时失败，原 App 正在重试'
                if isinstance(delay, (int, float)):
                    text += f'（等待 {delay / 1000:g} 秒）'
                if failure.get('message'):
                    text += '：' + str(failure['message'])
                add('retry:' + str(seq), 'systemNotice', text, at=at, noticeKind='info')
            elif t == 'turn/end' and d.get('reason', {}).get('kind') not in ('completed', 'cancelled', 'canceled', 'forked'):
                reason = d.get('reason') or {}
                stopped = reason.get('kind') in ('aborted', 'interrupted')
                failed = reason.get('kind') in ('blocked', 'max-tokens', 'failed', 'error', 'llm-failure')
                error = reason.get('error') or {}
                message = error.get('message') if isinstance(error, dict) else str(error)
                text = '任务已停止' if stopped else message or {'blocked': '本轮执行被阻塞', 'max-tokens': '本轮已达到输出长度上限'}.get(reason.get('kind'), '本轮执行异常' if failed else '本轮结束，原 App 未提供可识别的结束状态')
                add('error:' + str(seq), 'systemNotice', text, at=at, noticeKind='error' if failed else 'info')
        if self.attempt:
            turn = self.attempt['turn']
            committed = any((e['type'] in ('assistant/message', 'assistant/attempt') and e['seq'] > self.attempt['startedAfterSeq'] for e in self.events.values()))
            if not committed:
                for index, b in sorted(self.blocks.items()):
                    if b['type'] in ('text', 'reasoning') and b.get('text'):
                        add('live:' + self.attempt['attemptId'] + ':' + str(index), 'reasoning' if b['type'] == 'reasoning' else 'agentMessage', b['text'], phase='commentary', status='inProgress')
        return items

    @staticmethod
    def attachments(content):
        values = []
        for b in content or []:
            a = b.get('attachment') or b.get('file') or {}
            if a.get('attachmentId'):
                values.append({'id': a['attachmentId'], 'name': a.get('name') or '附件', 'label': a.get('name') or '附件', 'kind': 'image' if b['type'] == 'image' else 'file', 'mime': a.get('mediaType') or '', 'size': a.get('bytes', 0)})
        return values

    def native_retry_state(self, events, turn_id):
        """Project an actual native retry, never retry the model in this bridge."""
        retry = next((event for event in reversed(events) if event['type'] == 'llm/retry' and (not turn_id or str(event.get('data', {}).get('turn')) == turn_id)), None)
        if retry is None:
            return None
        progress = {'assistant/message', 'tool/call', 'tool/result', 'compaction/start', 'compaction/end', 'turn/end'}
        if any((event['seq'] > retry['seq'] and event['type'] in progress for event in events)):
            return None
        if self.attempt and self.attempt.get('startedAfterSeq', -1) >= retry['seq'] and any((block.get('text') or block.get('arguments') for block in self.blocks.values())):
            return None
        data = retry.get('data') or {}
        started = any((event['seq'] > retry['seq'] and event['type'] == 'llm/retry-started' and (event.get('data', {}).get('retryId') == data.get('retryId')) for event in events))
        return {**numeric_fields(data, ('retry', 'maxRetries', 'delayMs')), 'started': started}

    def public(self, meta):
        items = self.items()
        selection = self.projections.get('modelSelection') or {}
        chosen = selection.get('next') or selection.get('lastUsed')
        inbox = self.projections.get('inbox', {})
        pending = []
        for q in self.projections.get('userQuestions', {}).get('active', []):
            if q['state'] in ('open', 'continued'):
                pending.append({'id': q['callId'], 'method': 'item/tool/requestUserInput', 'questions': [{**x, 'isMultiSelect': x.get('multiSelect', False)} for x in q['questions']]})
        queued = [{'id': m['id'], 'clientUserMessageId': m.get('source', {}).get('rpcId'), 'text': text_blocks(m.get('content')), 'target': target, 'attachments': self.attachments(m.get('content')), 'canEdit': queue_text_editable(m), **({} if queue_text_editable(m) else {'editReason': QUEUE_ATTACHMENT_EDIT_REASON})} for target, values in inbox.items() for m in values]
        events = sorted(self.events.values(), key=lambda e: e['seq'])
        last = next((e for e in reversed(events) if e['type'] == 'turn/end'), None)
        started = next((e for e in reversed(events) if e['type'] == 'turn/start'), None)
        running = bool(started and (not last or started['seq'] > last['seq']))
        running = running or bool(self.attempt)
        status = ('active' if running else 'idle') if started or last or self.attempt else meta.get('status', 'idle')
        turn_id = str((self.attempt or {}).get('turn') or (started or last or {}).get('data', {}).get('turn', ''))
        turn_items = [i for i in items if i.get('turnId') == turn_id and i['type'] != 'userMessage']
        phase = 'waiting_input' if pending else 'waiting_output' if status == 'active' else 'idle'
        if status == 'active' and turn_items and (not pending):
            tail = turn_items[-1]
            phase = 'executing' if tail['type'] in ('commandExecution', 'fileChange', 'mcpToolCall') and tail.get('status') == 'inProgress' else 'thinking' if tail['type'] == 'reasoning' else 'responding' if tail['type'] == 'agentMessage' and tail.get('status') == 'inProgress' else 'compacting' if tail['type'] == 'contextCompaction' and tail['text'].startswith('正在') else 'running'
        if last and (not running) and (status != 'active'):
            reason = last.get('data', {}).get('reason', {}).get('kind')
            phase = 'completed' if reason == 'completed' else 'forked' if reason == 'forked' else 'interrupted' if reason in ('cancelled', 'canceled', 'aborted', 'interrupted') else 'failed' if reason in ('blocked', 'max-tokens', 'failed', 'error', 'llm-failure') else 'unknown'
        if pending:
            phase = 'waiting_input'
        retry = self.native_retry_state(events, turn_id) if status == 'active' and (not pending) else None
        if retry is not None:
            phase = 'retrying'
        return {**meta, 'id': self.sid, 'cwd': self.header.get('cwd', meta.get('cwd', '')), 'model': model_id(chosen) or meta.get('model'), 'reasoningEffort': (chosen or {}).get('reasoningEffort') or meta.get('reasoningEffort'), 'source': 'desktop-live', 'owned': self.connected, 'status': status, 'executionState': {'phase': phase, 'turnId': turn_id or None, 'hasOutput': bool(turn_items), **({'retry': retry} if retry is not None else {})}, 'revision': self.revision, 'items': items, 'totalItems': len(items), 'partialHistory': self.has_more, 'canLoadHistory': True, 'queuedMessages': queued, 'requests': pending, 'pendingRequests': len(pending), 'canContinue': False, 'editableUserMessage': None, 'fullAccess': False, 'permissionPreset': (self.projections.get('permissions') or {}).get('currentValue'), 'turnId': str(last['data']['turn']) if last else None, 'applicationId': APP_ID}

class HarnessAgent:

    def __init__(self, cfg, client=None):
        self.cfg = cfg
        self.client = client or HarnessClient(cfg.get('harness_home'), cfg.get('harness_url', 'http://127.0.0.1:19387'))
        self.root = Path(cfg['state_dir']).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.changed_catalog = threading.Event()
        self.changed_catalog.set()
        self.changed_models = threading.Event()
        self.changed_models.set()
        self.changed_features = threading.Event()
        self.changed_features.set()
        self.native_sections = ['permissions']
        self.metadata_available = False
        self.catalog, self.projects, self.models = ([], [], [])
        self.workspace = {'items': [], 'archivedSessionIds': [], 'pinnedSessionIds': []}
        self.states, self.followers, self.results, self.dirty = ({}, {}, {}, set())
        self.view_order = {}
        self.approvals = {}
        self.assets = {}
        self.connected, self.generation = (False, None)
        self.native_feature_cache = NativeReadCache()
        self.native_cache_connection = 0
        self.commands = queue.Queue()
        self.inflight = set()
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,result TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY,session TEXT,name TEXT,mime TEXT,size INTEGER,offset INTEGER,receipt TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS read_state(session TEXT PRIMARY KEY,seq INTEGER)')
            db.execute('CREATE TABLE IF NOT EXISTS unread_state(session TEXT PRIMARY KEY,status TEXT,unread INTEGER)')
            db.execute('CREATE TABLE IF NOT EXISTS watched(session TEXT PRIMARY KEY,touched REAL)')
            db.execute('UPDATE commands SET result=? WHERE result IS NULL', (json.dumps({'ok': False, 'unknown': True, 'error': '桥接重启前的操作结果未确认；不会重发'}),))

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.root / 'harness.sqlite', timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def rpc(self, endpoint, request=None, **args):
        if request is not None:
            args['request'] = request
        return self.client.rpc(endpoint, args)

    def refresh_catalog(self):
        rows = self.rpc('session/list', _request={})['items']
        metadata_available = any((any((key in r.get('projections', {}).get('values', {}) for key in ('tokenUsage', 'contextPressure', 'sessionStats'))) for r in rows))
        if metadata_available != self.metadata_available:
            self.metadata_available = metadata_available
            self.changed_features.set()
        catalog = []
        with self.lock, self.db() as db:
            reads = dict(db.execute('SELECT session,seq FROM read_state'))
            saved = {sid: {'status': status, 'unread': bool(unread)} for sid, status, unread in db.execute('SELECT session,status,unread FROM unread_state')}
            for row in rows:
                sid = row['sessionId']
                p = row.get('projections', {}).get('values', {})
                chosen = (p.get('modelSelection') or {}).get('next') or (p.get('modelSelection') or {}).get('lastUsed')
                if chosen is None:
                    chosen = getattr(self, 'default_model', None)
                workspace = next((w for w in self.workspace['items'] if sid in w['sessionIds']), {})
                previous = next((r for r in self.catalog if r['id'] == sid), saved.get(sid, {}))
                seq = row.get('projections', {}).get('asOfSeq', -1)
                if sid not in reads:
                    db.execute('INSERT OR IGNORE INTO read_state VALUES(?,?)', (sid, seq))
                    reads[sid] = seq
                status = 'active' if row.get('running') else 'idle'
                title = p.get('title')
                if isinstance(title, dict):
                    title = title.get('title') or title.get('text')
                new_reply = any((t.get('response') and t.get('seq', -1) > reads[sid] for t in p.get('turnOutline', [])))
                unread = previous.get('unread', False) or bool(status == 'idle' and seq > reads[sid] and (previous.get('status') == 'active' or new_reply))
                db.execute('INSERT OR REPLACE INTO unread_state VALUES(?,?,?)', (sid, status, int(unread)))
                catalog.append({'id': sid, 'title': title or '新对话', 'cwd': row.get('cwd', ''), 'projectId': workspace.get('workspaceId'), 'projectName': workspace.get('title'), 'status': status, 'archived': sid in self.workspace['archivedSessionIds'], 'pinned': sid in self.workspace['pinnedSessionIds'], 'updatedAt': row['updatedAt'] / 1000, 'model': model_id(chosen), 'reasoningEffort': (chosen or {}).get('reasoningEffort'), 'unread': unread})
                if sid in self.states:
                    self.states[sid].projections = copy.deepcopy(p)
                    self.dirty.add(sid)
            self.catalog = catalog
            self.projects = [{'id': w['workspaceId'], 'name': w['title'], 'path': w['path'], 'roots': [w['path']], 'threadCount': len(w['sessionIds'])} for w in self.workspace['items']]
            if not self.connected:
                self.native_cache_connection += 1
                self.native_feature_cache.invalidate()
            self.connected = True

    def global_stream(self, endpoint):
        while not self.stop.is_set():
            if getattr(self, 'connection_retry', None) is not None and (not self.connected):
                self.stop.wait(0.5)
                continue
            try:
                for frame in self.client.stream(endpoint, stop=self.stop):
                    with self.lock:
                        if endpoint == 'workspace/follow':
                            t = frame['type']
                            if t == 'baseline':
                                self.workspace = frame['value']
                            elif t == 'upsert':
                                w = frame['workspace']
                                self.workspace['items'] = [x for x in self.workspace['items'] if x['workspaceId'] != w['workspaceId']] + [w]
                            elif t == 'remove':
                                self.workspace['items'] = [x for x in self.workspace['items'] if x['workspaceId'] != frame['workspaceId']]
                            elif t == 'archived':
                                self.workspace['archivedSessionIds'] = frame['archivedSessionIds']
                            elif t == 'pinned':
                                self.workspace['pinnedSessionIds'] = frame['pinnedSessionIds']
                        elif frame['type'] == 'projection':
                            s = self.states.get(frame['sessionId'])
                            if s:
                                s.projections[frame['key']] = frame['value']
                                s.revision += 1
                                self.dirty.add(s.sid)
                    if endpoint == 'workspace/follow' or frame.get('type') == 'baseline' or frame.get('key') in ('title', 'sessionListMetadata', 'modelSelection', 'turnOutline'):
                        self.changed_catalog.set()
            except Exception:
                with self.lock:
                    self.connected = False
                self.stop.wait(3)

    def interaction_stream(self):
        """Native broadcast arbitration: either desktop or panel can settle once."""
        while not self.stop.is_set():
            if getattr(self, 'connection_retry', None) is not None and (not self.connected):
                self.stop.wait(0.5)
                continue
            client_id = None
            try:
                for frame in self.client.stream('$events', stop=self.stop):
                    kind = frame['type']
                    if kind == 'ready':
                        client_id = frame['clientId']
                        self.changed_models.set()
                        self.changed_features.set()
                    elif kind == 'emit' and frame.get('event') in ('llm/adapters-updated', 'settings/document-updated', 'credentials/record-updated', 'credentials/reference-updated'):
                        self.changed_models.set()
                    elif kind == 'emit' and frame.get('event') == 'plugin-manager/changed':
                        self.changed_models.set()
                        self.changed_features.set()
                        self.changed_catalog.set()
                    elif kind == 'waterfall':
                        if frame['event'] not in ('approval/request', 'user-questions/request'):
                            self.client.rpc('$events/result', {'clientId': client_id, 'eventId': frame['eventId'], 'outcome': {'kind': 'next'}})
                            continue
                        with self.lock:
                            self.approvals[frame['eventId']] = {**frame, 'clientId': client_id}
                            if frame['agentId'] in self.states:
                                self.states[frame['agentId']].revision += 1
                            self.dirty.add(frame['agentId'])
                    elif kind == 'cancel':
                        with self.lock:
                            old = self.approvals.pop(frame['eventId'], None)
                            if old:
                                if old['agentId'] in self.states:
                                    self.states[old['agentId']].revision += 1
                                self.dirty.add(old['agentId'])
            except Exception:
                self.stop.wait(2)
            finally:
                with self.lock:
                    for identity, frame in list(self.approvals.items()):
                        if frame['clientId'] == client_id:
                            self.approvals.pop(identity, None)
                            if frame['agentId'] in self.states:
                                self.states[frame['agentId']].revision += 1
                            self.dirty.add(frame['agentId'])

    def follow(self, sid, cancel):
        while not cancel.is_set() and (not self.stop.is_set()):
            try:
                args = {'request': {'address': {'kind': 'session', 'sessionId': sid}, 'assistantStream': True, 'turnWindow': {'minMessages': 5, 'minTurns': 5}}}
                for frame in self.client.stream('session/follow', args, cancel):
                    with self.lock:
                        if cancel.is_set() or sid not in self.states:
                            return
                        state = self.states[sid]
                        if state.apply(frame):
                            self.dirty.add(sid)
                        if frame.get('event', {}).get('type') in ('turn/start', 'turn/end', 'user/message', 'session/title', 'permission/preset'):
                            self.changed_catalog.set()
            except Exception:
                with self.lock:
                    if not cancel.is_set() and sid in self.states:
                        self.states[sid].connected = False
                        self.dirty.add(sid)
                cancel.wait(2)

    def watch(self, sid, refresh=False):
        if not sid:
            raise ValueError('缺少 Harness 会话标识')
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO watched VALUES(?,?)', (sid, time.time()))
            db.execute('DELETE FROM watched WHERE session NOT IN (SELECT session FROM watched ORDER BY touched DESC LIMIT 8)')
        old_worker = None
        with self.lock:
            if sid in self.followers and (not refresh):
                self.dirty.add(sid)
                return
            if sid in self.followers:
                cancel, old_worker = self.followers.pop(sid)
                cancel.set()
        if old_worker:
            old_worker.join(3)
        with self.lock:
            if refresh:
                self.states[sid] = SessionView(sid)
            self.states.setdefault(sid, SessionView(sid))
            while len(self.followers) >= 8:
                old = next((k for k in self.followers if k != sid and (not any((r['id'] == k and r['status'] == 'active' for r in self.catalog)))), None)
                if old is None:
                    break
                self.followers.pop(old)[0].set()
                self.states.pop(old, None)
                self.view_order.pop(old, None)
                self.dirty.discard(old)
            cancel = threading.Event()
            worker = threading.Thread(target=self.follow, args=(sid, cancel), daemon=True)
            self.followers[sid] = (cancel, worker)
            worker.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and (not self.stop.is_set()):
            with self.lock:
                if self.states[sid].connected:
                    return
            self.stop.wait(0.05)
        raise HarnessError('原 Harness 会话事件订阅尚未建立', 'harness/follow')

    def select_model(self, sid, payload):
        selected = payload.get('model')
        if selected:
            if not any((m['id'] == selected for m in self.models)):
                raise ValueError('该模型不在当前 Harness 原生模型列表中')
            provider, model = selected.split('::', 1)
            request = {'sessionId': sid, 'provider': provider, 'model': model}
            if payload.get('effort'):
                request['reasoningEffort'] = payload['effort']
            return self.rpc('session/selectModel', request)

    def send(self, sid, text, attachments, cid, mode='queue'):
        content = [{'type': 'text', 'text': text}] if text else []
        with self.db() as db:
            for a in attachments:
                row = db.execute('SELECT session,name,mime,size,offset,receipt FROM uploads WHERE id=?', (a['uploadId'],)).fetchone()
                if not row or row[0] != sid or row[3] != row[4] or (not row[5]):
                    raise ValueError('附件不存在、尚未上传完成或不属于该会话')
                if row[5] == 'image':
                    path = self.root / (str(uuid.UUID(a['uploadId'])) + '.upload')
                    content.append({'type': 'image', 'mediaType': row[2], 'data': base64.b64encode(path.read_bytes()).decode(), 'name': row[1]})
                else:
                    content.append({'type': 'file', 'receiptId': row[5]})
        if not content:
            raise ValueError('消息内容不能为空')
        return self.rpc('session/prompt', {'requestId': cid, 'sessionId': sid, 'mode': mode, 'content': content, 'clientTimeZone': 'Asia/Shanghai'})

    def upload(self, sid, p):
        upload = str(uuid.UUID(p['uploadId']))
        size, offset = (int(p['size']), int(p['offset']))
        if not 0 <= size <= MAX_UPLOAD or offset < 0:
            raise ValueError('附件超过 16 MiB 或分块偏移无效')
        raw = base64.b64decode(p['data'], validate=True)
        name = Path(p.get('name') or 'file').name.replace('\r', '').replace('\n', '')
        if len(raw) > 512 * 1024 or offset + len(raw) > size:
            raise ValueError('附件分块大小无效')
        target = self.root / (upload + '.upload')
        with self.db() as db:
            row = db.execute('SELECT session,name,mime,size,offset,receipt FROM uploads WHERE id=?', (upload,)).fetchone()
            if not row:
                if offset:
                    raise ValueError('缺少附件首块')
                db.execute('INSERT INTO uploads VALUES(?,?,?,?,?,0,NULL)', (upload, sid, name, p.get('mime', ''), size))
                row = (sid, name, p.get('mime', ''), size, 0, None)
            if row[:4] != (sid, name, p.get('mime', ''), size) or row[4] != offset:
                raise ValueError('附件分块顺序或所属会话不匹配')
            with target.open('ab' if offset else 'wb') as stream:
                stream.write(raw)
            db.execute('UPDATE uploads SET offset=? WHERE id=?', (offset + len(raw), upload))
        if p.get('eof'):
            if offset + len(raw) != size:
                raise ValueError('附件未完整上传')
            if p.get('mime') in ('image/png', 'image/jpeg', 'image/webp', 'image/gif'):
                receipt = {'receiptId': 'image'}
            else:
                receipt = self.rpc('fileUploads/upload', agentId=sid, request={'data': base64.b64encode(target.read_bytes()).decode(), 'name': name})
            with self.db() as db:
                db.execute('UPDATE uploads SET receipt=? WHERE id=?', (receipt['receiptId'], upload))
        return {'uploadId': upload, 'name': name, 'mime': p.get('mime', ''), 'size': size}

    def execute(self, command):
        action = command['action']
        payload = json.loads(command.get('text') or '{}') if action == 'native_read' else {}
        operation = payload.get('operation')
        if action == 'native_read' and payload.get('cache') is True and (operation in ('permissions', 'tools', 'metadata')):
            sid = command['thread']
            with self.lock:
                if not any((row['id'] == sid for row in self.catalog)):
                    raise ValueError('没有找到目标对话')
                key = (id(self.client), self.generation, self.native_cache_connection, self.connected, sid, operation)
            return self.native_feature_cache.read(key, payload, lambda: self._execute(command), NATIVE_READ_TTLS[operation])
        if action in ('native_action', 'update_permissions', 'update_settings') or operation == 'refresh_thread':
            self.native_feature_cache.invalidate()
            try:
                return self._execute(command)
            finally:
                self.native_feature_cache.invalidate()
        return self._execute(command)

    def _execute(self, command):
        action, sid, cid = (command['action'], command['thread'], command['id'])
        text = command.get('text', '')
        p = json.loads(text) if text and action not in ('send', 'steer') else {}
        if action not in ACTIONS:
            raise ValueError('该功能尚未接入 Harness 原生接口；未执行任何替代操作')
        result = {}
        if action in ('watch', 'attach_thread'):
            self.watch(sid, bool(p.get('refresh')))
        elif action == 'native_read':
            if p.get('operation') == 'permissions':
                self.watch(sid)
                return {'ok': True, **self.permission_state(sid)}
            if p.get('operation') == 'tools':
                return {'ok': True, **self.tools_state(sid)}
            if p.get('operation') == 'metadata':
                values = self.rpc('session/projections', {'sessionId': sid})['values']
                return {'ok': True, 'applicationId': APP_ID, 'metadata': public_metadata(values)}
            if p.get('operation') != 'refresh_thread':
                raise ValueError('该原生设置功能尚未接入 Harness')
            self.watch(sid, refresh=True)
            self.models = public_models(self.rpc('session/modelCatalog'))
            result = {'refreshed': True}
        elif action == 'native_action':
            result = self.set_native_plugin(p)
        elif action == 'update_permissions':
            self.watch(sid)
            before = self.permission_state(sid)
            preset = p.get('preset')
            if preset not in {o['value'] for o in before['options']}:
                raise ValueError('该权限模式不在 Harness 原生权限目录中')
            if p.get('expectedPreset') is not None and p['expectedPreset'] != before['currentValue']:
                raise ValueError('权限已在原 App 改变，请刷新后重新选择')
            if preset != before['currentValue'] and preset in ('danger-full-access', 'auto') and (p.get('confirmRisk') is not True):
                raise ValueError('请确认原生高权限模式的风险后再应用')
            if preset != before['currentValue']:
                execution = self.rpc('commands/execute', agentId=sid, line='/permission ' + preset, submittedAttachments=[])
                if not execution or execution.get('result', {}).get('kind') != 'success':
                    raise HarnessError((execution or {}).get('result', {}).get('text') or 'Harness 原生权限命令未成功', 'harness/permission')
            after = self.permission_state(sid)
            if after['currentValue'] != preset:
                raise HarnessError('原 App 尚未确认权限变化；不会自动重复修改', 'harness/permission', True)
            with self.lock:
                self.states[sid].projections['permissions'] = {'currentValue': after['currentValue']}
                self.states[sid].revision += 1
                self.dirty.add(sid)
            result = {'permissionPreset': after['currentValue']}
        elif action == 'mark_read':
            with self.lock, self.db() as db:
                state = self.states.get(sid)
                receipt = str(state.cursor) if state else ''
                if not state or p.get('readReceipt') not in (None, receipt):
                    return {'ok': True, 'read': False}
                db.execute('INSERT OR REPLACE INTO read_state VALUES(?,?)', (sid, state.cursor))
                db.execute('UPDATE unread_state SET unread=0 WHERE session=?', (sid,))
                for r in self.catalog:
                    if r['id'] == sid:
                        r['unread'] = False
                self.dirty.add(sid)
            result = {'read': True, 'scope': 'panel'}
        elif action in ('send', 'steer'):
            attachments = []
            if text.startswith('{'):
                try:
                    message = json.loads(text)
                    if message.get('relayMessageVersion') == 1:
                        text, attachments = (message.get('text', ''), message.get('attachments', []))
                except ValueError:
                    pass
            result = self.send(sid, text, attachments, cid, 'steer' if action == 'steer' else 'queue')
        elif action == 'stop_turn':
            result = self.rpc('session/cancel', {'sessionId': sid})
        elif action == 'new_thread':
            request = {}
            if p.get('projectId'):
                request['workspaceId'] = p['projectId']
            else:
                request['cwd'] = self.cfg.get('default_cwd') or (self.workspace['items'][0]['path'] if self.workspace['items'] else str(Path.home() / 'Documents' / 'deepseek-harness' / 'default-workspace'))
            created = self.rpc('session/create', request)
            sid = created['sessionId']
            if not isinstance(sid, str) or not sid:
                raise HarnessError('Harness 未确认新会话标识；请先核对原 App', 'harness/protocol', True)
            result = {'ok': True, 'threadId': sid, 'initialMessagePending': bool(p.get('prompt')), 'initialMessageOutcomeUnknown': False, 'creationStage': 'created'}
            sending = False
            try:
                self.save_creation_receipt(cid, result)
                self.select_model(sid, p)
                self.watch(sid)
                if p.get('prompt'):
                    result.update(creationStage='sending', initialMessageOutcomeUnknown=True)
                    self.save_creation_receipt(cid, result)
                    sending = True
                    self.send(sid, p['prompt'], [], cid)
                result.update(creationStage='ready', initialMessagePending=False, initialMessageOutcomeUnknown=False)
            except Exception as error:
                result.update(initialMessageWarning=str(error), initialMessageOutcomeUnknown=sending and bool(getattr(error, 'unknown', True)))
        elif action == 'rename_thread':
            result = self.rpc('session/rename', {'sessionId': sid, 'title': p.get('title') or p.get('name')})
        elif action == 'fork_thread':
            request = {'sessionId': sid}
            if p.get('nativeSeq') is not None:
                request['atSeq'] = int(p['nativeSeq'])
            elif p.get('lastTurnId'):
                raise ValueError('缺少原生消息序号，请刷新对话后从该消息创建分支')
            result = {'threadId': self.rpc('session/fork', request)['sessionId']}
        elif action in ('archive', 'unarchive'):
            result = self.rpc('workspace/' + ('archiveSession' if action == 'archive' else 'unarchiveSession'), {'sessionId': sid})
        elif action.startswith('project_'):
            request = {'path': p.get('root') or p.get('path')} if action == 'project_create' else {'workspaceId': p['projectId']}
            if action == 'project_rename':
                request['title'] = p['name']
            result = self.rpc('workspace/' + action.removeprefix('project_'), request)
            if action == 'project_create':
                w = result['workspace']
                if p.get('name') and p['name'] != w['title']:
                    w = self.rpc('workspace/rename', {'workspaceId': w['workspaceId'], 'title': p['name']})['workspace']
                result = {'project': {'id': w['workspaceId'], 'name': w['title'], 'roots': [w['path']]}}
        elif action == 'update_settings':
            result = self.select_model(sid, p) or {}
        elif action.startswith('queue_'):
            kind = {'queue_edit': 'edit', 'queue_delete': 'remove', 'queue_send_now': 'steer'}[action]
            change = {'kind': kind}
            if kind == 'edit':
                projection = self.rpc('session/projections', {'sessionId': sid})
                values = projection.get('values', {}) if isinstance(projection, dict) else {}
                inbox = values.get('inbox', {}) if isinstance(values, dict) else {}
                original = next((message for group in inbox.values() if isinstance(group, list) for message in group if isinstance(message, dict) and str(message.get('id', '')) == str(p.get('messageId', ''))), None) if isinstance(inbox, dict) else None
                if original is None:
                    raise ValueError('这条队列消息已发送或已被删除')
                if not queue_text_editable(original):
                    raise ValueError(QUEUE_ATTACHMENT_EDIT_REASON)
                if not str(p.get('text', '')).strip():
                    raise ValueError('队列消息不能同时没有文字和附件')
                change['content'] = [{'type': 'text', 'text': p['text']}]
            result = self.rpc('session/updateQueue', {'sessionId': sid, 'itemId': p['messageId'], 'action': change})
        elif action == 'load_older':
            self.watch(sid)
            with self.lock:
                state = self.states[sid]
                through = state.cursor
            request = {'address': {'kind': 'session', 'sessionId': sid}, 'throughSeq': through, 'turnWindow': {'minMessages': 5, 'minTurns': min(5, max(1, int(p.get('limit', 5))))}}
            if p.get('cursor') is not None:
                request['beforeSeq'] = int(p['cursor'])
            page = self.rpc('session/page', request)
            parsed = SessionView(sid)
            parsed.events = {r['event']['seq']: r['event'] for r in page['records'] if r['type'] == 'event'}
            items = parsed.items()
            with self.lock:
                for item in items:
                    for asset in item.get('attachments', []):
                        self.assets[sid, asset['id']] = asset
            result = {'items': items, 'nextCursor': str(min(parsed.events)) if page['hasMore'] and parsed.events else None}
        elif action == 'upload_asset_chunk':
            result = self.upload(sid, p)
        elif action == 'attach_local_file':
            path = Path(p['path']).resolve(strict=True)
            if not path.is_file() or path.stat().st_size > MAX_UPLOAD:
                raise ValueError('只支持不超过 16 MiB 的文件')
            upload = str(uuid.uuid4())
            mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            if mime in ('image/png', 'image/jpeg', 'image/webp', 'image/gif'):
                (self.root / (upload + '.upload')).write_bytes(path.read_bytes())
                receipt = {'receiptId': 'image'}
            else:
                receipt = self.rpc('fileUploads/upload', agentId=sid, request={'data': base64.b64encode(path.read_bytes()).decode(), 'name': path.name})
            with self.db() as db:
                db.execute('INSERT INTO uploads VALUES(?,?,?,?,?,?,?)', (upload, sid, path.name, mime, path.stat().st_size, path.stat().st_size, receipt['receiptId']))
            result = {'uploadId': upload, 'name': path.name, 'size': path.stat().st_size, 'mime': mime}
        elif action == 'resolve_request':
            self.watch(sid)
            with self.lock:
                approval = self.approvals.get(p['requestId'])
            if approval:
                if approval['agentId'] != sid:
                    raise ValueError('授权请求不属于该会话')
                if approval['event'] == 'user-questions/request':
                    outcome = {'answers': self.question_answers(approval['request']['questions'], p)}
                else:
                    decision = p.get('response', {}).get('decision')
                    outcome = {'accept': 'allowed-once', 'decline': 'rejected', 'cancel': 'rejected'}.get(decision)
                    if outcome is None:
                        raise ValueError('Harness 原生仅支持单次允许或拒绝')
                self.client.rpc('$events/result', {'clientId': approval['clientId'], 'eventId': p['requestId'], 'outcome': {'kind': 'result', 'value': outcome}})
                with self.lock:
                    self.approvals.pop(p['requestId'], None)
                    self.states[sid].revision += 1
                    self.dirty.add(sid)
                return {'ok': True}
            with self.lock:
                questions = self.states[sid].projections.get('userQuestions', {}).get('active', [])
                question = next((q for q in questions if q['callId'] == p['requestId'] and q['state'] == 'continued'), None)
            if not question:
                raise ValueError('问题已结束，或原生回答通道尚未就绪，请刷新重试')
            answers = self.question_answers(question['questions'], p)
            accepted = self.rpc('userQuestions/answer', agentId=sid, callId=question['callId'], answer={'answers': answers})
            if not accepted:
                raise ValueError('原 Harness 未接受该回答')
        elif action == 'fetch_asset':
            identity = p.get('assetId') or p.get('id')
            with self.lock:
                reference = self.assets.get((sid, identity))
                if reference is None and sid in self.states:
                    reference = next((v for item in self.states[sid].items() for v in item.get('attachments', []) if v['id'] == identity), None)
            if not reference:
                raise ValueError('附件不属于已读取的原 Harness 会话')
            if reference.get('kind') == 'file':
                match = re.fullmatch('sha256:([a-f0-9]{64})', identity)
                if not match:
                    raise ValueError('Harness 原生文件引用格式未知')
                digest = match[1]
                root = (self.client.home / 'attachments' / 'v1' / 'file-objects').resolve()
                path = (root / digest[:2] / digest).resolve(strict=True)
                if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
                    raise ValueError('附件文件越界或过大')
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != digest:
                    raise ValueError('Harness 原生附件完整性校验失败')
                mime = mimetypes.guess_type(reference['name'])[0] or 'application/octet-stream'
            else:
                asset = self.rpc('session/attachment', {'sessionId': sid, 'attachmentId': identity})
                raw = base64.b64decode(asset['data'], validate=True)
                mime = asset['attachment']['mediaType']
            offset, limit = (int(p.get('offset', 0)), min(2 * 1024 * 1024, int(p.get('limit', 2 * 1024 * 1024))))
            if offset < 0 or limit <= 0:
                raise ValueError('无效的附件分块范围')
            end = min(len(raw), offset + limit)
            result = {'data': base64.b64encode(raw[offset:end]).decode(), 'offset': offset, 'nextOffset': end, 'eof': end >= len(raw), 'asset': {**reference, 'mime': mime}}
        self.changed_catalog.set()
        return {'ok': True, **result} if isinstance(result, dict) else {'ok': True}

    @staticmethod
    def question_answers(questions, payload):
        incoming = payload.get('response', {}).get('answers', {})
        answers = []
        for question in questions:
            values = incoming.get(question['id'], {}).get('answers', [])
            labels = {o['label'] for o in question.get('options', [])}
            answers.append({'id': question['id'], 'selected': [v for v in values if v in labels], 'custom': '\n'.join((v for v in values if v not in labels))})
        return answers

    def permission_state(self, sid):
        catalog = self.rpc('permissionPresets/catalog')
        projection = self.rpc('session/projections', {'sessionId': sid})
        current = (projection.get('values', {}).get('permissions') or {}).get('currentValue')
        if not current:
            raise ValueError('该 Harness 会话尚未提供原生权限模式')
        return {'applicationId': APP_ID, 'currentValue': current, 'options': catalog['options']}

    def discover_native_sections(self):
        sections = []
        for section, endpoint in (('permissions', 'permissionPresets/catalog'), ('tools', 'pluginManager/listBundles')):
            try:
                self.rpc(endpoint)
                sections.append(section)
            except HarnessError as error:
                if error.code != 'harness/http' or 'HTTP 404' not in str(error):
                    raise
        if self.metadata_available:
            sections.append('metadata')
        with self.lock:
            self.native_sections = sections

    @staticmethod
    def plugin_row(row, bundle=False):
        meta = row.get('meta') or {}

        def localized(field, fallback=''):
            value = meta.get(field) or row.get(field) or fallback
            if isinstance(value, dict):
                value = value.get('zh-CN') or value.get('zh') or value.get('en') or fallback
            return str(value)[:2000]
        identity = row['name'] if bundle else row['entryId']
        writable = not row.get('readOnlyReason') and (row.get('installed') is True if bundle else bool(row.get('patchId')))
        return {'id': identity, 'name': row.get('name') or row.get('moduleName'), 'label': localized('title', row.get('name') or row.get('moduleName')), 'description': localized('description'), 'enabled': row['enabled'], 'canToggle': writable, 'readOnlyReason': row.get('readOnlyReason') or ('' if writable else 'unavailable'), **({'installed': row.get('installed', False)} if bundle else {'phase': row.get('fiberPhase')})}

    def tools_state(self, sid):
        skills_available = True
        try:
            skills = self.rpc('skills/list', {'sessionId': sid})['skills']
        except HarnessError as error:
            if error.code != 'harness/http' or 'HTTP 404' not in str(error):
                raise
            skills, skills_available = ([], False)
        plugins = self.rpc('pluginManager/listPlugins')
        bundles = self.rpc('pluginManager/listBundles')
        return {'applicationId': APP_ID, 'skillsAvailable': skills_available, 'skills': [{'name': s['name'], 'description': s.get('description', ''), 'path': s.get('path'), 'modelInvocable': s.get('modelInvocable', False), 'canToggle': False, 'invokePrefix': '/'} for s in skills], 'plugins': [self.plugin_row(row) for row in plugins], 'bundles': [self.plugin_row(row, True) for row in bundles], 'notice': '技能使用原生 /技能名；插件使用原 App 管理接口。受保护或未安装的项目不提供开关。插件变更影响这台电脑的 Harness，不仅当前会话；需要重启时会提示，不会自动中断任务。'}

    def set_native_plugin(self, p):
        operation = p.get('operation')
        if operation not in ('harness_plugin_enabled', 'harness_bundle_enabled'):
            raise ValueError('该原生操作尚未接入 Harness')
        if type(p.get('enabled')) is not bool or type(p.get('expectedEnabled')) is not bool:
            raise ValueError('缺少插件开关的原始状态')
        bundle = operation == 'harness_bundle_enabled'
        endpoint = 'pluginManager/' + ('listBundles' if bundle else 'listPlugins')
        rows = self.rpc(endpoint)
        row = next((row for row in rows if (row.get('name') if bundle else row.get('entryId')) == p.get('id')), None)
        if row is None or not self.plugin_row(row, bundle)['canToggle']:
            raise ValueError('该项目不存在、受原生保护或尚未安装；未执行修改')
        if row['enabled'] != p['expectedEnabled']:
            raise ValueError('插件状态已在原 App 改变，请刷新后重新选择')
        if row['enabled'] == p['enabled']:
            return {'enabled': row['enabled']}
        if p.get('confirmGlobal') is not True:
            raise ValueError('请确认变更会影响目标机的 Harness 插件配置')
        result = self.rpc('pluginManager/' + ('setBundleEnabled' if bundle else 'setPluginEnabled'), **{'name' if bundle else 'id': p['id'], 'enabled': p['enabled']})
        self.changed_features.set()
        self.changed_catalog.set()
        self.changed_models.set()
        if result.get('error') or result.get('application') not in ('applied', 'restart-required'):
            raise HarnessError('原 App 未应用插件变更：' + str((result.get('error') or {}).get('code') or result.get('application')), 'harness/plugin')
        warning = '配置已保存，需要重启原 Harness 才能生效；未自动重启。' if result['application'] == 'restart-required' else ''
        if result['application'] == 'applied':
            after = next((row for row in self.rpc(endpoint) if (row.get('name') if bundle else row.get('entryId')) == p['id']), None)
            if after is None or after['enabled'] != p['enabled']:
                raise HarnessError('原 App 尚未确认插件状态；不会重复修改', 'harness/plugin', True)
        warning = '\n'.join([warning, *result.get('warnings', [])]).strip()
        return {'application': result['application'], 'warning': warning}

    def save_creation_receipt(self, cid, result):
        with self.db() as db:
            db.execute('UPDATE commands SET result=? WHERE id=?', (json.dumps(result, ensure_ascii=False), cid))

    def worker(self):
        while not self.stop.is_set():
            try:
                command = self.commands.get(timeout=1)
            except queue.Empty:
                continue
            cid = command['id']
            with self.db() as db:
                existing = db.execute('SELECT result FROM commands WHERE id=?', (cid,)).fetchone()
                if not existing:
                    db.execute('INSERT INTO commands VALUES(?,NULL)', (cid,))
            if existing:
                result = json.loads(existing[0]) if existing[0] else {'ok': False, 'unknown': True, 'error': '操作已接收，尚未确认；不会重复执行'}
            else:
                try:
                    result = self.execute(command)
                except Exception as error:
                    result = {'ok': False, 'unknown': bool(getattr(error, 'unknown', False)), 'error': str(error)}
                with self.db() as db:
                    db.execute('UPDATE commands SET result=? WHERE id=?', (json.dumps(result, ensure_ascii=False), cid))
            with self.lock:
                self.results[cid] = result

    def payload(self):
        with self.lock:
            views = {}
            self.dirty.intersection_update(self.states)
            for sid in sorted(self.dirty, key=lambda sid: self.view_order.get(sid, 0))[:1]:
                if sid in self.states:
                    self.view_order[sid] = time.monotonic()
                    meta = next((r for r in self.catalog if r['id'] == sid), {})
                    views[sid] = self.states[sid].public(meta)
                    views[sid]['readReceipt'] = str(self.states[sid].cursor)
                    for identity, frame in self.approvals.items():
                        if frame['agentId'] == sid:
                            request = frame['request']
                            if frame['event'] == 'user-questions/request':
                                call_id = (request.get('wait') or {}).get('callId')
                                views[sid]['requests'] = [r for r in views[sid]['requests'] if r['id'] != call_id]
                                views[sid]['requests'].append({'id': identity, 'method': 'item/tool/requestUserInput', 'questions': [{**q, 'isMultiSelect': q.get('multiSelect', False), 'question': q.get('question', '') + ('\n\n' + q['detail'] if isinstance(q.get('detail'), str) else '')} for q in request['questions']]})
                                continue
                            tool = next((item for item in views[sid]['items'] if item['id'] == request.get('callId')), {})
                            views[sid]['requests'].append({'id': identity, 'method': 'item/commandExecution/requestApproval', 'command': tool.get('text') or request.get('toolName') or 'Harness 工具', 'commandActions': tool.get('detail'), 'reason': request.get('reason') or '', 'availableDecisions': ['accept', 'decline']})
                    views[sid]['pendingRequests'] = len(views[sid]['requests'])
                    if views[sid]['requests']:
                        views[sid]['executionState']['phase'] = 'waiting_input' if any((r.get('method') == 'item/tool/requestUserInput' for r in views[sid]['requests'])) else 'waiting_approval'
            return {'host': self.cfg['host_id'], 'label': self.cfg['label'], 'applicationId': APP_ID, 'physicalHostId': self.cfg['physical_host_id'], 'desktopConnected': self.connected, 'capabilities': {'actions': sorted(ACTIONS), 'nativeSettings': bool(self.native_sections), 'nativeReadCache': True, 'nativeReadCacheScope': hashlib.sha256(repr((id(self.client), self.generation, self.native_cache_connection)).encode()).hexdigest()[:24], 'nativeSections': list(self.native_sections), 'permissions': 'permissions' in self.native_sections, 'permissionSwitch': False, 'projectRoots': False, 'moveToUnassigned': False, 'launchApp': False, 'nativeDeepLinks': False}, 'catalog': copy.deepcopy(self.catalog), 'projects': copy.deepcopy(self.projects), 'models': copy.deepcopy(self.models), 'views': views, 'results': dict(self.results)}

    def run(self):
        for endpoint in ('session/control', 'workspace/follow'):
            threading.Thread(target=self.global_stream, args=(endpoint,), daemon=True).start()
        threading.Thread(target=self.worker, daemon=True).start()
        threading.Thread(target=self.interaction_stream, daemon=True).start()

        def restore_watches():
            with self.db() as db:
                sessions = [r[0] for r in db.execute('SELECT session FROM watched ORDER BY touched DESC LIMIT 8')]
            for sid in sessions:
                if self.stop.is_set():
                    break
                try:
                    self.watch(sid)
                except Exception:
                    pass
        threading.Thread(target=restore_watches, daemon=True).start()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        next_refresh = 0
        while not self.stop.is_set():
            retry = getattr(self, 'connection_retry', None)
            connecting = retry is not None and (not self.connected)
            if connecting and (not retry.begin('desktop')):
                self.stop.wait(0.5)
                continue
            native_ready = False
            if connecting:
                self.changed_models.set()
                self.changed_catalog.set()
            try:
                now = time.monotonic()
                if self.changed_models.is_set():
                    self.changed_models.clear()
                    models = self.rpc('session/modelCatalog')
                    self.default_model = models.get('default')
                    self.models = public_models(models)
                    self.changed_catalog.set()
                if now >= next_refresh or self.changed_catalog.is_set():
                    self.changed_catalog.clear()
                    self.refresh_catalog()
                    next_refresh = now + 60
                if self.changed_features.is_set():
                    self.changed_features.clear()
                    try:
                        self.discover_native_sections()
                    except Exception:
                        self.changed_features.set()
                        raise
                if connecting:
                    retry.success('desktop')
                native_ready = True
                payload = self.payload()
                raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
                headers = {'Authorization': 'Bearer ' + self.cfg['agent_token'], 'Content-Type': 'application/json'}
                if len(raw) > 32768:
                    raw = gzip.compress(raw, compresslevel=5)
                    headers['Content-Encoding'] = 'gzip'
                req = urllib.request.Request(self.cfg['relay_url'] + '/agent/sync', data=raw, headers=headers)
                with opener.open(req, timeout=15) as reply:
                    response = json.load(reply)
                with self.lock:
                    for sid, view in payload['views'].items():
                        if sid in self.states and self.states[sid].revision == view['revision']:
                            self.dirty.discard(sid)
                    for cid in payload['results']:
                        self.results.pop(cid, None)
                        self.inflight.discard(cid)
                    if self.generation != response.get('generation'):
                        self.generation = response.get('generation')
                        self.dirty.update(self.states)
                    for command in response.get('commands', []):
                        if command['id'] not in self.inflight:
                            self.inflight.add(command['id'])
                            self.commands.put(command)
                self.stop.wait(0.6 if self.dirty or self.inflight else 1.5)
            except Exception as e:
                print('Harness bridge:', type(e).__name__, flush=True)
                if connecting and (not native_ready):
                    retry.failure('desktop')
                    self.changed_models.set()
                    self.changed_catalog.set()
                    self.changed_features.set()
                    self.connected = False
                elif retry is None:
                    self.connected = False
                self.stop.wait(3)
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    HarnessAgent(json.loads(Path(args.config).read_text(encoding='utf-8'))).run()
