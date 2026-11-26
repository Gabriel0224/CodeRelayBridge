"""Connect to the unmodified Linux desktop and verify saved side chats by native ID."""
import json
import threading
import time
from uuid import UUID
from crb.codex.linux_desktop_queue import LinuxDesktopQueue, desktop_parent
from crb.codex.stock_desktop_queue import StockDesktopQueue

class LinuxStockDesktopQueue(StockDesktopQueue):
    """Independent app-server for a stock Linux desktop on its factory transport."""

    def __init__(self, agent, backend):
        self.window = desktop_parent(backend)
        if self.window is None:
            raise ConnectionError('无法确认原 Codex 桌面')
        self.side_lock = threading.Lock()
        LinuxDesktopQueue.__init__(self, agent, backend)
        agent.appserver.request('thread/list', {'limit': 1}, timeout=5)

    def owns(self, tid):
        return LinuxDesktopQueue.owns(self, tid)

    def open_thread(self, tid):
        return LinuxDesktopQueue.open_thread(self, str(UUID(tid)))

    def execute(self, command):
        if command['action'] == 'send':
            raw = command.get('text')
            try:
                payload = json.loads(raw) if isinstance(raw, str) else None
            except ValueError:
                payload = None
            if isinstance(payload, dict) and payload.get('relayMessageVersion') == 1:
                if payload.get('attachments', []) != []:
                    raise ValueError('当前连接只支持纯文字消息')
                command = {**command, 'text': payload.get('text')}
        return super().execute(command)

    def send_side(self, parent, child, message, command_id):
        with self.agent.lock:
            known = any((row['id'] == child for row in self.agent.catalog))
        if not known:
            self.agent.read_catalog()
        return super().send_side(parent, child, message, command_id)

    def _linux_children(self, parent):
        ids = set()
        cursor = None
        seen = set()
        while True:
            page = self.agent.appserver.request('thread/list', {'attachedToThreadId': parent, 'cursor': cursor, 'limit': 100})
            ids.update((row['id'] for row in page['data'] if row.get('attachedToThreadId') == parent))
            next_cursor = page.get('nextCursor')
            if not next_cursor:
                return ids
            if next_cursor in seen or len(seen) >= 20:
                raise RuntimeError('原生侧聊目录过长或分页异常，无法核对新侧聊')
            seen.add(next_cursor)
            cursor = next_cursor

    def create_side_chat(self, parent):
        parent = str(UUID(parent))
        read = self.agent.appserver.request
        with self.side_lock:
            if not self.owns(parent):
                result = read('thread/fork', {'threadId': parent, 'attachToSource': True, 'excludeTurns': True})
                if result['thread'].get('attachedToThreadId') != parent:
                    raise RuntimeError('原 App 未确认侧聊归属')
                return result
            before = self._linux_children(parent)
            if not self.connected:
                raise RuntimeError('与原 Codex 的连接已断开')
            self.open_thread(parent)
            from crb.codex.linux_side_window import open_side_chat
            open_side_chat(self.window, parent)
            end = time.monotonic() + 10
            while time.monotonic() < end:
                new = self._linux_children(parent) - before
                if len(new) == 1:
                    value = read('thread/read', {'threadId': new.pop()})
                    if value['thread'].get('attachedToThreadId') == parent:
                        return value
                    break
                if new:
                    break
                time.sleep(0.25)
            raise RuntimeError('已请求原 Codex 打开侧聊，未确认可保存的侧聊编号。请核对原窗口，避免重复创建')
