"""Drive the visible local Codex forms on explicit panel actions."""
from contextlib import closing, contextmanager
from pathlib import Path
import os
import re
import shutil
import subprocess
import time
import uuid
import psutil
from jeepney.io.blocking import open_dbus_connection
from jeepney.wrappers import DBusErrorResponse
from crb.codex.linux_question_window import QuestionWindow, desktop_process, unique_title
from crb.codex.projects import local_projects, project_id_map
A11Y = 'org.a11y.atspi.'
BUTTON = ('button', 'push button')
EDIT = ('Edit message', '编辑消息')
PROJECT_NAME = ('Project name', '项目名称')
CREATE = ('Create project', '创建项目', '新建项目')
EDIT_PROJECT = ('Edit project', '编辑项目')
CANCEL = ('Cancel', '取消')
SEND = ('Send', '发送')
REQUEST_MARKER = re.compile('## My request(?: for Codex)?:')
REGISTRY = ('org.a11y.atspi.Registry', '/org/a11y/atspi/accessible/root')

def wait_for(read, message, seconds=5):
    end = time.monotonic() + seconds
    while True:
        try:
            value = read()
        except DBusErrorResponse:
            value = None
        if value:
            return value
        if time.monotonic() >= end:
            raise RuntimeError(message)
        time.sleep(0.1)

def subtree(nodes, root):
    result = {}
    for key, row in nodes.items():
        if key == root or row[2] in result:
            result[key] = row
    return result

def matches(nodes, roles, names=None):
    return [k for k, (role, name, _) in nodes.items() if role in roles and (names is None or name in names)]

def only(items):
    if len(items) > 1:
        raise ValueError('桌面上存在多个相同控件，无法确认目标')
    return items[0] if items else None

class FormWindow(QuestionWindow):

    def __init__(self, bus, gui):
        super().__init__(bus, time.monotonic() + 90)
        self.gui = gui
        self.root = self.app_root(gui.pid)
        if not self.root:
            raise RuntimeError('未找到原 Codex App 的辅助功能窗口')

    def nodes(self):
        if not self.gui.is_running():
            raise RuntimeError('原 Codex 窗口已退出')
        return self.tree(self.root, all_names=True)

    def find(self, roles, names, scope=None):
        tree = self.nodes()
        return only(matches(subtree(tree, scope) if scope else tree, roles, names))

    def expect(self, roles, names, scope=None):
        return wait_for(lambda: self.find(roles, names, scope), '原 App 未显示预期控件：' + names[0])

    def act(self, node, names=('click', 'press', 'activate', 'select'), *, unnamed=True):
        if not node:
            raise ValueError('未找到目标控件')
        flags = self.call(node, A11Y + 'Accessible', 'GetState')[0][0]
        if not flags & 1 << 8 or flags & 1 << 6:
            raise ValueError('原 App 的目标控件不可用')
        actions = self.call(node, A11Y + 'Action', 'GetActions')[0]
        labels = [self.call(node, A11Y + 'Action', 'GetName', 'i', (i,))[0].lower() for i in range(len(actions))]
        indexes = [i for i, label in enumerate(labels) if label in names]
        if not indexes and unnamed and actions and (not labels[0]):
            indexes = [0]
        index = only(indexes)
        if index is None or not self.call(node, A11Y + 'Action', 'DoAction', 'i', (index,))[0]:
            raise RuntimeError('原 App 未接受控件操作')

    def menu_item(self, names):
        rows = self.nodes()
        menus = matches(rows, ('menu',))
        keys = {key for menu in menus for key in matches(subtree(rows, menu), ('menu item',), names)}
        if len(keys) > 1:
            boxes = {tuple(self.call(key, A11Y + 'Component', 'GetExtents', 'u', (0,))[0]) for key in keys}
            if len(boxes) != 1 or min(next(iter(boxes))[2:]) <= 0:
                raise ValueError('无法唯一确认桌面菜单')
        return next(iter(keys), None)

    def field_text(self, field):
        value = self.call(field, A11Y + 'Text', 'GetText', 'ii', (0, -1))[0]
        if '￼' in value:
            rows = self.tree(field, all_names=True)
            return '\n'.join((v[1] for v in rows.values() if v[0] == 'static'))
        return value

    def field_menu(self, field, names):
        self.act(field, ('showcontextmenu',), unnamed=False)
        self.act(wait_for(lambda: self.menu_item(names), '原 App 编辑菜单不可用'))

    def fill(self, field, old, new):
        if self.field_text(field) != old:
            raise RuntimeError('原 App 表单内容已改变')
        if old == new:
            return
        with clipboard_text(new):
            if not self.call(field, A11Y + 'Component', 'GrabFocus')[0]:
                raise RuntimeError('原 App 无法聚焦指定表单')
            if old:
                self.field_menu(field, ('Select All', 'Select All Ctrl+A', '全选', '全选 Ctrl+A'))
            self.field_menu(field, ('Paste Ctrl+V', 'Paste', '粘贴 Ctrl+V', '粘贴'))
            wait_for(lambda: self.field_text(field) == new, '原 App 表单未填入指定文字，未提交')

    def show_sidebar(self):
        toggle = self.find(BUTTON, ('Show sidebar', '显示侧边栏', '打开侧边栏'))
        if toggle:
            self.act(toggle)

    def project_row(self, label):
        area = self.expect(('landmark',), ('Chat history', '聊天历史记录', '聊天记录', '对话历史记录'))
        tree = subtree(self.nodes(), area)
        row = only([k for k in matches(tree, BUTTON, [label]) if matches(subtree(tree, k), BUTTON, [f'Project actions for {label}', f'{label} 的项目操作']) or any((v[0] in BUTTON and ('project' in v[1].lower() or '项目' in v[1]) for v in subtree(tree, k).values() if v[1] != label))])
        if not row:
            raise RuntimeError('目标项目未在原 App 左侧展开显示')
        return row

@contextmanager
def clipboard_text(text):
    paste, copy = (shutil.which('wl-paste'), shutil.which('wl-copy'))
    if not os.environ.get('WAYLAND_DISPLAY') or not paste or (not copy):
        raise RuntimeError('此桌面需要 Wayland 和 wl-clipboard 才能填写原 App 表单')
    formats = subprocess.run([paste, '--list-types'], capture_output=True, timeout=3, env={**os.environ, 'LC_ALL': 'C'})
    if formats.returncode and b'Nothing is copied' not in formats.stderr:
        raise RuntimeError('无法读取目标桌面的剪贴板状态')
    types = set(formats.stdout.decode().splitlines())
    if types - {'text/plain;charset=utf-8', 'text/plain', 'TEXT', 'STRING', 'UTF8_STRING'}:
        raise ValueError('剪贴板包含其他格式，已保留原内容；请在原 App 表单继续')

    def read():
        with subprocess.Popen([paste, '--no-newline', '--type', 'text'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
            import selectors
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                buf = bytearray()
                end = time.monotonic() + 3
                while len(buf) <= 1024 * 1024:
                    if not selector.select(max(0, end - time.monotonic())):
                        proc.kill()
                        raise RuntimeError('剪贴板读取超时')
                    chunk = os.read(proc.stdout.fileno(), 65536)
                    if not chunk:
                        return bytes(buf) if proc.wait(timeout=1) == 0 else None
                    buf.extend(chunk)
                proc.kill()
                raise ValueError('已保留较大的剪贴板，请在原 App 表单继续')
    before = read() if types else None
    if types and before is None:
        raise RuntimeError('未能保存原剪贴板')
    data = text.encode()
    if before == data:
        yield
        return
    proc = subprocess.Popen([copy, '--foreground', '--type', 'text/plain;charset=utf-8'], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        proc.stdin.write(data)
        proc.stdin.close()
        wait_for(lambda: proc.poll() is None and read() == data, '无法填写原 App 表单')
        yield
    finally:
        try:
            if proc.poll() is None and read() == data:
                if before is None:
                    subprocess.run([copy, '--clear'], check=True, timeout=3)
                else:
                    subprocess.run([copy, '--type', 'text/plain;charset=utf-8'], input=before, check=True, timeout=3, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        finally:
            proc.terminate()
            proc.wait(timeout=3)

@contextmanager
def window(desktop):
    gui = desktop_process(desktop.backend)
    if not gui:
        raise RuntimeError('当前 Codex 窗口未提供辅助功能')
    with closing(open_dbus_connection('SESSION')) as session:
        address = QuestionWindow(session, time.monotonic() + 3).call(('org.a11y.Bus', '/org/a11y/bus'), 'org.a11y.Bus', 'GetAddress')[0]
    if not address.startswith('unix:'):
        raise ValueError('需要本机桌面会话')
    with closing(open_dbus_connection(address)) as bus:
        yield FormWindow(bus, gui)

def available(desktop):
    if not (os.environ.get('WAYLAND_DISPLAY') and shutil.which('wl-copy') and shutil.which('wl-paste')):
        return False
    try:
        return desktop_process(desktop.backend) is not None
    except (OSError, psutil.Error):
        return False

def chooser(w):
    apps = w.call(REGISTRY, A11Y + 'Accessible', 'GetChildren')[0]
    choices = []
    for app in apps:
        pid = w.call(('org.freedesktop.DBus', '/org/freedesktop/DBus'), 'org.freedesktop.DBus', 'GetConnectionUnixProcessID', 's', (app[0],))[0]
        try:
            proc = psutil.Process(pid)
            if proc.uids().real != os.getuid() or proc.exe() != '/usr/libexec/xdg-desktop-portal-gtk':
                continue
        except psutil.NoSuchProcess:
            continue
        choices.extend(w.call(app, A11Y + 'Accessible', 'GetChildren')[0])
    return choices

def choose_folder(w, form, folder):
    if chooser(w):
        raise ValueError('目标电脑已有文件选择窗口，请先完成该操作')
    w.act(w.expect(BUTTON, ('Add folder', 'Choose source folders', 'Add', '选择源文件夹', '添加文件夹', '添加', '添加文件夹…'), form))
    dialog = wait_for(lambda: only(chooser(w)), '未找到原 App 打开的目录选择器')

    def nodes():
        if chooser(w) != [dialog] or not subtree(w.nodes(), form):
            raise RuntimeError('目录选择窗口已改变')
        return w.tree(dialog, all_names=True, skip_roles=('table',))
    initial = nodes()
    if initial[dialog][:2] not in [('file chooser', n) for n in ('Select Project Root', '选择项目根目录', '选择项目根文件夹', '选择项目目录')]:
        raise RuntimeError('无法确认原 App 的项目目录选择器')
    root = only(matches(initial, ('toggle button',), ('File System Root', '文件系统根目录', '文件系统根', '文件系统根路径', '根文件系统')))
    w.act(root)

    def current(rows):
        for layer in matches(rows, ('filler',), ('PathBar Layer',)):
            names = [name for role, name, _ in subtree(rows, layer).values() if role == 'toggle button']
            if root_name in names:
                return Path('/', *reversed(names[:names.index(root_name)]))
        return None
    root_name = initial[root][1]
    parent = Path('/')
    for part in folder.parts[1:]:

        def cell():
            rows = nodes()
            if current(rows) != parent:
                return None
            table = only(matches(rows, ('table',), ('Files', '文件')))
            if not table:
                return None
            count = w.call(table, 'org.freedesktop.DBus.Properties', 'Get', 'ss', (A11Y + 'Table', 'NRows'))[0][1]
            for index in range(count):
                cell = w.call(table, A11Y + 'Table', 'GetAccessibleAt', 'ii', (index, 0))[0]
                item = only(matches(w.tree(cell, all_names=True), ('table cell',), (part,)))
                if item:
                    return item
            return None
        row = wait_for(cell, '原生目录选择器找不到指定路径')
        w.act(row, ('activate',), unnamed=False)
        parent = parent / part
        wait_for(lambda: current(nodes()) == parent, '原 App 未进入指定目录')
    rows = nodes()
    if current(rows) != folder:
        raise ValueError('原生选择器显示的目录与面板不一致')
    w.act(only(matches(rows, BUTTON, ('Open', '打开', '选择'))))
    wait_for(lambda: dialog not in chooser(w), '原 App 未确认目录选择')

def listed(desktop):
    from crb.codex.agent import app_tool_json
    return local_projects(app_tool_json(desktop.desktop_tool('list_projects', {}, None)))

def project_roots(row):
    return [str(Path(p).resolve()) for p in row.get('rootPaths') or [row.get('path')] if p]

def directories(values):
    if not isinstance(values, list) or len(values) > 64:
        raise ValueError('项目目录无效')
    paths = []
    for value in values:
        if not isinstance(value, str) or not value.strip() or (not Path(value).is_absolute()) or (not Path(value).is_dir()):
            raise ValueError('请选择本机已存在的绝对目录')
        paths.append(str(Path(value).resolve(strict=True)))
    if len(set(paths)) != len(paths):
        raise ValueError('项目目录重复')
    return paths

def project_action(desktop, action, payload):
    name = payload.get('name')
    if action in ('project_create', 'project_rename'):
        if not isinstance(name, str) or not 0 < len(name.strip()) <= 120:
            raise ValueError('项目名称需为 1–120 个字符')
        name = name.strip()
    home = desktop.agent.home
    before = listed(desktop)
    if action == 'project_create':
        roots = directories([payload.get('root')])
        old_id = None
    else:
        backend_id = payload.get('projectId')
        mapping = project_id_map(home)
        row = only([p for p in before if mapping.get(p['projectId'], p['projectId']) == backend_id])
        if not row:
            raise ValueError('未找到原 App 已登记的目标项目')
        old_id = row['projectId']
        old_name = row['label']
        if sum((p.get('label') == old_name for p in before)) != 1:
            raise ValueError('原 App 有同名项目，无法确认要操作的行')

        def durable_roots():
            record = desktop.owner.request('project/read', {'projectId': backend_id})['project']
            return [r['path'] for r in record['roots']]
        old_roots = durable_roots()
        if not old_roots or project_roots(row)[0:1] != old_roots[:1]:
            raise ValueError('原 App 的项目主目录与后台不一致')
        roots = old_roots
        if action == 'project_update_roots':
            if directories(payload.get('expectedRoots')) != old_roots:
                raise ValueError('项目目录已改变，请刷新')
            roots = directories(payload.get('roots'))
            if not roots:
                raise ValueError('原 App 需保留一个项目目录')
        if action != 'project_rename':
            name = old_name

    def current_row():
        return next((p for p in listed(desktop) if p['projectId'] == old_id), None)
    with window(desktop) as w:
        if matches(w.nodes(), ('dialog', 'menu')):
            raise ValueError('请先完成原 App 当前打开的菜单或对话框')
        w.show_sidebar()
        if old_id is None:
            w.act(w.expect(BUTTON, ('Add new project', '添加新项目', '新建项目')))
            form = w.expect(('dialog',), CREATE)
            choose_folder(w, form, Path(roots[0]))
        else:
            w.act(w.project_row(old_name), ('showcontextmenu',), unnamed=False)
            if action == 'project_delete':
                w.act(wait_for(lambda: w.menu_item(('Remove project', '移除项目')), '未找到移除项目'))
                form = w.expect(('dialog',), (f'Remove {old_name}?', f'移除{old_name}？', f'移除“{old_name}”？', f'移除 {old_name}？'))
                if current_row() != row:
                    raise ValueError('项目已变化，未提交移除')
                w.act(w.expect(BUTTON, ('Remove project', '移除项目'), form))
                wait_for(lambda: current_row() is None, '原 App 未确认移除项目，请刷新核对')
                wait_for(lambda: form not in w.nodes(), '原 App 移除窗口仍在关闭')
                return {'ok': True, 'transport': 'desktop-project-form', 'confirmedAt': time.time()}
            w.act(wait_for(lambda: w.menu_item(('Edit', '编辑')), '未找到编辑项目'))
            form = w.expect(('dialog',), EDIT_PROJECT)
        field = w.expect(('entry',), PROJECT_NAME, form)
        if old_id and (current_row() != row or w.field_text(field) != old_name):
            raise ValueError('原 App 表单与面板项目不一致')
        if action == 'project_update_roots' and roots != old_roots:
            for original in old_roots:
                base = Path(original).name
                w.act(w.expect(BUTTON, (f'Remove {base}', f'移除 {base}', f'移除{base}', f'删除 {base}'), form))
            for path in roots:
                choose_folder(w, form, Path(path))
        w.fill(field, w.field_text(field), name)
        rows = subtree(w.nodes(), form)
        if any((not matches(rows, ('static',), (Path(path).name, str(path))) for path in roots)):
            raise ValueError('原 App 表单未显示所选目录，未保存')
        if old_id and (current_row() != row or durable_roots() != old_roots):
            raise ValueError('项目已在其他窗口发生变化')
        w.act(w.expect(BUTTON, CREATE if not old_id else ('Save', '保存'), form))

        def confirmed():
            mapping = project_id_map(home)
            prior_ids = {item['projectId'] for item in before}
            candidates = [p for p in listed(desktop) if (p['projectId'] == old_id if old_id else p['projectId'] not in prior_ids) and p.get('label') == name and (project_roots(p)[:1] == roots[:1])]
            candidate = only(candidates)
            if not candidate:
                return None
            desktop_id = candidate['projectId']
            backend_id = mapping.get(desktop_id, desktop_id if old_id else None)
            if not backend_id:
                return None
            durable = desktop.owner.request('project/read', {'projectId': backend_id})['project']
            if durable.get('name') == name and [r['path'] for r in durable['roots']] == roots:
                return dict(id=backend_id, desktopId=desktop_id, name=name, roots=roots, ready=True)
        project = wait_for(confirmed, '请在原 App 核对项目或完成文件夹信任提示，再刷新面板；请勿重复创建', 7)
        wait_for(lambda: form not in w.nodes(), '原 App 项目表单仍在关闭')
        return {'ok': True, 'project': project, 'registrationPending': False, 'transport': 'desktop-project-form', 'confirmedAt': time.time()}

def editable_text(value):
    markers = list(REQUEST_MARKER.finditer(value))
    if markers:
        return value[markers[-1].end():].strip()
    return value.replace('\r\n', '\n').rstrip('\n')

def checked_edit(desktop, tid, data):
    from crb.codex.agent import desktop_user_text, scheduled_user_trigger, text_content
    if not desktop.owns(tid):
        raise ValueError('请先接入这条对话')
    cursor = None
    seen = set()
    matches_original = []
    target = None
    last = None
    ids = []
    while True:
        page = desktop.owner.request('thread/turns/list', dict(threadId=tid, itemsView='full', sortDirection='desc', limit=30, cursor=cursor))
        for turn in page['data']:
            ids.append(turn['id'])
            if last is None:
                last = turn['id']
                if last != data['expectedLastTurnId'] or turn['status'] == 'inProgress':
                    raise ValueError('对话已变化，请刷新后再编辑')
            for item in turn['items']:
                if item['type'] != 'userMessage':
                    continue
                contents = item.get('content', [])
                raw = text_content(contents)
                if editable_text(desktop_user_text(raw)) == editable_text(data['originalText']):
                    matches_original.append(item['id'])
                    if turn['id'] == data['turnId'] and item['id'] == data['itemId'] and (scheduled_user_trigger(raw) is None):
                        if not (isinstance(contents, list) and len(contents) == 1 and (contents[0].get('type') == 'text') and (not (contents[0].get('text_elements') or contents[0].get('textElements')))):
                            raise ValueError('这条消息含附件或嵌入内容，请在原 App 的编辑框中修改')
                        target = item['id']
        cursor = page.get('nextCursor')
        if not cursor:
            break
        if cursor in seen:
            raise ValueError('原 App 历史分页异常')
        seen.add(cursor)
    if not target or matches_original != [target]:
        raise ValueError('原消息已变化或同一对话有相同文本，无法确认编辑目标')
    return ids[ids.index(data['turnId']) + 1:]

def stored_turn_ids(desktop, tid):
    ids = []
    cursor = None
    visited = set()
    while True:
        page = desktop.owner.request('thread/turns/list', dict(threadId=tid, itemsView='summary', sortDirection='desc', limit=100, cursor=cursor))
        ids.extend((turn['id'] for turn in page['data']))
        cursor = page.get('nextCursor')
        if not cursor:
            return ids
        if cursor in visited:
            raise ValueError('原 App 历史分页异常')
        visited.add(cursor)

def edit_button(tree, title, old):
    docs = matches(tree, ('document web',))
    if len(docs) != 1 or tree[docs[0]][1] != title:
        return None
    area = subtree(tree, docs[0])
    for side in matches(area, ('scroll pane',), ('Side chat', '侧聊', '侧边聊天')):
        for key in subtree(area, side):
            area.pop(key)
    conversation = only(matches(area, ('landmark',), ('Conversation', '对话', '会话')))
    if not conversation:
        return None
    results = []
    for key in matches(subtree(area, conversation), BUTTON, EDIT):
        group = tree[key][2]
        children = [k for k, (_, _, p) in tree.items() if p == group]
        headers = [i for i, k in enumerate(children[:children.index(key)]) if tree[k][0] == 'heading']
        if not headers or tree[children[headers[-1]]][1] not in ('You said:', '你说：', '你说:', '你说过：', '您说：'):
            continue
        for sibling in children[headers[-1] + 1:children.index(key)]:
            parts = [name for role, name, _ in subtree(tree, sibling).values() if role == 'static']
            if parts:
                if editable_text('\n'.join(parts)) == editable_text(old):
                    results.append(key)
                break
    return only(results)

def locate_edit(w, title, old):
    previous = None

    def search():
        nonlocal previous
        tree = w.nodes()
        found = edit_button(tree, title, old)
        if found:
            return found
        docs = matches(tree, ('document web',))
        if len(docs) != 1 or tree[docs[0]][1] != title:
            return None
        panes = matches(tree, ('landmark',), ('Conversation', '对话', '会话'))
        if len(panes) != 1 or matches(tree, ('scroll pane',), ('Side chat', '侧聊', '侧边聊天')):
            return None
        headings = matches(subtree(tree, panes[0]), ('heading',))
        if headings and headings[0] != previous:
            previous = headings[0]
            w.call(previous, A11Y + 'Component', 'ScrollTo', 'u', (2,))
        return None
    return wait_for(search, '原窗口未显示这条消息的编辑按钮', 9)

def edit_message(desktop, tid, payload):
    message = payload.get('text')
    if not isinstance(message, str) or not 0 < len(message.strip()) <= 32000 or (not isinstance(payload.get('originalText'), str)) or (not payload['originalText']) or (not all((isinstance(payload.get(k), str) and payload[k] for k in ('expectedLastTurnId', 'turnId', 'itemId')))):
        raise ValueError('编辑内容或消息标识无效')
    message = message.strip()
    title = unique_title(desktop.agent.home, tid)
    if not title:
        raise ValueError('无法在原 App 唯一确认对话标题')
    prefix = checked_edit(desktop, tid, payload)
    with window(desktop) as w:
        if matches(w.nodes(), ('dialog', 'menu')):
            raise ValueError('原 App 还有正在操作的表单')
        if not w.find(('document web',), (title,)):
            desktop.agent.open_desktop_thread(str(uuid.UUID(tid)))
        button = locate_edit(w, title, payload['originalText'])
        w.act(button)
        field = w.expect(('entry',), EDIT)
        original_field = w.field_text(field)
        if editable_text(original_field) != editable_text(payload['originalText']):
            raise ValueError('原 App 编辑框与所选消息不符')
        markers = list(REQUEST_MARKER.finditer(original_field))
        submitted = original_field[:markers[-1].end()] + '\n' + message if markers else message
        w.fill(field, original_field, submitted)
        rows = w.nodes()
        parent = rows[field][2]
        while parent and rows[parent][0] != 'document web':
            area = subtree(rows, parent)
            if len(matches(area, BUTTON)) == 2 and len(matches(area, BUTTON, CANCEL)) == 1 and (len(matches(area, BUTTON, SEND)) == 1):
                break
            parent = rows[parent][2]
        else:
            raise RuntimeError('无法确认原 App 的编辑表单')
        if checked_edit(desktop, tid, payload) != prefix:
            raise ValueError('编辑期间原历史已改变')
        if unique_title(desktop.agent.home, tid) != title or not w.find(('document web',), (title,)) or w.field_text(field) != submitted:
            raise RuntimeError('提交前原 App 目标已变化')
        w.act(w.expect(BUTTON, SEND, parent))

        def accepted():
            latest = desktop.owner.request('thread/turns/list', dict(threadId=tid, itemsView='full', sortDirection='desc', limit=1))['data']
            if not latest or latest[0]['id'] == payload['expectedLastTurnId']:
                return None
            inputs = [i for i in latest[0]['items'] if i['type'] == 'userMessage']
            from crb.codex.agent import text_content
            if len(inputs) != 1 or editable_text(text_content(inputs[0].get('content'))) != message:
                return None
            ids = stored_turn_ids(desktop, tid)
            if ids != [latest[0]['id'], *prefix]:
                return None
            return latest[0]['id']
        turn = wait_for(accepted, '已操作原 App，尚未收到目标消息回执，请刷新核对', 8)
        return dict(ok=True, transport='desktop-history-form', messageSent=True, historyReverted=True, turnId=turn)
