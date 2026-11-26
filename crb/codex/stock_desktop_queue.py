"""Official persisted chat/queue operations targeting the user's running desktop."""
import json
import os
from pathlib import Path
import subprocess
import sqlite3
from contextlib import closing
import sys
import time
import threading
from uuid import UUID
from crb.codex.linux_desktop_queue import LinuxDesktopQueue
from crb.codex.native_queue_owner import owns_thread, stock_backend

class StockDesktopQueue(LinuxDesktopQueue):
    actions = (*LinuxDesktopQueue.actions, 'new_thread', 'native_read', 'native_action', 'file_read_text', 'file_write_text')
    direct_actions = {'new_thread'}
    supports_side_workbench = True

    def __init__(self, agent):
        self.window, backend = stock_backend(agent.home)
        self.side_lock = threading.Lock()
        super().__init__(agent, backend)
        agent.appserver.request('thread/list', {'limit': 1}, timeout=5)

    @property
    def connected(self):
        return super().connected and self.window.is_running()

    def owns(self, tid):
        return self.connected and owns_thread(self.backend, self.agent.home, tid)

    def open_thread(self, tid):
        uri = 'codex://threads/' + str(UUID(tid))
        if sys.platform == 'darwin':
            subprocess.run(['/usr/bin/open', '-b', 'com.openai.codex', uri], check=True, timeout=8)
        else:
            os.startfile(uri)

    def attach(self, tid):
        if not self.owns(tid):
            self.open_thread(tid)
            deadline = time.monotonic() + 16
            while not self.owns(tid):
                if time.monotonic() >= deadline:
                    raise RuntimeError('原 Codex 尚未接入这个对话')
                time.sleep(0.35)

    def public_queue(self, tid):
        rows = self.agent.appserver.request('thread/queue/list', {'threadId': tid})['data']
        return [{'id': row['clientUserMessageId'], 'text': '\n'.join((item.get('text', '') for item in row.get('input', []) if item.get('type') == 'text')), 'canEdit': False} for row in rows]

    def new_chat(self, command):
        body = json.loads(command['text'])
        prompt = body.get('prompt')
        if not isinstance(prompt, str) or not 0 < len(prompt.strip()) <= 32000:
            raise ValueError('请输入 1 至 32000 字的首条消息')
        with self.agent.lock:
            project_id = body.get('projectId')
            project = next((p for p in self.agent.projects if p['id'] == project_id), None)
            model = next((m for m in self.agent.models if m['id'] == body.get('model')), None)
        if project_id and (not project):
            raise ValueError('未找到项目')
        settings = {}
        if body.get('model'):
            if not model:
                raise ValueError('目标机没有此模型')
            settings['model'] = model['id']
        for field, options in [('effort', 'efforts'), ('serviceTier', 'serviceTiers')]:
            value = body.get(field)
            if value:
                if not model or value not in {r['id'] for r in model.get(options, [])}:
                    raise ValueError('原 App 不支持所选模型设置')
                settings[field] = value
        roots = (project or {}).get('roots', [])
        cwd = roots[0] if roots else (project or {}).get('path') or str(self.agent.home.parent)
        if not Path(cwd).is_absolute() or not Path(cwd).is_dir():
            raise ValueError('项目目录不可用')
        params = {'cwd': cwd, 'historyMode': 'paginated'}
        if project_id:
            params['projectId'] = project_id
        if 'fullAccess' in body:
            full = body['fullAccess']
            if type(full) is not bool:
                raise ValueError('权限选项无效')
            profile = ':danger-full-access' if full else ':workspace'
            options = self.agent.appserver.request('permissionProfile/list', {'cwd': cwd, 'limit': 100})['data']
            if not any((p['id'] == profile and p.get('allowed') for p in options)):
                raise ValueError('原 App 不允许所选权限配置')
            params.update(permissions=profile, approvalsReviewer='user', approvalPolicy='never' if full else 'on-request')
        tid = None
        try:
            with self.agent.appserver.session(timeout=20) as rpc:
                tid = str(UUID(rpc('thread/start', {**params, **{k: v for k, v in settings.items() if k != 'effort'}})['thread']['id']))
                rpc('thread/name/set', {'threadId': tid, 'name': prompt.strip().splitlines()[0][:60]})
                if settings:
                    rpc('thread/settings/update', {'threadId': tid, **settings})
            self.agent.read_catalog()
            self.attach(tid)
            result = self.execute({'id': command['id'], 'thread': tid, 'action': 'send', 'text': prompt})
            return {**result, 'threadId': tid, 'initialMessagePending': not result.get('ok')}
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            if not tid:
                raise
            return {'ok': False, 'threadId': tid, 'initialMessagePending': True, 'error': str(exc)}

    def create_side_chat(self, parent):
        parent = str(UUID(parent))
        with self.side_lock:
            if not self.owns(parent):
                return self.agent.appserver.request('thread/fork', {'threadId': parent, 'attachToSource': True, 'excludeTurns': True})
            read = self.agent.appserver.request

            def local_rows(sql, params):
                databases = list(self.agent.home.glob('state_*.sqlite'))
                if not databases:
                    raise RuntimeError('没有找到原 App 的本地对话目录')
                current = max(databases, key=lambda p: p.stat().st_mtime)
                with closing(sqlite3.connect(current.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
                    return db.execute(sql, params).fetchall()
            display = "coalesce(nullif(name,''),title)"
            targets = local_rows('SELECT ' + display + ' FROM threads WHERE id=? AND archived=0', (parent,))
            title = targets[0][0] if targets else None
            if not title or len(title) > 512 or (not title.isprintable()):
                raise ValueError('请先为主对话设置可核对的名称')
            if local_rows('SELECT id FROM threads WHERE ' + display + '=? LIMIT 2', (title,)) != [(parent,)]:
                raise ValueError('主对话名称不唯一，无法核对原窗口')

            def children():
                return {r[0] for r in local_rows('SELECT id FROM threads WHERE attached_thread_parent_id=? AND archived=0', (parent,))}
            before = children()
            self.open_thread(parent)
            if sys.platform == 'darwin':
                from crb.codex.mac_side_chat import open_side_chat
                open_side_chat(self.window.pid, title)
            else:
                helper = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2])) / 'desktop_client' / 'OpenCodexSideChat.exe'
                deadline = time.monotonic() + 5
                while True:
                    proc = subprocess.run([str(helper), str(self.window.pid)], input=title + '\n', encoding='utf-8', timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
                    if proc.returncode not in (3, 4) or time.monotonic() >= deadline:
                        break
                    time.sleep(0.25)
                if proc.returncode:
                    raise RuntimeError('原 Codex 当前文档未能核对，侧聊未创建')
            end = time.monotonic() + 10
            while time.monotonic() < end:
                rows = children()
                new = rows - before
                if len(new) == 1:
                    value = read('thread/read', {'threadId': new.pop()})
                    if value['thread'].get('attachedToThreadId') == parent:
                        return value
                    break
                if new:
                    break
                time.sleep(0.4)
            raise RuntimeError('已请求原 App 打开侧聊，尚未确认新对话编号，请刷新后核对')

    def send_side(self, parent, child, message, command_id):
        child = str(UUID(child))
        record = self.agent.appserver.request('thread/read', {'threadId': child})['thread']
        if record.get('attachedToThreadId') != str(UUID(parent)):
            raise ValueError('侧聊与当前主对话不匹配')
        self.attach(child)
        return self.execute({'id': command_id, 'action': 'send', 'thread': child, 'text': message})

    def execute(self, command):
        if command['action'] == 'new_thread':
            return self.new_chat(command)
        return super().execute(command)
