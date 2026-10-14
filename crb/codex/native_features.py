"""Typed, on-demand Desktop feature bridge. No scheduler or inference engine."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

def text(value, limit=4000):
    return str(value or '')[:limit]

def canonical_path(value):
    raw = str(value)
    if raw.startswith('\\\\?\\UNC\\'):
        raw = '\\\\' + raw[8:]
    elif raw.startswith('\\\\?\\'):
        raw = raw[4:]
    return Path(raw).resolve()

def usage(value):
    if not isinstance(value, dict):
        return None
    result = {}
    for target, names in {'inputTokens': ('inputTokens', 'input_tokens'), 'cachedInputTokens': ('cachedInputTokens', 'cached_input_tokens'), 'outputTokens': ('outputTokens', 'output_tokens'), 'reasoningOutputTokens': ('reasoningOutputTokens', 'reasoning_output_tokens'), 'totalTokens': ('totalTokens', 'total_tokens')}.items():
        for name in names:
            n = value.get(name)
            if isinstance(n, int) and (not isinstance(n, bool)) and (n >= 0):
                result[target] = n
                break
    return result or None

def public_metadata(state):
    """Only native counters; never infer token usage from characters."""
    value = state.get('tokenUsage') or state.get('latestTokenUsage') or {}
    if not isinstance(value, dict):
        value = {}
    result = {'total': usage(value.get('total')), 'last': usage(value.get('last'))}
    window = value.get('modelContextWindow')
    if isinstance(window, int) and window > 0:
        result['modelContextWindow'] = window
    history = state.get('turnHistory', {}).get('history', {})
    turns = list(history.get('entitiesByKey', {}).values()) if isinstance(history, dict) else []
    turns = turns if isinstance(turns, list) else []
    active = state.get('activeTurn') or state.get('latestTurn')
    if isinstance(active, dict):
        turns = [*turns, active]
    turns = sorted((t for t in turns if isinstance(t, dict)), key=lambda t: t.get('turnStartedAtMs') or 0)
    unique = {str(t.get('turnId') or t.get('id')): t for t in turns}
    result['turns'] = []
    for turn in list(unique.values())[-10:]:
        if not isinstance(turn, dict):
            continue
        item = {'id': text(turn.get('turnId') or turn.get('id'), 128), 'status': text(turn.get('status'), 40)}
        for target, names in {'startedAtMs': ('turnStartedAtMs', 'startedAtMs'), 'completedAtMs': ('turnCompletedAtMs', 'completedAtMs'), 'durationMs': ('durationMs',)}.items():
            for key in names:
                n = turn.get(key)
                if isinstance(n, (int, float)) and (not isinstance(n, bool)) and (n >= 0):
                    item[target] = n
                    break
        result['turns'].append(item)
    return result

def schedule_rule(value):
    if not isinstance(value, dict):
        raise ValueError('定时设置无效')
    mode = value.get('mode')
    if mode == 'interval':
        minutes = value.get('minutes')
        if not isinstance(minutes, int) or isinstance(minutes, bool) or (not 1 <= minutes <= 10080):
            raise ValueError('间隔应为 1–10080 分钟')
        return f'FREQ=MINUTELY;INTERVAL={minutes}'
    if mode in ('daily', 'weekly'):
        hour, minute = (value.get('hour'), value.get('minute'))
        if not isinstance(hour, int) or not isinstance(minute, int) or (not 0 <= hour <= 23) or (not 0 <= minute <= 59):
            raise ValueError('时间格式无效')
        day = value.get('day')
        if mode == 'weekly' and day not in ('MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU'):
            raise ValueError('星期无效')
        return f'FREQ={mode.upper()};BYHOUR={hour};BYMINUTE={minute}' + (f';BYDAY={day}' if mode == 'weekly' else '')
    raise ValueError('不支持的定时方式')

def public_schedule(rule):
    parts = dict(re.findall('(\\w+)=([^;\\s]+)', str(rule)))
    freq = parts.get('FREQ')
    if freq in ('MINUTELY', 'HOURLY') and set(parts) <= {'FREQ', 'INTERVAL'}:
        try:
            minutes = int(parts.get('INTERVAL', '1')) * (60 if freq == 'HOURLY' else 1)
            return {'mode': 'interval', 'minutes': minutes, 'label': f'每 {minutes} 分钟'}
        except ValueError:
            pass
    if freq in ('DAILY', 'WEEKLY') and set(parts) <= {'FREQ', 'BYHOUR', 'BYMINUTE', 'BYDAY'}:
        try:
            hour, minute = (int(parts['BYHOUR']), int(parts.get('BYMINUTE', '0')))
            day = parts.get('BYDAY', 'MO')
            days = {'MO': '一', 'TU': '二', 'WE': '三', 'TH': '四', 'FR': '五', 'SA': '六', 'SU': '日'}
            return {'mode': freq.lower(), 'hour': hour, 'minute': minute, 'day': day, 'label': ('每天' if freq == 'DAILY' else '每周' + days.get(day, day)) + f' {hour:02}:{minute:02}'}
        except (KeyError, ValueError):
            pass
    return {'mode': 'preserve', 'label': '原 App 定时安排（保留）'}

class NativeFeatures:

    def __init__(self, agent):
        self.agent = agent

    def rpc(self, method, params):
        if not self.agent.appserver:
            raise RuntimeError('目标机原生接口不可用')
        return self.agent.appserver.request(method, params, timeout=30)

    def tool(self, name, params, tid):
        if sys.platform == 'darwin':
            result = self.agent.call_macos_app_tool(name, params, tid)
        else:
            result = self.agent.app_tools.call(name, params, tid)
        return {'ok': True, 'transport': 'desktop-app-tool', 'confirmed': bool(result.get('success'))}

    def context(self, tid):
        with self.agent.lock:
            native = self.agent.states.get(tid, {}).get('state', {})
            state = {k: copy.deepcopy(native[k]) for k in ('cwd', 'latestThreadSettings', 'currentPermissions', 'tokenUsage', 'latestTokenUsage') if k in native}
            history = native.get('turnHistory', {}).get('history', {})
            turns = list(history.get('entitiesByKey', {}).values())
            turns += [native.get('activeTurn'), native.get('latestTurn')]
            turns = sorted((t for t in turns if isinstance(t, dict)), key=lambda t: t.get('turnStartedAtMs') or 0)
            state['turnHistory'] = {'history': {'entitiesByKey': {str(index): {k: turn[k] for k in ('id', 'turnId', 'status', 'turnStartedAtMs', 'turnCompletedAtMs', 'startedAtMs', 'completedAtMs', 'durationMs') if k in turn} for index, turn in enumerate(turns[-10:])}}}
            meta = next((row for row in self.agent.catalog if row['id'] == tid), None)
        if not meta:
            raise ValueError('没有找到目标对话')
        return (state, text(state.get('cwd') or meta.get('cwd'), 32768))

    def definitions(self):
        result = {}
        for path in sorted((self.agent.home / 'automations').glob('*/automation.toml'))[:500]:
            try:
                if path.stat().st_size > 128 * 1024:
                    continue
                data = tomllib.loads(path.read_text(encoding='utf-8'))
                row = data.get('automation', data)
                if not isinstance(row, dict):
                    continue
                row = dict(row)
                row['id'] = str(row.get('id') or path.parent.name)
                result[row['id']] = row
            except (OSError, ValueError):
                continue
        return result

    @staticmethod
    def task_thread(row):
        target = row.get('target') or {}
        return row.get('targetThreadId') or row.get('target_thread_id') or (target.get('threadId') or target.get('thread_id') if isinstance(target, dict) else None)

    def tasks(self, tid):
        result = []
        for row in self.definitions().values():
            if self.task_thread(row) != tid:
                continue
            result.append({'id': row['id'], 'name': text(row.get('name'), 200), 'prompt': text(row.get('prompt'), 32000), 'status': text(row.get('status'), 30), 'kind': text(row.get('kind') or 'heartbeat', 30), 'schedule': public_schedule(row.get('rrule')), 'notificationPolicy': row.get('notificationPolicy'), 'revision': digest(row)})
        return {'ok': True, 'tasks': result}

    def metadata(self, tid, state):
        result = public_metadata(state)
        source = self.agent.paths.get(tid)
        if source:
            path = canonical_path(source)
            if any((path.is_relative_to(canonical_path(self.agent.home / folder)) for folder in ('sessions', 'archived_sessions'))) and path.is_file():
                last_record = None
                with path.open('rb') as stream:
                    size = path.stat().st_size
                    if size > 2 * 1024 * 1024:
                        stream.seek(-2 * 1024 * 1024, 2)
                        stream.readline()
                    for line in stream:
                        try:
                            row = json.loads(line)
                            p = row.get('payload') or {}
                            if row.get('type') == 'event_msg' and p.get('type') == 'token_count':
                                info = p.get('info') or {}
                                total = usage(info.get('total_token_usage'))
                                last = usage(info.get('last_token_usage'))
                                if total is not None:
                                    result['total'] = total
                                if last is not None:
                                    result['last'] = last
                                n = info.get('model_context_window')
                                if isinstance(n, int) and n > 0:
                                    result['modelContextWindow'] = n
                            elif row.get('type') == 'token_usage_record' and p.get('thread_id') == tid:
                                last_record = p
                        except (ValueError, AttributeError):
                            continue
                if last_record:
                    for name, key in (('total', 'thread_token_usage'), ('last', 'usage'), ('turn', 'turn_token_usage')):
                        count = usage(last_record.get(key))
                        if count is not None:
                            result[name] = count
                    result['counterSource'] = 'native-token-usage-record'
        if not result['turns'] and self.agent.appserver:
            data = self.rpc('thread/turns/list', {'threadId': tid, 'limit': 10, 'sortDirection': 'desc', 'itemsView': 'notLoaded'})
            for turn in reversed(data.get('data', [])[:10]):
                item = {'id': text(turn.get('id'), 128), 'status': text(turn.get('status'), 30)}
                for key in ('durationMs', 'startedAt', 'completedAt'):
                    n = turn.get(key)
                    if isinstance(n, (int, float)) and (not isinstance(n, bool)) and (n >= 0):
                        item[key if key == 'durationMs' else key + 'Ms'] = n if key == 'durationMs' else n * 1000
                result['turns'].append(item)
        return {'ok': True, 'metadata': result, 'source': 'native-counters'}

    def skills(self, cwd):
        data = self.rpc('skills/list', {'cwds': [cwd] if cwd else [], 'forceReload': True})
        rows = []
        for group in data.get('data', []):
            for skill in group.get('skills', []):
                rows.append({k: skill[k] if isinstance(skill[k], bool) else text(skill[k], 5000) for k in ('name', 'path', 'enabled', 'description', 'shortDescription') if k in skill})
        return rows[:500]

    def config(self):
        return self.rpc('config/read', {'includeLayers': True})

    def tools(self, cwd):
        config = self.config().get('config', {})
        plugins = [{'id': name, 'enabled': row.get('enabled', True)} for name, row in (config.get('plugins') or {}).items() if isinstance(row, dict)][:150]
        servers = [{'name': name, 'enabled': row.get('enabled', True), 'transport': 'HTTP' if row.get('url') else 'stdio'} for name, row in (config.get('mcp_servers') or {}).items() if isinstance(row, dict) and name != 'codex_relay_native_bridge'][:150]
        return {'ok': True, 'skills': self.skills(cwd), 'plugins': plugins, 'servers': servers, 'pluginInstallSupported': False, 'notice': '技能/工具开关是当前电脑的原生配置，影响后续对话。插件/MCP 在后续轮次或原 App 刷新后生效。安装插件接口尚未开放。'}

    def review(self, cwd, payload):
        if not cwd or not Path(cwd).is_dir():
            raise ValueError('目标项目目录不可用')
        view = payload.get('view', 'unstaged')
        args = ['git', '--no-pager', '-C', cwd, 'diff', '--no-ext-diff', '--no-textconv']
        if view == 'staged':
            args += ['--cached']
        elif view == 'branch':
            base = text(payload.get('base'), 200)
            if not base or base.startswith('-') or (not re.fullmatch('[\\w./~^@{}-]+', base)):
                raise ValueError('基准分支无效')
            args += [base]
        elif view != 'unstaged':
            raise ValueError('审阅范围无效')
        path = text(payload.get('path'), 32768)
        args += ['--', path] if path else ['--']
        kwargs = {'cwd': cwd, 'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE}
        if os.name == 'nt':
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        import tempfile
        with tempfile.TemporaryFile() as err:
            kwargs['stderr'] = err
            process = subprocess.Popen(args, **kwargs)
            import threading
            timer = threading.Timer(15, process.kill)
            timer.start()
            try:
                raw = process.stdout.read(512 * 1024 + 1)
                truncated = len(raw) > 512 * 1024
                if truncated:
                    process.kill()
                process.wait(timeout=3)
                if process.returncode and (not truncated):
                    err.seek(0)
                    raise RuntimeError('Git 审阅失败：' + err.read(2000).decode('utf-8', errors='replace'))
                return {'ok': True, 'diff': raw[:512 * 1024].decode('utf-8', errors='replace'), 'truncated': truncated, 'cwd': cwd, 'view': view}
            finally:
                timer.cancel()
                if process.poll() is None:
                    process.kill()
                process.stdout.close()

    def execute(self, action, tid, payload):
        state, cwd = self.context(tid)
        operation = payload.get('operation')
        if action == 'native_read':
            if operation == 'refresh_thread':
                return self.agent.refresh_thread_snapshot(tid)
            if operation == 'tasks':
                return self.tasks(tid)
            if operation == 'permissions':
                result = self.rpc('permissionProfile/list', {'cwd': cwd or None, 'limit': 100})
                settings = {**(state.get('currentPermissions') or {}), **(state.get('latestThreadSettings') or {})}
                return {'ok': True, 'profiles': [{k: row.get(k) for k in ('id', 'description', 'allowed')} for row in result.get('data', [])[:100]], 'current': {k: settings.get(k) for k in ('permissions', 'activePermissionProfile', 'approvalPolicy', 'approvalsReviewer')}}
            if operation == 'tools':
                return self.tools(cwd)
            if operation == 'metadata':
                return self.metadata(tid, state)
            if operation == 'review':
                return self.review(cwd, payload)
            raise ValueError('不支持的原生读取操作')
        if action != 'native_action':
            raise ValueError('不支持的原生操作')
        if operation == 'task_create':
            name, prompt = (text(payload.get('name'), 200).strip(), text(payload.get('prompt'), 32000).strip())
            if not name or not prompt:
                raise ValueError('请输入任务名称和内容')
            args = {'mode': 'create', 'kind': 'heartbeat', 'destination': 'thread', 'targetThreadId': tid, 'name': name, 'prompt': prompt, 'rrule': schedule_rule(payload.get('schedule')), 'status': payload.get('status', 'ACTIVE')}
            if args['status'] not in ('ACTIVE', 'PAUSED'):
                raise ValueError('任务状态无效')
            if 'notificationPolicy' in payload:
                if payload['notificationPolicy'] not in (None, 'failed_runs_only'):
                    raise ValueError('通知策略无效')
                args['notificationPolicy'] = payload['notificationPolicy']
            return self.tool('automation_update', args, tid)
        if operation in ('task_update', 'task_delete'):
            row = self.definitions().get(str(payload.get('id')))
            if not row or self.task_thread(row) != tid:
                raise ValueError('目标定时任务不存在或不属于当前对话')
            if payload.get('revision') != digest(row):
                raise ValueError('任务已在原 App 更新，请刷新后重试')
            if operation == 'task_delete':
                return self.tool('automation_update', {'mode': 'delete', 'id': row['id']}, tid)
            allowed = ('name', 'prompt', 'rrule', 'status', 'kind', 'notificationPolicy', 'destination', 'targetThreadId', 'projectId', 'model', 'reasoningEffort', 'executionEnvironment', 'localEnvironmentConfigPath')
            args = {k: row[k] for k in allowed if k in row}
            args.update(mode='update', id=row['id'])
            args.setdefault('kind', 'heartbeat')
            args.setdefault('targetThreadId', tid)
            args.setdefault('destination', 'thread')
            for key, limit in (('name', 200), ('prompt', 32000)):
                if key in payload:
                    if not isinstance(payload[key], str) or not payload[key].strip() or len(payload[key]) > limit:
                        raise ValueError('任务内容格式错误')
                    args[key] = payload[key]
            if 'status' in payload:
                if payload['status'] not in ('ACTIVE', 'PAUSED'):
                    raise ValueError('任务状态无效')
                args['status'] = payload['status']
            if 'notificationPolicy' in payload:
                if payload['notificationPolicy'] not in (None, 'failed_runs_only'):
                    raise ValueError('通知策略无效')
                args['notificationPolicy'] = payload['notificationPolicy']
            if payload.get('schedule', {}).get('mode') not in (None, 'preserve'):
                args['rrule'] = schedule_rule(payload['schedule'])
            return self.tool('automation_update', args, tid)
        if operation == 'skill_enabled':
            if not isinstance(payload.get('enabled'), bool):
                raise ValueError('技能状态无效')
            skill = next((s for s in self.skills(cwd) if s['path'] == payload.get('path')), None)
            if not skill:
                raise ValueError('原 App 已没有这个技能，请刷新')
            self.rpc('skills/config/write', {'path': skill['path'], 'enabled': payload['enabled']})
            return {'ok': True, 'transport': 'codex-native-config'}
        if operation in ('plugin_enabled', 'mcp_enabled'):
            group = 'plugins' if operation == 'plugin_enabled' else 'mcp_servers'
            name = str(payload.get('name') or '')
            config = self.config()
            entries = config.get('config', {}).get(group) or {}
            if name not in entries or not isinstance(payload.get('enabled'), bool):
                raise ValueError('目标原生配置不存在')
            current = entries[name].get('enabled', True)
            if payload.get('expectedEnabled') != current:
                raise ValueError('配置已变化，请刷新后重试')
            layer = next((row for row in config.get('layers') or [] if row.get('name', {}).get('type') == 'user' and (not row.get('name', {}).get('profile'))), {})
            saved = self.rpc('config/value/write', {'keyPath': f'{group}.{json.dumps(name)}.enabled', 'value': payload['enabled'], 'mergeStrategy': 'replace', 'expectedVersion': layer.get('version') or config.get('version')})
            return {'ok': True, 'transport': 'codex-native-config', 'nextTurn': True, 'warning': '已保存，但有更高优先级的原生配置覆盖此设置。' if saved.get('status') == 'okOverridden' else ''}
        if operation == 'review_open_native':
            view = payload.get('view', 'unstaged')
            if view not in ('last-turn', 'branch', 'unstaged', 'staged'):
                raise ValueError('审阅范围无效')
            target = {'type': 'review', 'view': view}
            if payload.get('path'):
                target['path'] = text(payload['path'], 32768)
            if view == 'branch' and payload.get('base'):
                target['baseBranch'] = text(payload['base'], 200)
            return self.tool('open_in_codex', {'target': target, 'threadId': tid}, tid)
        raise ValueError('不支持的原生管理操作')
