"""Commands handled by the original Linux desktop backend at its private socket."""
import json
import threading
import time
import uuid
from pathlib import Path
from crb.codex.linux_desktop_queue import LinuxDesktopQueue
from crb.codex.linux_live_items import LiveViews
from crb.codex import linux_panel_actions as panel
from crb.codex.linux_owner_transport import OwnerRPC, endpoint_for

def connect_desktop(agent):
    from crb.codex.linux_desktop_queue import native_backend
    backend = native_backend(agent.home)
    endpoint = endpoint_for(backend)
    if endpoint is None:
        from crb.codex.linux_stock_desktop import LinuxStockDesktopQueue
        return LinuxStockDesktopQueue(agent, backend)
    desktop = LinuxSharedDesktop(agent, backend)
    desktop.owner = OwnerRPC(desktop.backend, endpoint)
    desktop.owner.request('thread/loaded/list', {'limit': 1})
    desktop.refresh_tools()
    return desktop

class LinuxSharedDesktop(LinuxDesktopQueue):
    supports_side_workbench = True
    direct_actions = {'send', 'steer', 'stop_turn', 'queue_edit', 'queue_reorder', 'queue_delete', 'queue_resume', 'queue_send_now', 'new_thread', 'resolve_request', 'rename_thread', 'archive', 'unarchive', 'fork_thread', 'continue_turn', 'answer_choice', 'question_control'}
    actions = (*LinuxDesktopQueue.actions, 'native_read', 'native_action', 'steer', 'stop_turn', 'queue_edit', 'queue_delete', 'queue_reorder', 'attach_local_file', 'upload_asset_chunk', 'file_read_text', 'file_write_text', 'update_settings', 'update_permissions', 'mark_read', 'queue_resume', 'queue_send_now', 'project_create', 'new_thread', 'resolve_request', 'continue_turn', 'archive', 'unarchive', 'rename_thread', 'fork_thread', 'move_project', 'software_check', 'secure_software_update', 'answer_choice', 'question_control')

    def __init__(self, agent, backend=None):
        super().__init__(agent, backend)
        self.live = LiveViews()
        self.desktop_tools = frozenset()
        self.next_tool_check = 0
        self.native_sidebar = False
        self.form_lock = threading.Lock()
        self.form_actions = ()
        self.question_lock = threading.Lock()
        self.question_controls = {}

    def refresh_tools(self):
        self.next_tool_check = time.monotonic() + 15
        try:
            rows = self.owner.request('mcpServerStatus/list', {'limit': 100}, timeout=5)['data']
            tools = next((r.get('tools') for r in rows if r.get('name') == 'codex_app'), None)
        except (OSError, KeyError, ValueError, RuntimeError):
            tools = None
        if isinstance(tools, dict):
            self.desktop_tools = frozenset(tools)
            if tools:
                self.next_tool_check = time.monotonic() + 300
        self.native_sidebar = {'list_threads', 'list_projects'} <= self.desktop_tools
        optional = {'mark_read': 'set_thread_read_state', 'project_create': 'create_project', 'move_project': 'move_thread_to_project'}
        self.actions = tuple((a for a in type(self).actions if a not in optional or optional[a] in self.desktop_tools))
        from crb.codex.linux_foreground_forms import available
        self.form_actions = ()
        if self.native_sidebar and available(self):
            self.form_actions = ('edit_user_turn', 'project_rename', 'project_delete', 'project_update_roots')
            if 'create_project' not in self.desktop_tools:
                self.form_actions += ('project_create',)
            self.actions += self.form_actions
        self.direct_actions = type(self).direct_actions.union(self.form_actions)

    def poll(self):
        if time.monotonic() >= self.next_tool_check:
            self.refresh_tools()
        super().poll()

    def close(self):
        super().close()
        self.live.close()

    def _observe(self, tid, force):
        if self.owns(tid):
            self.live.watch(self.owner, tid, 5)
        return super()._observe(tid, force)

    def side_history(self, params):
        tid = params['threadId']
        if not params.get('cursor') and self.owns(tid):
            self.live.watch(self.owner, tid, 20)
        result = self.owner.request('thread/turns/list', params)
        return result if params.get('cursor') else self.live.merge(tid, result)

    def desktop_tool(self, tool, args, tid, timeout=60):
        caller = tid
        if not caller or not self.owns(caller):
            loaded = self.owner.request('thread/loaded/list', {'limit': 100})['data']
            with self.agent.lock:
                known = {row['id'] for row in self.agent.catalog}
            caller = next((i for i in loaded if i in known and self.owns(i)), None)
        if not caller:
            raise ValueError('请先在原 App 接入一个对话以使用桌面工具')
        response = self.owner.request('mcpServer/tool/call', {'threadId': caller, 'server': 'codex_app', 'tool': tool, 'arguments': args}, timeout=timeout)
        if response.get('isError'):
            raise RuntimeError('原 App 的桌面工具未确认操作，请刷新核对')
        return {'success': True, 'contentItems': [{'type': 'inputText', 'text': c['text']} for c in response['content'] if c.get('type') == 'text' and isinstance(c.get('text'), str)]}

    def new_chat(self, payload, cid):
        from crb.codex.projects import desktop_project_id, local_projects
        from crb.codex.agent import app_tool_json
        text = payload.get('prompt')
        if not isinstance(text, str) or not text.strip() or len(text) > 32000:
            raise ValueError('首条消息需为 1–32000 字符')
        with self.agent.lock:
            projects = {row['id']: row for row in self.agent.projects}
            models = {row['id']: row for row in self.agent.models}
        project_id = payload.get('projectId')
        project = projects.get(project_id)
        if project_id and (not project):
            raise ValueError('目标项目不存在')
        cwd = str(self.agent.home.parent)
        if project:
            roots = project.get('roots')
            cwd = roots[0] if roots else project.get('path')
        if not cwd or not Path(cwd).is_absolute() or (not Path(cwd).is_dir()):
            raise ValueError('项目目录不可用')
        params = {'cwd': cwd, 'historyMode': 'paginated'}
        if project:
            listed = local_projects(app_tool_json(self.desktop_tool('list_projects', {}, None)))
            desktop_project_id(self.agent.home, project, listed, projects.values())
            params['projectId'] = project_id
        model, effort, tier = (payload.get(key) for key in ('model', 'effort', 'serviceTier'))
        if model:
            if model not in models:
                raise ValueError('目标机没有此模型')
            params['model'] = model
        if effort and (not model or effort not in {e['id'] for e in models[model].get('efforts', [])}):
            raise ValueError('此模型不支持所选推理强度')
        if tier is not None:
            if not model or tier not in {t['id'] for t in models[model].get('serviceTiers', [])}:
                raise ValueError('此模型不支持所选加速模式')
            params['serviceTier'] = tier
        if 'fullAccess' in payload:
            full = payload['fullAccess']
            if type(full) is not bool:
                raise ValueError('完全访问设置无效')
            profile = ':danger-full-access' if full else ':workspace'
            profiles = self.owner.request('permissionProfile/list', {'cwd': cwd, 'limit': 100})['data']
            if not any((p['id'] == profile and p.get('allowed') for p in profiles)):
                raise ValueError('原 App 不允许选择此权限配置')
            params.update(permissions=profile, approvalPolicy='never' if full else 'on-request', approvalsReviewer='user')
        cid = str(uuid.UUID(cid))
        sock = self.owner.open()
        result = None
        try:
            row = self.owner.call(sock, 'thread/start', params)['thread']
            tid = str(uuid.UUID(row['id']))
            result = {'ok': True, 'threadId': tid, 'initialMessagePending': True, 'initialMessageOutcomeUnknown': False, 'transport': 'desktop-native-first-turn'}
            if project and row.get('projectId') != project_id:
                raise RuntimeError('原 App 未确认项目归属，首条消息尚未提交')
            args = {'threadId': tid, 'clientUserMessageId': cid, 'input': [{'type': 'text', 'text': text}]}
            if effort:
                args['effort'] = effort
            result['initialMessageOutcomeUnknown'] = True
            turn = self.owner.call(sock, 'turn/start', args)['turn']
            result.update(turnId=str(uuid.UUID(turn['id'])), initialMessagePending=False, initialMessageOutcomeUnknown=False)
        except Exception as exc:
            if result is None:
                raise
            result['initialMessageWarning'] = str(exc)
        finally:
            sock.close(timeout=0.5)
        try:
            self.agent.read_catalog()
        except (OSError, RuntimeError) as exc:
            result['catalogWarning'] = str(exc)
        return result

    def create_side_chat(self, parent):
        if not self.owns(parent):
            raise ValueError('请先接入主对话')
        result = self.owner.request('thread/fork', {'threadId': parent, 'attachToSource': True, 'excludeTurns': True})
        if result['thread'].get('attachedToThreadId') != parent:
            raise RuntimeError('原 App 未确认侧聊的主对话，请刷新列表核对')
        return result

    def control_question(self, tid, turn, question, operation):
        from crb.codex.linux_question_window import control_question
        with self.question_lock:
            return control_question(self, tid, turn, question, operation)

    def send_side(self, parent, child, message, cid):
        row = self.owner.request('thread/read', {'threadId': child})['thread']
        if child == parent or row.get('attachedToThreadId') != parent:
            raise ValueError('侧聊与主对话不匹配')
        result = self.owner.request('thread/resume', {'threadId': child, 'excludeTurns': True})
        if result['thread'].get('id') != child or result['thread'].get('attachedToThreadId') != parent:
            raise RuntimeError('原 App 未确认当前侧聊')
        return self.send_inputs(child, str(uuid.UUID(cid)), [{'type': 'text', 'text': message}], 'send')

    def send_inputs(self, tid, cid, inputs, action):
        self.live.watch(self.owner, tid, 8)
        recent = self.owner.request('thread/turns/list', {'threadId': tid, 'limit': 1, 'itemsView': 'summary', 'sortDirection': 'desc'})['data']
        active = recent and recent[0].get('status') == 'inProgress'
        if action == 'steer':
            if not active:
                raise ValueError('当前没有运行中的轮次')
            result = self.owner.request('turn/steer', {'threadId': tid, 'expectedTurnId': recent[0]['id'], 'clientUserMessageId': cid, 'input': inputs})
            if result.get('turnId') != recent[0]['id']:
                raise RuntimeError('原 App 未确认目标轮次')
            return {'ok': True, 'turnId': result['turnId'], 'clientUserMessageId': cid}
        if active:
            queued = self.owner.request('thread/queue/add', {'threadId': tid, 'clientUserMessageId': cid, 'input': inputs})['queuedSubmission']
            if queued.get('clientUserMessageId') != cid:
                raise RuntimeError('队列回执不匹配，请先核对原 App')
            return {'ok': True, 'queued': True, 'messageId': cid, 'threadId': tid}
        turn = self.owner.request('turn/start', {'threadId': tid, 'clientUserMessageId': cid, 'input': inputs})['turn']
        return {'ok': True, 'turnId': str(uuid.UUID(turn['id'])), 'clientUserMessageId': cid, 'threadId': tid}

    def queue_rows(self, tid):
        return self.owner.request('thread/queue/list', {'threadId': tid})['data']

    def public_queue(self, tid):
        rows = []
        for row in self.queue_rows(tid):
            inputs = row['input']
            files = [item for item in inputs if item.get('type') != 'text']
            rows.append({'id': row['clientUserMessageId'], 'text': '\n'.join((item.get('text', '') for item in inputs if item.get('type') == 'text')), 'attachments': [{'kind': 'image' if item['type'] in ('image', 'localImage') else 'file', 'name': item.get('name') or '附件'} for item in files], 'canEdit': not files})
        return rows

    def execute(self, command):
        action, tid = (command['action'], command['thread'])
        if action in self.form_actions:
            from crb.codex.linux_foreground_forms import edit_message, project_action
            payload = json.loads(command.get('text') or '{}')
            if not self.form_lock.acquire(blocking=False):
                raise ValueError('原 App 正在处理另一项前台操作')
            try:
                if action == 'edit_user_turn':
                    return edit_message(self, tid, payload)
                if action == 'project_rename' and payload.get('mode') == 'roots':
                    action = 'project_update_roots'
                from crb.codex.native_features import NativeFeatures
                NativeFeatures(self.agent).cache().invalidate()
                result = project_action(self, action, payload)
                self.agent.read_catalog()
                return result
            finally:
                self.form_lock.release()
        if action == 'attach_thread':
            with self.agent.lock:
                if not any((row['id'] == tid for row in self.agent.catalog)):
                    raise ValueError('目标不在本机对话目录中')
            if not self.owns(tid):
                row = self.owner.request('thread/resume', {'threadId': tid, 'excludeTurns': True})['thread']
                if row.get('id') != tid or not self.owns(tid):
                    raise RuntimeError('原 App 尚未确认接入这条对话')
            self.observe(tid, force=True)
            return {'ok': True, 'threadId': tid, 'transport': 'desktop-native-resume'}
        if action == 'new_thread':
            return self.new_chat(json.loads(command.get('text') or '{}'), command['id'])
        if action not in self.direct_actions:
            return super().execute(command)
        if action in ('archive', 'unarchive', 'rename_thread', 'fork_thread'):
            with self.agent.lock:
                if not any((r['id'] == tid for r in self.agent.catalog)):
                    raise ValueError('目标不在本机对话目录中')
            return panel.manage_thread(self, tid, action, json.loads(command.get('text') or '{}'))
        if not self.owns(tid):
            raise ValueError('请先接入这条原桌面对话')
        if action == 'continue_turn':
            return panel.continue_turn(self, tid, json.loads(command.get('text') or '{}'))
        if action == 'question_control':
            return panel.question_control(self, tid, json.loads(command.get('text') or '{}'))
        if action == 'answer_choice':
            with self.lock:
                return panel.answer_choice(self, tid, str(uuid.UUID(command['id'])), json.loads(command['text']))
        raw = command.get('text') or ''
        if action == 'resolve_request':
            return self.live.respond(tid, json.loads(raw or '{}'))
        if action in ('send', 'steer'):
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = None
            message, files = (raw, [])
            if isinstance(payload, dict) and payload.get('relayMessageVersion') == 1:
                message, files = (payload.get('text', ''), payload.get('attachments', []))
            if not isinstance(message, str) or len(message) > 32000:
                raise ValueError('消息长度无效')
            inputs = [{'type': 'text', 'text': message}] if message.strip() else []
            attachments, _ = self.agent.resolve_uploaded_inputs(tid, files)
            inputs.extend(attachments)
            if not inputs:
                raise ValueError('请输入消息或添加附件')
            return self.send_inputs(tid, str(uuid.UUID(command['id'])), inputs, action)
        if action == 'stop_turn':
            latest = self.owner.request('thread/turns/list', {'threadId': tid, 'limit': 1, 'itemsView': 'summary', 'sortDirection': 'desc'})['data']
            if not latest or latest[0]['status'] != 'inProgress':
                raise ValueError('当前没有正在运行的任务')
            self.owner.request('turn/interrupt', {'threadId': tid, 'turnId': latest[0]['id']})
            return {'ok': True, 'interruptedTurnId': latest[0]['id']}
        payload = json.loads(raw)
        by_id = {row['clientUserMessageId']: row for row in self.queue_rows(tid)}
        if action in ('queue_send_now', 'queue_resume'):
            ids = list(by_id)
            if action == 'queue_resume' and (not ids or payload.get('messageIds') != ids):
                raise ValueError('队列已变化，请刷新后重试')
            key = ids[0] if action == 'queue_resume' else payload.get('messageId')
            if key not in by_id:
                raise ValueError('消息已移出队列')
            result = self.owner.request('thread/queue/start', {'threadId': tid, 'queuedSubmissionId': by_id[key]['id']})
            turn_id = str(uuid.UUID(result['turn']['id']))
            return {'ok': True, 'messageId': key, 'turnId': turn_id, 'threadId': tid, 'resumed': action == 'queue_resume', 'sentNow': action == 'queue_send_now', 'pending': False}
        if action == 'queue_reorder':
            ids = payload.get('messageIds')
            if not isinstance(ids, list) or len(ids) != len(by_id) or set(ids) != set(by_id):
                raise ValueError('队列已变化，请刷新后重试')
            self.owner.request('thread/queue/reorder', {'threadId': tid, 'queuedSubmissionIds': [by_id[i]['id'] for i in ids]})
        else:
            row = by_id.get(payload.get('messageId'))
            if not row:
                raise ValueError('消息已移出队列')
            params = {'threadId': tid, 'queuedSubmissionId': row['id']}
            if action == 'queue_delete':
                if not self.owner.request('thread/queue/delete', params).get('deleted'):
                    raise RuntimeError('原 App 未确认删除')
            else:
                text = payload.get('text')
                if not isinstance(text, str) or not text.strip() or len(text) > 32000 or any((i['type'] != 'text' for i in row['input'])):
                    raise ValueError('只支持编辑有效的纯文字队列消息')
                confirmed = self.owner.request('thread/queue/update', {**params, 'input': [{'type': 'text', 'text': text}]})
                if confirmed['queuedSubmission']['id'] != row['id']:
                    raise RuntimeError('队列回执不匹配')
        return {'ok': True, 'threadId': tid}
