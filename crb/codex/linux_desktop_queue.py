"""Use the stock Linux desktop's queue and verify its actual per-thread writer."""
from pathlib import Path
import json
import configparser
import os
import stat
import threading
import time
import uuid
import psutil
APP = Path('/usr/lib/chatgpt/ChatGPT')
BACKEND = APP.parent / 'resources/codex'
ACTIONS = ('watch', 'refresh_thread', 'attach_thread', 'send', 'load_older', 'fetch_asset', 'terminal_start', 'terminal_read', 'terminal_write', 'terminal_resize', 'terminal_close')

def desktop_parent(backend):
    """Identify the stock GUI that launched this backend, including its runtime wrapper."""
    uid = backend.uids().real
    parent = backend
    for _ in range(4):
        parent = parent.parent()
        if parent is None or parent.uids().real != uid:
            break
        if Path(parent.exe()) == APP and (not any((a.startswith('--type=') for a in parent.cmdline()))):
            return parent
    return None

def native_backend(home):
    if home != (Path.home() / '.codex').resolve() or not (home / 'thread-writer-locks').is_dir():
        raise ConnectionError('本机没有可验证的桌面对话写入记录')
    version = configparser.ConfigParser()
    version.read(APP.parent / 'resources/owl-app.ini')
    if version.get('Owl', 'AppVersion', fallback='') != '26.1120.41243':
        raise ConnectionError('此桌面版本尚未验证原生队列协议')
    matches = []
    for p in psutil.process_iter(['name']):
        if p.info['name'] != 'codex':
            continue
        try:
            if p.uids().real != os.getuid() or Path(p.exe()) != BACKEND or 'app-server' not in p.cmdline()[1:]:
                continue
            parent = desktop_parent(p)
            if parent and (not any((a == '--user-data-dir' or a.startswith('--user-data-dir=') for a in parent.cmdline()))):
                matches.append(p)
        except (OSError, psutil.Error):
            continue
    if len(matches) != 1:
        raise ConnectionError('无法唯一确认本机 Codex App 的后端')
    return matches[0]

def writer_inodes(pid):
    result = set()
    for line in Path('/proc/locks').read_text().splitlines():
        parts = line.split()
        if len(parts) < 8 or parts[1:5] != ['FLOCK', 'ADVISORY', 'WRITE', str(pid)]:
            continue
        major, minor, inode = parts[5].split(':')
        result.add((os.makedev(int(major, 16), int(minor, 16)), int(inode)))
    return result

class LinuxDesktopQueue:
    queue_desktop = True
    actions = ACTIONS

    def __init__(self, agent, backend=None):
        self.agent = agent
        self.backend = backend if backend is not None else native_backend(agent.home)
        self.lock = threading.RLock()
        self.watched = {}
        self.last_poll = 0.0
        self.sock = self
        self.closed = False

    @property
    def connected(self):
        return not self.closed and self.backend.is_running()

    def close(self):
        self.closed = True

    def owns(self, tid):
        tid = str(uuid.UUID(tid))
        if not self.connected:
            return False
        try:
            info = (self.agent.home / 'thread-writer-locks' / (tid + '.lock')).lstat()
            return stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and ((info.st_dev, info.st_ino) in writer_inodes(self.backend.pid)) and self.connected
        except (FileNotFoundError, ProcessLookupError):
            return False

    def observe(self, tid, force=False):
        with self.lock:
            return self._observe(tid, force)

    def _observe(self, tid, force):
        with self.agent.lock:
            meta = next((r.copy() for r in self.agent.catalog if r['id'] == tid), None)
        if not meta:
            raise ValueError('未找到这条本机对话')
        owned = self.owns(tid)
        view = self.agent.read_native_history_view(tid, meta)
        server = getattr(self, 'owner', None) or self.agent.appserver
        latest = server.request('thread/turns/list', {'threadId': tid, 'limit': 1, 'itemsView': 'summary', 'sortDirection': 'desc'})['data']
        stamp = latest[0].get('status') if latest else None
        view.update(owned=owned, status=('active' if stamp == 'inProgress' else 'idle') if owned else 'notLoaded', source='desktop-live' if owned else 'codex-app-server', transport='codex-desktop-queue')
        if getattr(self, 'supports_side_workbench', False):
            from crb.codex.linux_panel_actions import retry_state
            tail = latest[0] if latest else {}
            retry = retry_state(tail)
            view.update(queuedMessages=self.public_queue(tid), lastTurnId=tail.get('id'), capacityRetry=retry, canContinue=owned and (stamp == 'interrupted' or bool(retry and retry['canContinue'])), canEditHistory=owned and stamp != 'inProgress' and ('edit_user_turn' in self.actions))
        if getattr(self, 'live', None):
            view['requests'] = self.live.pending(tid) if owned else []
        signature = (owned, view['status'], view['partialHistory'], view['items'], view.get('queuedMessages'), view.get('requests'), view.get('lastTurnId'), view.get('canContinue'), view.get('canEditHistory'))
        now = time.monotonic()
        with self.lock:
            prev = self.watched.get(tid)
            if prev is None and len(self.watched) >= 4:
                self.watched.pop(min(self.watched, key=lambda k: self.watched[k][0]))
            lease = now + 90 if force or prev is None else prev[0]
            self.watched[tid] = (lease, signature)
        if force or prev is None or prev[1] != signature:
            with self.agent.lock:
                self.agent.last_sent[tid] = view
        return view

    def poll(self):
        now = time.monotonic()
        if now - self.last_poll < 2:
            return
        self.last_poll = now
        with self.lock:
            for tid in list(self.watched):
                if self.watched[tid][0] < now:
                    del self.watched[tid]
            watched = list(self.watched)
        for tid in watched:
            try:
                self.observe(tid)
            except (OSError, RuntimeError, ValueError, TimeoutError):
                continue

    def renew_foreground(self, targets):
        if not isinstance(targets, list):
            return
        with self.agent.lock:
            catalog = {row['id'] for row in self.agent.catalog}
        deadline = time.monotonic() + 8
        with self.lock:
            for tid in targets[:4]:
                if not isinstance(tid, str) or tid not in catalog:
                    continue
                if tid not in self.watched and len(self.watched) >= 4:
                    oldest = min(self.watched, key=lambda key: self.watched[key][0])
                    del self.watched[oldest]
                previous = self.watched.get(tid)
                self.watched[tid] = (deadline, previous[1] if previous else None)

    def execute(self, command):
        tid, action = (command['thread'], command['action'])
        if action == 'attach_thread':
            if not self.owns(tid):
                self.agent.open_desktop_thread(tid)
                deadline = time.monotonic() + 12
                while not self.owns(tid):
                    if time.monotonic() >= deadline:
                        raise RuntimeError('原 App 尚未接入指定对话')
                    time.sleep(0.2)
            self.observe(tid, force=True)
            return {'ok': True, 'threadId': tid, 'transport': 'codex-desktop-queue'}
        if action in ('watch', 'refresh_thread'):
            self.observe(tid, force=True)
            return {'ok': True, 'threadId': tid}
        if action != 'send':
            raise ValueError('当前连接不支持此操作')
        if not self.owns(tid):
            raise ValueError('请先接入这条原桌面对话')
        text = command.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > 32000:
            raise ValueError('需要有效的文字消息')
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
        if isinstance(payload, dict) and payload.get('relayMessageVersion') == 1:
            raise ValueError('此连接目前支持纯文字消息')
        cid = str(uuid.UUID(command['id']))
        latest = self.agent.appserver.request('thread/turns/list', {'threadId': tid, 'limit': 1, 'itemsView': 'summary', 'sortDirection': 'desc'})['data']
        if latest and latest[0].get('status') == 'inProgress':
            raise ValueError('这条对话正在运行，请在空闲时发送')
        existing = {t['id'] for t in latest}
        try:
            queued = self.agent.appserver.request('thread/queue/add', {'threadId': tid, 'clientUserMessageId': cid, 'input': [{'type': 'text', 'text': text, 'text_elements': []}]})['queuedSubmission']
        except (OSError, RuntimeError, ValueError, KeyError, TimeoutError):
            return {'ok': False, 'unknown': True, 'error': '原 App 的队列回执未确认，请先核对原对话，避免重复发送'}
        if queued.get('clientUserMessageId') != cid:
            raise RuntimeError('未收到匹配的原生队列回执，请核对原对话')
        try:
            self.agent.open_desktop_thread(tid)
            self.observe(tid, force=True)
            end = time.monotonic() + 15
            while time.monotonic() < end:
                turns = self.agent.appserver.request('thread/turns/list', {'threadId': tid, 'limit': 3, 'itemsView': 'full', 'sortDirection': 'desc'})['data']
                for t in turns:
                    if t['id'] in existing:
                        continue
                    for item in t.get('items', []):
                        if item.get('type') == 'userMessage' and cid in (item.get('clientUserMessageId'), item.get('clientId'), item.get('id')):
                            return {'ok': True, 'threadId': tid, 'clientUserMessageId': cid, 'turnId': t['id'], 'confirmedBy': 'native-turn'}
                time.sleep(0.6)
        except (OSError, RuntimeError, ValueError, TimeoutError):
            pass
        return {'ok': False, 'unknown': True, 'queuedSubmissionId': queued.get('id'), 'error': '原 App 已收进队列，尚未确认开始处理；请核对原对话，避免重复发送'}
