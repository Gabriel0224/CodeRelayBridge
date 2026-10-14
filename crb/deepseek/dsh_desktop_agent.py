"""Version-specific native bridge for the existing 服务器 DSH Desktop."""
import copy
import json
from pathlib import Path
from crb.deepseek.dsh_desktop_client import DesktopHarnessClient
from crb.deepseek.harness_agent import HarnessAgent, SessionView, ACTIONS, PUBLIC_EVENTS, public_metadata
from crb.deepseek.harness_client import HarnessError
DESKTOP_ACTIONS = ACTIONS - {'unarchive', 'move_project', 'native_action', 'attach_local_file'}

class DesktopSessionView(SessionView):

    def chunk(self, chunk):
        if chunk.get('type') == 'block-start' and chunk.get('blockType') == 'tool-call':
            self.blocks[chunk.get('index', 0)] = {'type': 'tool-call', 'arguments': ''}
        else:
            super().chunk(chunk)

    def native_chunk(self, event):
        data = event['data']
        identity = str(data['turn']) + ':' + str(data['step'])
        if not self.attempt or self.attempt['attemptId'] != identity:
            self.attempt = {'attemptId': identity, 'turn': data['turn'], 'startedAfterSeq': event['seq'] - 1}
            self.blocks = {}
        self.chunk(data['chunk'])

    def apply(self, frame):
        kind = frame['type']
        if kind == 'legacy-projection':
            self.projections[frame['key']] = copy.deepcopy(frame['value'])
        elif kind == 'legacy-inbox':
            self.projections['inbox'] = {'queued': [], 'steering': [], 'context': []}
            for row in frame['items']:
                self.projections['inbox'][row['placement']].append(row['message'])
        elif kind == 'snapshot':
            super().apply(frame)
            for row in frame['records']:
                event = row['event']
                if event['type'] == 'assistant/chunk':
                    self.native_chunk(event)
                elif event['type'] in ('assistant/message', 'assistant/attempt', 'turn/end'):
                    self.attempt, self.blocks = (None, {})
            return True
        elif kind == 'event':
            event = frame['event']
            if event['seq'] <= self.cursor:
                return False
            if event['type'] == 'assistant/chunk':
                self.native_chunk(event)
            elif event['type'] in ('assistant/message', 'assistant/attempt', 'turn/end'):
                self.attempt, self.blocks = (None, {})
            return super().apply(frame)
        else:
            return super().apply(frame)
        self.connected = True
        self.revision += 1
        return True

class DesktopHarnessAgent(HarnessAgent):

    def __init__(self, cfg, client=None):
        super().__init__(cfg, client or DesktopHarnessClient(cfg['harness_home'], cfg['desktop_runtime']))

        def reads():
            with self.db() as db:
                return dict(db.execute('SELECT session,seq FROM read_state'))
        self.client.read_receipts = reads

    def watch(self, sid, refresh=False):
        previous = None
        with self.lock:
            if refresh and sid in self.followers:
                cancelled, previous = self.followers.pop(sid)
                cancelled.set()
        if previous:
            previous.join(3)
        with self.lock:
            if refresh or sid not in self.states:
                self.states[sid] = DesktopSessionView(sid)
        return super().watch(sid, False)

    def permission_state(self, sid):
        projection = self.rpc('session/projections', {'sessionId': sid})
        permissions = projection.get('values', {}).get('permissions') or {}
        if not permissions.get('currentValue') or not permissions.get('options'):
            raise HarnessError('该原生会话未提供权限目录', 'harness/permissions')
        return {'applicationId': 'deepseek', **permissions}

    def discover_native_sections(self):
        self.client.native_rpc('pluginInventory/list', {}, remote=True)
        with self.lock:
            self.native_sections = ['permissions', 'tools'] + (['metadata'] if self.metadata_available else [])

    def tools_state(self, sid):
        self.watch(sid)
        skills = self.rpc('skills/list', {'sessionId': sid})['skills']
        entries = self.client.native_rpc('pluginInventory/list', {}, remote=True)['entries']
        return {'applicationId': 'deepseek', 'skillsAvailable': True, 'skills': [{'name': s['name'], 'description': s.get('description', ''), 'modelInvocable': s.get('modelInvocable', False), 'canToggle': False, 'invokePrefix': '/'} for s in skills], 'plugins': [{**row, 'id': row['entryId'], 'name': row['moduleName'], 'label': row['moduleName'], 'canToggle': False, 'readOnlyReason': '此版本的原生插件接口仅支持查看', 'phase': row.get('fiberPhase')} for row in entries], 'bundles': [], 'notice': '同步原 DSH Desktop 的技能与插件目录；该版本原生插件接口只读，不提供无效开关。'}

    def payload(self):
        payload = super().payload()
        payload['capabilities']['actions'] = sorted(DESKTOP_ACTIONS)
        payload['capabilities']['attachmentMimeTypes'] = ['image/png', 'image/jpeg', 'image/webp', 'image/gif']
        return payload

    def execute(self, command):
        action, sid = (command['action'], command['thread'])
        if action not in DESKTOP_ACTIONS:
            raise ValueError('此版本原 DSH Desktop 不支持该操作；未执行替代操作')
        if action == 'resolve_request':
            p = json.loads(command['text'])
            with self.lock:
                approval = self.approvals.get(p['requestId'])
            if not approval or approval['agentId'] != sid:
                raise ValueError('原生问题已结束或不属于该会话')
            native = approval['request']
            if approval['event'] == 'user-questions/request':
                response = {'sessionId': sid, 'answer': {'answers': self.question_answers(native['questions'], p)}}
            else:
                decision = p.get('response', {}).get('decision')
                outcome = {'accept': 'allowed-once', 'decline': 'rejected', 'cancel': 'rejected'}.get(decision)
                if not outcome:
                    raise ValueError('原生授权仅接受单次允许或拒绝')
                response = {'sessionId': sid, 'approvalId': native['approvalId'], 'outcome': outcome}
            self.client.respond(p['requestId'], response)
            return {'ok': True}
        if action == 'upload_asset_chunk':
            p = json.loads(command['text'])
            if p.get('mime') not in ('image/png', 'image/jpeg', 'image/webp', 'image/gif'):
                raise ValueError('此版本原生支持图片附件，不支持通用文件上传')
        return super().execute(command)

    def interaction_stream(self):
        while not self.stop.is_set():
            try:
                for frame in self.client.stream('$events', stop=self.stop):
                    kind = frame['type']
                    if kind == 'ready':
                        self.changed_models.set()
                        self.changed_features.set()
                    elif kind == 'emit' and frame.get('event') in ('llm/adapters-updated', 'settings/document-updated', 'credentials/reference-updated'):
                        self.changed_models.set()
                        self.changed_catalog.set()
                    elif kind in ('waterfall', 'cancel', 'legacy-approval-resolved'):
                        with self.lock:
                            if kind == 'waterfall':
                                self.approvals[frame['eventId']] = {**frame, 'clientId': 'dsh-desktop-native'}
                                affected = [frame['agentId']]
                            else:
                                affected = []
                                for identity, old in list(self.approvals.items()):
                                    if kind == 'cancel' and identity == frame['eventId'] or (kind == 'legacy-approval-resolved' and old['request'].get('approvalId') == frame['approvalId']):
                                        affected.append(old['agentId'])
                                        self.approvals.pop(identity, None)
                            for sid in affected:
                                if sid in self.states:
                                    self.states[sid].revision += 1
                                    self.dirty.add(sid)
            except Exception:
                with self.lock:
                    self.approvals.clear()
                    for state in self.states.values():
                        state.revision += 1
                        self.dirty.add(state.sid)
                self.stop.wait(2)
