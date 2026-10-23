"""Explicit, on-demand sidebar operations through the original Desktop tools."""
from contextlib import contextmanager
import copy
import hashlib
import json
from crb.codex.projects import desktop_project_id, local_projects
from crb.codex.sidebar_ui import call as ui_call, capabilities as ui_capabilities
BUILTINS = frozenset(('pinned', 'threads', 'projects', 'chats', 'orbit', 'agents'))

def tool_json(result):
    if not isinstance(result, dict) or result.get('success') is not True:
        raise RuntimeError('原 App 未确认操作')
    for item in result.get('contentItems', []):
        value = item.get('text')
        if item.get('type') == 'inputText' and isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return parsed
            except ValueError:
                pass
    raise TimeoutError('原 App 没有返回可核验的结果；请刷新核对，不要重复提交')

def revision(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 120:
        raise ValueError('名称应为 1–120 个字符')
    return value.strip()

class NativeSidebar:

    def __init__(self, agent):
        self.agent = agent

    @contextmanager
    def connection(self, tid, *, allow_create=False):
        with self.agent.lock:
            ids = [row['id'] for row in self.agent.catalog]
        caller = tid if tid in ids else next(iter(ids), '')
        if not caller and (not allow_create):
            raise RuntimeError('暂无可读取的对话上下文，请先新建对话后刷新分区')
        if not self.agent.ipc or not self.agent.ipc.connected:
            raise RuntimeError('请先连接目标 Codex Desktop')
        with self.agent.native_creation_context(caller) as (call, caller, cleanup):
            yield (lambda tool, args: tool_json(call(tool, args, caller, timeout=60)))

    def snapshot(self, call):
        raw = call('list_threads', {'limit': 1})
        native_projects = local_projects(call('list_projects', {}))
        with self.agent.lock:
            projects = copy.deepcopy(self.agent.projects)
            threads = {row['id'] for row in self.agent.catalog}
        native_ids = {}
        for project in projects:
            try:
                native_ids[project['id']] = desktop_project_id(self.agent.home, project, native_projects, projects)
            except RuntimeError:
                pass
        reverse = {v: k for k, v in native_ids.items()}
        rows = raw.get('sections')
        if not isinstance(rows, list) or len(rows) > 1000:
            raise RuntimeError('原 App 分区列表无效')
        sections, seen = ([], set())
        for row in rows:
            sid = row.get('sectionId')
            keys = row.get('itemKeys')
            if not isinstance(sid, str) or not sid or sid in seen or (not isinstance(keys, list)):
                raise RuntimeError('原 App 分区标识无效')
            seen.add(sid)
            sections.append({'id': sid, 'name': str(row.get('name') or sid)[:500], 'custom': sid not in BUILTINS, 'items': [{'type': 'thread', 'id': key[19:]} if key.startswith('codex:thread:local:') else {'type': 'project', 'id': reverse[key[14:]]} for key in keys if isinstance(key, str) and (key.startswith('codex:thread:local:') and key[19:] in threads or (key.startswith('codex:project:') and key[14:] in reverse))], 'threadIds': [key[19:] for key in keys if isinstance(key, str) and key.startswith('codex:thread:local:') and (key[19:] in threads)], 'projectIds': [reverse[key[14:]] for key in keys if isinstance(key, str) and key.startswith('codex:project:') and (key[14:] in reverse)]})
        preferences = raw.get('sidebarPreferences') or {}
        state = {'sections': sections, 'projectNativeIds': native_ids, 'preferences': preferences, 'capabilities': ui_capabilities(self.agent) if hasattr(self.agent, 'app_tools') else {}}
        state['revision'] = revision([rows, native_ids, preferences])
        return (state, rows)

    def execute(self, action, tid, payload):
        operation = payload.get('operation')
        with self.connection(tid, allow_create=action == 'native_action') as call:
            state, raw = self.snapshot(call)
            if action == 'native_read' and operation == 'sidebar':
                if payload.get('emojiThreadId') == tid and tid and any((row['id'] == tid for row in self.agent.catalog)):
                    emoji = call('get_thread_emoji', {'threadId': tid}).get('emoji')
                    if emoji is None or isinstance(emoji, str):
                        state['threadEmojis'] = {tid: emoji}
                return {'ok': True, 'sidebar': state}
            if action != 'native_action':
                raise ValueError('不支持的分区操作')
            if payload.get('revision') != state['revision']:
                raise ValueError('分区已在原 App 更新，请刷新后重试')
            sections = {row['id']: row for row in state['sections']}
            sid = payload.get('sectionId')
            if operation in ('sidebar_section_rename', 'sidebar_section_delete'):
                if sid not in sections or not sections[sid]['custom']:
                    raise ValueError('只能修改自建分区')
                args = {'sectionId': sid}
                if operation.endswith('rename'):
                    args['name'] = name(payload.get('name'))
                call('rename_sidebar_section' if operation.endswith('rename') else 'delete_sidebar_section', args)
            elif operation == 'sidebar_section_create':
                call('create_sidebar_section', {'name': name(payload.get('name'))})
            elif operation in ('sidebar_move_thread', 'sidebar_move_project'):
                if sid is not None and sid not in sections:
                    raise ValueError('目标分区不存在')
                if operation.endswith('thread'):
                    with self.agent.lock:
                        valid = any((row['id'] == tid for row in self.agent.catalog))
                    if not valid:
                        raise ValueError('没有找到目标对话')
                    call('move_thread_to_sidebar_section', {'source': 'codex', 'threadId': tid, 'sectionId': sid})
                else:
                    pid = state['projectNativeIds'].get(payload.get('projectId'))
                    if not pid:
                        raise ValueError('项目尚未完成原 App 登记')
                    call('move_project_to_sidebar_section', {'projectId': pid, 'sectionId': sid})
            elif operation == 'sidebar_reorder_sections':
                order = payload.get('sectionIds')
                if not isinstance(order, list) or len(order) != len(set(order)) or set(order) != set(sections):
                    raise ValueError('分区顺序已变化，请刷新')
                call('reorder_sidebar_sections', {'sectionIds': order})
            elif operation == 'sidebar_reorder_projects':
                order = payload.get('projectIds')
                default = next((row['projectIds'] for row in state['sections'] if row['id'] in ('threads', 'projects')), [])
                if not isinstance(order, list) or len(order) != len(set(order)) or set(order) != set(default):
                    raise ValueError('项目顺序已变化，请刷新')
                call('reorder_sidebar_projects', {'projectIds': [state['projectNativeIds'][pid] for pid in order]})
            elif operation == 'sidebar_reorder_items':
                row = sections.get(sid)
                order = payload.get('projectIds')
                if not state.get('capabilities', {}).get('mixedOrdering') or not row or (not row['custom']):
                    raise ValueError('当前原 App 版本未提供混合分区排序，请在原 App 调整')
                if not isinstance(order, list) or len(order) != len(set(order)) or set(order) != set(row['projectIds']):
                    raise ValueError('项目顺序已变化，请刷新')
                before = next((item['itemKeys'] for item in raw if item['sectionId'] == sid))
                owned = {'codex:project:' + state['projectNativeIds'][pid] for pid in order}
                iterator = iter(('codex:project:' + state['projectNativeIds'][pid] for pid in order))
                after = [next(iterator) if key in owned else key for key in before]
                ui_call({'operation': 'reorder-items', 'sectionId': sid, 'before': before, 'after': after})
            elif operation == 'sidebar_reorder_threads':
                order = payload.get('threadIds')
                row = sections.get(sid)
                if not row or (not row['custom'] and sid != 'pinned') or (not isinstance(order, list)) or (len(order) != len(set(order))) or (set(order) != set(row['threadIds'])):
                    raise ValueError('对话顺序已变化，请刷新')
                keys = next((item['itemKeys'] for item in raw if item['sectionId'] == sid))
                iterator = iter(order)
                all_threads = []
                for key in keys:
                    if key.startswith('codex:thread:local:'):
                        original = key[19:]
                        all_threads.append(next(iterator) if original in row['threadIds'] else original)
                    elif key.startswith('chatgpt:thread:'):
                        all_threads.append(key[15:])
                    elif ':thread:' in key:
                        raise ValueError('此分区包含其他远程主机，请在原 App 调整顺序')
                call('reorder_section', {'sectionId': sid, 'threadIds': all_threads})
            elif operation == 'sidebar_preferences':
                args = {}
                if 'grouping' in payload:
                    if payload['grouping'] not in ('project', 'connection', 'list'):
                        raise ValueError('分组方式无效')
                    args['grouping'] = {'mode': payload['grouping'], 'surface': 'codex'}
                if 'sorting' in payload:
                    sorting = payload['sorting']
                    if not isinstance(sorting, dict) or not sorting or set(sorting) - {'chats', 'pinned'} or any((v not in ('manual', 'updated_at') for v in sorting.values())):
                        raise ValueError('排序方式无效')
                    args['sorting'] = sorting
                if not args:
                    raise ValueError('请选择分组或排序方式')
                call('update_sidebar_preferences', args)
            elif operation == 'sidebar_read_state':
                if not any((row['id'] == tid for row in self.agent.catalog)) or not isinstance(payload.get('read'), bool):
                    raise ValueError('没有找到目标对话')
                call('set_thread_read_state', {'source': 'codex', 'threadId': tid, 'read': payload['read']})
            elif operation in ('sidebar_archive_preview', 'sidebar_archive_project'):
                pid = payload.get('projectId')
                if pid not in state['projectNativeIds']:
                    raise ValueError('项目尚未完成原 App 登记')
                rows, cursor, cursors = ([], None, set())
                for _ in range(20):
                    page = self.agent.appserver.request('thread/list', {'projectId': pid, 'archived': False, 'limit': 100, 'cursor': cursor, 'useStateDbOnly': True}, timeout=30)
                    rows.extend(page.get('data', []))
                    cursor = page.get('nextCursor')
                    if not cursor:
                        break
                    if cursor in cursors:
                        raise RuntimeError('原 App 的项目对话分页无效')
                    cursors.add(cursor)
                else:
                    raise ValueError('项目对话超过本次操作上限，请在原 App 分批归档')
                active = {row['id'] for row in self.agent.catalog if row.get('status') == 'active'}
                active.update((row['id'] for row in rows if isinstance(row.get('status'), dict) and row['status'].get('type') == 'active'))
                rows = list({row['id']: row for row in rows}.values())
                idle = {row['id'] for row in self.agent.catalog if row.get('status') == 'idle'}
                idle.update((row['id'] for row in rows if isinstance(row.get('status'), dict) and row['status'].get('type') == 'idle'))
                ids = [row['id'] for row in rows if row['id'] in idle and row['id'] not in active]
                token = revision([sorted(ids), sorted(active & {row['id'] for row in rows})])
                preview = {'count': len(ids), 'activeCount': len(active & {row['id'] for row in rows}), 'preservedCount': len(rows) - len(ids), 'archiveRevision': token}
                if operation == 'sidebar_archive_preview':
                    return {'ok': True, **preview, 'sidebar': state}
                if payload.get('archiveRevision') != token:
                    raise ValueError('项目对话已变化，请重新确认归档范围')
                completed, warning = ([], None)
                for thread_id in ids:
                    if any((row['id'] == thread_id and row.get('status') == 'active' for row in self.agent.catalog)):
                        warning = '对话开始运行，已停止后续归档'
                        break
                    try:
                        call('set_thread_archived', {'source': 'codex', 'threadId': thread_id, 'archived': True})
                        completed.append(thread_id)
                    except Exception as exc:
                        warning = str(exc)
                        break
                return {'ok': True, 'archivedCount': len(completed), 'remainingCount': len(ids) - len(completed), 'warning': warning, 'sidebar': state}
            elif operation == 'sidebar_emoji':
                if not any((row['id'] == tid for row in self.agent.catalog)):
                    raise ValueError('没有找到目标对话')
                emoji = payload.get('emoji')
                if emoji is not None and (not isinstance(emoji, str) or not emoji.strip() or len(emoji) > 32):
                    raise ValueError('图标格式无效')
                call('set_thread_emoji', {'threadId': tid, 'emoji': emoji})
                state['threadEmojis'] = {tid: call('get_thread_emoji', {'threadId': tid}).get('emoji')}
            elif operation == 'sidebar_share':
                if not any((row['id'] == tid for row in self.agent.catalog)):
                    raise ValueError('没有找到目标对话')
                shared = call('share_thread', {'threadId': tid})
                return {'ok': True, 'share': shared, 'sidebar': state}
            else:
                raise ValueError('不支持的分区操作')
            fresh, _ = self.snapshot(call)
            if 'threadEmojis' in state:
                fresh['threadEmojis'] = state['threadEmojis']
            return {'ok': True, 'transport': 'desktop-app-tools', 'sidebar': fresh}
