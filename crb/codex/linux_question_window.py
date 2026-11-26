"""Operate only an identified, visible async-question card in the Linux Codex app."""
from contextlib import closing
import sqlite3
import time
import psutil
from crb.codex.linux_desktop_queue import desktop_parent
DISMISS = ('Dismiss', '关闭', '收起', '忽略')
SKIP = ('Skip', '跳过')

def unique_title(home, tid):
    from crb.codex.agent import title_text
    databases = sorted(home.glob('state_*.sqlite'), key=lambda p: p.stat().st_mtime, reverse=True)
    if not databases:
        return None
    with closing(sqlite3.connect(databases[0].resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
        db.create_function('panel_title', 3, title_text)
        row = db.execute('SELECT panel_title(name,title,first_user_message) FROM threads WHERE id=?', (tid,)).fetchone()
        if not row or row[0] == title_text():
            return None
        found = db.execute('SELECT id FROM threads WHERE panel_title(name,title,first_user_message)=? LIMIT 2', row).fetchall()
        return row[0] if found == [(tid,)] else None

def desktop_process(backend):
    parent = desktop_parent(backend)
    return parent if parent and '--force-renderer-accessibility' in parent.cmdline() else None

class QuestionWindow:

    def __init__(self, bus, deadline):
        self.bus = bus
        self.deadline = deadline

    def call(self, address, interface, method, signature=None, args=()):
        from jeepney import DBusAddress, new_method_call
        from jeepney.wrappers import unwrap_msg
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('读取提问窗口超时')
        name, path = address
        message = new_method_call(DBusAddress(path, name, interface), method, signature, args)
        return unwrap_msg(self.bus.send_and_get_reply(message, timeout=min(1, remaining)))

    def app_root(self, pid):
        registry = ('org.a11y.atspi.Registry', '/org/a11y/atspi/accessible/root')
        dbus = ('org.freedesktop.DBus', '/org/freedesktop/DBus')
        apps = self.call(registry, 'org.a11y.atspi.Accessible', 'GetChildren')[0]
        matches = [app for app in apps if self.call(dbus, 'org.freedesktop.DBus', 'GetConnectionUnixProcessID', 's', (app[0],))[0] == pid]
        return matches[0] if len(matches) == 1 else None

    def tree(self, root, *, all_names=False, skip_roles=()):
        pending = [(root, None)]
        nodes = {}
        while pending:
            address, parent = pending.pop()
            if address in nodes:
                continue
            if len(nodes) >= 1800:
                raise ValueError('当前窗口过大')
            role = self.call(address, 'org.a11y.atspi.Accessible', 'GetRoleName')[0]
            name = ''
            if all_names or role in ('document web', 'section', 'static', 'push button', 'button', 'radio button', 'entry', 'scroll pane'):
                name = self.call(address, 'org.freedesktop.DBus.Properties', 'Get', 'ss', ('org.a11y.atspi.Accessible', 'Name'))[0][1]
            children = [] if role in skip_roles else self.call(address, 'org.a11y.atspi.Accessible', 'GetChildren')[0]
            children = [c for c in children if c[0] == root[0]]
            nodes[address] = (role, name, parent)
            pending.extend(((child, address) for child in reversed(children)))
        return nodes

    @staticmethod
    def button(nodes, title, anchor, question, labels):

        def subtree(parent):
            descendants = {parent}
            for key, (_, _, ancestor) in nodes.items():
                if ancestor in descendants:
                    descendants.add(key)
            return [(key, nodes[key][0], nodes[key][1]) for key in nodes if key in descendants]
        docs = [key for key, (role, _, _) in nodes.items() if role == 'document web']
        if len(docs) != 1 or nodes[docs[0]][1] != title:
            return None
        doc = subtree(docs[0])
        if sum((r == 'static' and n == anchor for _, r, n in doc)) != 1 or any((r == 'scroll pane' and n in ('Side chat', '侧聊', '侧边聊天') for _, r, n in doc)):
            return None
        buttons = set()
        for key, role, _ in doc:
            if role != 'section':
                continue
            rows = subtree(key)
            if sum((r == 'static' and n == question['title'] for _, r, n in rows)) != 1 or [n for _, r, n in rows if r == 'radio button'] != question['options']:
                continue
            if not question['options']:
                direct = [(r, n) for k, r, n in rows if nodes[k][2] == key]
                names = {n for r, n in direct if r in ('push button', 'button')}
                if sum((r == 'entry' for r, _ in direct)) != 1 or not names.intersection(DISMISS) or (not names.intersection(SKIP)):
                    continue
            buttons.update((k for k, r, n in rows if r in ('push button', 'button') and n in labels))
        return next(iter(buttons)) if len(buttons) == 1 else None

    @staticmethod
    def answer_button(nodes, title, anchor, question, siblings):
        docs = [k for k, (role, _, _) in nodes.items() if role == 'document web']
        if len(docs) != 1 or nodes[docs[0]][1] != title:
            return None
        if sum((role == 'static' and name == anchor for role, name, _ in nodes.values())) != 1:
            return None
        if any((role == 'scroll pane' and name in ('Side chat', '侧聊', '侧边聊天') for role, name, _ in nodes.values())):
            return None
        labels = {q['title'] for q in siblings}
        candidates = [k for k, (role, name, _) in nodes.items() if role == 'static' and name == question['title']]
        if len(candidates) != 1:
            return None
        parent = nodes[candidates[0]][2]
        while parent and nodes[parent][0] not in ('landmark', 'document web', 'frame', 'application'):
            group = {parent}
            for key, (_, _, ancestor) in nodes.items():
                if ancestor in group:
                    group.add(key)
            rows = [nodes[k] for k in group]
            if any((role in ('heading', 'entry', 'radio button') for role, _, _ in rows)) or sum((role == 'static' and name in labels for role, name, _ in rows)) != 1:
                return None
            buttons = [k for k in group if nodes[k][0] in ('push button', 'button')]
            if buttons:
                if len(buttons) == 1 and nodes[buttons[0]][1] in ('Answer question', '回答问题'):
                    return buttons[0]
                return None
            parent = nodes[parent][2]
        return None

    def press(self, button):
        state = self.call(button, 'org.a11y.atspi.Accessible', 'GetState')[0][0]
        if not state & 1 << 8 or state & 1 << 6:
            return False
        actions = self.call(button, 'org.a11y.atspi.Action', 'GetActions')[0]
        names = [self.call(button, 'org.a11y.atspi.Action', 'GetName', 'i', (i,))[0].lower() for i in range(len(actions))]
        indices = [i for i, name in enumerate(names) if name in ('click', 'press', 'activate')]
        if not indices and names and (not names[0]):
            indices = [0]
        return len(indices) == 1 and self.call(button, 'org.a11y.atspi.Action', 'DoAction', 'i', (indices[0],))[0]

    def reopen(self, app, title, anchor, question, siblings, validate):
        visited = set()
        for _ in range(len(siblings) + 1):
            nodes = self.tree(app, all_names=True)
            if not validate():
                return False
            if self.button(nodes, title, anchor, question, DISMISS):
                return 'applied'
            origin = None
            target = self.answer_button(nodes, title, anchor, question, siblings)
            if target is None:
                visible = [i for i, q in enumerate(siblings) if self.button(nodes, title, anchor, q, DISMISS)]
                if len(visible) != 1 or question not in siblings:
                    return False
                idx = visible[0]
                origin = siblings[idx]['questionItemId']
                labels = ('Previous question', '上一个问题') if idx > siblings.index(question) else ('Next question', '下一个问题')
                target = self.button(nodes, title, anchor, siblings[idx], labels)
            if not target or (target, origin) in visited or (not self.press(target)):
                return False
            visited.add((target, origin))
            time.sleep(0.18)
        return False

    def control(self, app, title, anchor, question, operation, validate, recent, siblings=()):
        if operation == 'reopen':
            return self.reopen(app, title, anchor, question, siblings or (question,), validate)
        labels = {'close': DISMISS, 'skip': SKIP}[operation]
        button = self.button(self.tree(app), title, anchor, question, labels)
        if not button or not validate():
            return False
        key = (button, question['questionItemId'], operation)
        now = time.monotonic()
        for old in list(recent):
            if now - recent[old] > 30:
                del recent[old]
        if key in recent:
            return 'requested'
        if len(recent) >= 64:
            return False
        recent[key] = now
        from jeepney.wrappers import DBusErrorResponse
        try:
            if not self.press(button):
                recent.pop(key, None)
                return False
            for _ in range(3):
                time.sleep(0.12)
                nodes = self.tree(app)
                if sum((role == 'document web' and name == title for role, name, _ in nodes.values())) == 1 and any((role == 'static' and name == anchor for role, name, _ in nodes.values())) and (not self.button(nodes, title, anchor, question, labels)):
                    recent.pop(key, None)
                    return 'applied'
        except (OSError, DBusErrorResponse, ValueError):
            pass
        return 'requested'

def control_question(desktop, tid, turn, question, operation):
    from crb.codex.agent import pending_async_questions, text_content
    from crb.codex.linux_panel_actions import question_turns
    if operation not in ('close', 'skip', 'reopen'):
        return False
    from jeepney.io.blocking import open_dbus_connection
    from jeepney.wrappers import DBusErrorResponse
    try:
        gui = desktop_process(desktop.backend)
        title = unique_title(desktop.agent.home, tid)
        anchors = [text_content(i.get('content') or i.get('input')) for i in turn['items'] if i.get('type') == 'userMessage']
        if not gui or not title or len(anchors) != 1 or (not 1 <= len(anchors[0]) <= 4000):
            return False
        deadline = time.monotonic() + (15 if operation == 'reopen' else 6)
        with closing(open_dbus_connection('SESSION')) as session:
            addr = QuestionWindow(session, deadline).call(('org.a11y.Bus', '/org/a11y/bus'), 'org.a11y.Bus', 'GetAddress')[0]
        if not addr.startswith('unix:'):
            return False
        with closing(open_dbus_connection(addr)) as bus:
            window = QuestionWindow(bus, deadline)
            root = window.app_root(gui.pid)

            def validate():
                if not (gui.is_running() and desktop.owns(tid) and (unique_title(desktop.agent.home, tid) == title)):
                    return False
                turns = question_turns(desktop, tid, turn_id=turn['id'])
                matches = [q for _, group in pending_async_questions({'turns': turns}) for q in group if q['title'] == question['title'] and q['options'] == question['options']]
                return bool(turns) and turns[-1]['id'] == turn['id'] and (matches == [question])
            if root and operation == 'reopen' and validate():
                docs = [n for role, n, _ in window.tree(root).values() if role == 'document web']
                navigate = getattr(desktop.agent, 'open_desktop_thread', None)
                if docs != [title] and len(docs) == 1 and callable(navigate):
                    navigate(tid)
                    for _ in range(8):
                        time.sleep(0.15)
                        if [n for r, n, _ in window.tree(root).values() if r == 'document web'] == [title]:
                            break
            siblings = next((group for _, group in pending_async_questions({'turns': [turn]}) if question in group), ())
            return root and window.control(root, title, anchors[0], question, operation, validate, desktop.question_controls, siblings)
    except (DBusErrorResponse, OSError, ValueError, KeyError, IndexError, sqlite3.Error, psutil.Error, RuntimeError):
        return False
