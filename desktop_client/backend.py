from __future__ import annotations
import json
import logging
import os
import re
import socket
import time
import datetime
import hashlib
import zipfile
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
import psutil

class ClientError(RuntimeError):
    pass

def normalized_path(value):
    path = os.path.normcase(os.path.abspath(str(value)))
    return path.casefold() if sys.platform == 'win32' else path

def redact(text: str) -> str:
    text = re.sub('\\bsk-[A-Za-z0-9_-]+', '[密钥已隐藏]', text)
    text = re.sub('(?i)(Bearer\\s+)\\S+', '\\1[已隐藏]', text)
    text = re.sub('(?i)((?:["\']?)(?:agent_token|api_key|password|authorization|cookie|access_token|refresh_token)(?:["\']?)\\s*[:=]\\s*)(?:"[^"\\r\\n]*"|\'[^\'\\r\\n]*\'|[^\\s,;\\r\\n]+)', '\\1[已隐藏]', text)
    text = re.sub('(?i)([?&](?:token|key|secret|password)=)[^&#\\s]+', '\\1[已隐藏]', text)
    return text

@dataclass(frozen=True)
class BridgeSpec:
    key: str
    title: str
    task_name: str
    launcher: Path
    executable: Path
    config: Path
    log: Path
    managed_executable: Path | None = None
    runtime_file: Path | None = None
    managed_config: Path | None = None

def bridge_specs(home=None, local=None):
    home = Path(home or Path.home())
    root = home / 'codex-desktop-relay'
    cfg = home / '.config' / 'codex-desktop-relay'
    if sys.platform != 'win32':
        from desktop_client.connections import ConnectionStore
        from desktop_client.platform_support import data_root, installed_executable
        client = Path(local) if local else data_root(home=home)
        store = ConnectionStore(client, home)
        codex_launcher = root / 'agent.py'
        if (root / 'agents/local/agent.py').exists():
            codex_launcher = root / 'agents/local/agent.py'
        return {key: BridgeSpec(key, title, 'relay-client-' + key, codex_launcher if key == 'codex' else root / 'applications/deepseek/run_harness_posix.py', Path(sys.executable), store.legacy_path(key), client / ('runtime/' + key + '.log'), installed_executable(client), client / ('runtime/' + key + '.json'), store.path(key)) for key, title in [('codex', 'Codex'), ('deepseek', 'DeepSeek')]}
    local = Path(local or os.environ.get('LOCALAPPDATA', home / 'AppData/Local'))
    state = local / 'codex-desktop-relay'
    from desktop_client.connections import client_root
    client = local / 'CodexRelayClient' if local != Path(os.environ.get('LOCALAPPDATA', '')) else client_root()
    suffix = ' - SetupTest ' + hashlib.sha256(str(client).encode()).hexdigest()[:8] if client.name.startswith('CodexRelayClient-SetupTest-') else ''
    codex_cfg, harness_cfg = (cfg / 'agent.json', cfg / 'harness-agent.json')
    if not codex_cfg.exists() and (cfg / 'crb.codex.agent.json').exists():
        codex_cfg = cfg / 'crb.codex.agent.json'
    if suffix:
        codex_cfg, harness_cfg = (client / 'legacy-not-imported/codex.json', client / 'legacy-not-imported/deepseek.json')
        state = client / 'bridge-data'
    return {'codex': BridgeSpec('codex', 'Codex', 'Codex Desktop Relay Agent' + suffix, root / 'run-agent.pyw', Path('C:/Python314/pythonw.exe'), codex_cfg, state / 'agent.log', client / 'app/RelayClient.exe', client / 'runtime/codex.json', client / 'connections/codex.json'), 'deepseek': BridgeSpec('deepseek', 'DeepSeek', 'DeepSeek Harness Relay Agent' + suffix, root / 'run-harness.pyw', root / 'harness-venv/Scripts/pythonw.exe', harness_cfg, state / 'harness/bridge.log', client / 'app/RelayClient.exe', client / 'runtime/deepseek.json', client / 'connections/deepseek.json')}

def read_public_config(spec):
    """Whitelist public fields: never return a token to Qt or diagnostic output."""
    if spec.managed_config:
        from desktop_client.connections import ConnectionStore
        store = ConnectionStore(spec.managed_config.parent.parent)
        if store.unpaired(spec.key) or store.disconnect_pending(spec.key):
            raise FileNotFoundError('已解除连接，请重新配对。')
    path = spec.managed_config if spec.managed_config and spec.managed_config.exists() else spec.config
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    return {'host_id': str(data.get('host_id', '')), 'label': str(data.get('label', '')), 'relay_url': str(data.get('relay_url', '')), 'paired': bool(data.get('agent_token'))}

def matching_processes(spec, rows):
    """Require exact launcher argument; a text mention in another process is not enough."""
    out = []
    for row in rows:
        cmd = row.get('cmdline') or []
        if not cmd:
            continue
        if spec.managed_executable and len(cmd) == 3 and (cmd[1:] == ['--bridge', spec.key]) and (normalized_path(row.get('exe') or cmd[0]) == normalized_path(spec.managed_executable)):
            out.append({'pid': row['pid'], 'ppid': row.get('ppid', 0), 'created': row.get('create_time', 0), 'packaged': True})
            continue
        if not str(row.get('name', '')).lower().startswith('python'):
            continue
        if sys.platform != 'win32':
            args = cmd[1:]
            if args[:1] == ['-u']:
                args = args[1:]
            if len(args) == 3 and normalized_path(args[0]) == normalized_path(spec.launcher) and (args[1] == '--config') and (normalized_path(args[2]) == normalized_path(spec.config)):
                out.append({'pid': row['pid'], 'ppid': row.get('ppid', 0), 'created': row.get('create_time', 0)})
            continue
        if len(cmd) == 2 and normalized_path(cmd[1]) == normalized_path(spec.launcher):
            exe = row.get('exe') or cmd[0]
            if normalized_path(exe) in {normalized_path(spec.executable), normalized_path('C:/Python314/pythonw.exe')}:
                out.append({'pid': row['pid'], 'ppid': row.get('ppid', 0), 'created': row.get('create_time', 0)})
    ids = {r['pid'] for r in out}
    roots = [r for r in out if r['ppid'] not in ids]
    return (out, roots)

def process_rows():
    clear = getattr(psutil.process_iter, 'cache_clear', None)
    if clear:
        clear()
    from desktop_client.process_snapshot import iter_process_info
    rows = []
    for proc, info in iter_process_info(['pid', 'ppid', 'name', 'exe', 'create_time']):
        try:
            name = str(info.get('name', '')).lower()
            info['cmdline'] = proc.cmdline() if name.startswith('python') or name in {'relayclient.exe', 'relayclient', 'relayserver.exe', 'relayserver'} else []
            rows.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return rows

def request_bridge_stop(spec, timeout=7):
    """Best-effort graceful stop, identified from live processes, not telemetry."""
    if not spec.runtime_file:
        return
    _, roots = matching_processes(spec, process_rows())
    targets = [p for p in roots if p.get('packaged')]
    if len(targets) != 1:
        return
    target = targets[0]
    from desktop_client.connections import atomic_json
    atomic_json(spec.runtime_file.with_name(spec.key + '.stop.json'), {'pid': target['pid'], 'created': target['created']})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if psutil.Process(target['pid']).create_time() != target['created']:
                break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            break
        time.sleep(0.2)

def native_app_running(key, rows):
    for row in rows:
        exe = str(row.get('exe', '')).replace('\\', '/').lower()
        name = str(row.get('name', '')).lower()
        if key == 'claude' and name in {'claude.exe', 'claude'} and ('/app/claude.exe' in exe or '/claude.app/' in exe or '/opt/claude/' in exe):
            return True
        if key == 'deepseek' and name in {'deepseek harness', 'deepseek-harness', 'dsh-desktop'}:
            return True
        if key == 'codex' and sys.platform != 'win32':
            if name == 'chatgpt' and ('/chatgpt.app/contents/macos/chatgpt' in exe or '/usr/lib/chatgpt/chatgpt' in exe):
                return True
            if name == 'codex' and ('/codex.app/contents/macos/codex' in exe or '/opt/codex/' in exe or '/usr/lib/codex/' in exe):
                return True
        if key == 'deepseek' and name == 'deepseek harness.exe':
            return True
        if key == 'codex' and name in {'chatgpt.exe', 'codex.exe'} and ('/app/chatgpt.exe' in exe or '/app/codex.exe' in exe):
            return True
    return False

def native_app_identity(key, rows):
    """Stable GUI roots, not renderer churn, for external App launch detection."""
    matching = [row for row in rows if native_app_running(key, [row])]
    pids = {row['pid'] for row in matching}
    return sorted((f"{row['pid']}:{row.get('create_time', '')}" for row in matching if row.get('ppid') not in pids))

def windows_task_error(error, stage):
    """Useful COM failure location without dumping account, XML or paths."""
    if isinstance(error, ClientError):
        return error
    codes = []
    candidates = [getattr(error, 'hresult', None)]
    info = getattr(error, 'excepinfo', None)
    if isinstance(info, tuple) and len(info) > 5:
        candidates.append(info[5])
    for value in candidates:
        if isinstance(value, int) and (not isinstance(value, bool)):
            codes.append(f'0x{value & 4294967295:08X}')
    detail = ' / '.join(dict.fromkeys(codes)) or type(error).__name__
    return ClientError('Windows 计划任务失败：' + stage + '（' + detail + '）。请检查 Task Scheduler 服务及其事件日志；不要反复点击启动。')

class WindowsTasks:
    """Task Scheduler COM: no periodic cmd/PowerShell windows and one process owner."""

    def exists(self, spec):
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        service = folder = collection = task = error = None
        stage = 'Schedule.Service'
        try:
            service = win32com.client.Dispatch('Schedule.Service')
            stage = 'Connect'
            service.Connect()
            stage = 'GetFolder'
            folder = service.GetFolder('\\')
            stage = 'GetTasks'
            collection = folder.GetTasks(1)
            stage = 'EnumerateTasks'
            return any((str(task.Name) == spec.task_name for task in collection))
        except Exception as exc:
            error = windows_task_error(exc, stage)
            exc.__traceback__ = None
        finally:
            task = collection = folder = service = None
            pythoncom.CoUninitialize()
        if error:
            raise error from None

    def create(self, spec):
        if not spec.managed_executable or not spec.managed_executable.exists():
            raise ClientError('没有找到内置客户端 EXE。')
        if self.exists(spec):
            raise ClientError('同名计划任务已存在，未覆盖。')
        import win32api
        import win32security
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
        try:
            sid = win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])
        finally:
            token.Close()
        self.replace_xml(spec, fresh_task_xml(spec, sid), flags=2)

    def delete_test_task(self, spec):
        if ' - SetupTest ' not in spec.task_name or not spec.managed_executable.parent.parent.name.startswith('CodexRelayClient-SetupTest-'):
            raise ClientError('拒绝删除非验收任务。')
        self.inspect(spec)
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        service = folder = None
        try:
            service = win32com.client.Dispatch('Schedule.Service')
            service.Connect()
            folder = service.GetFolder('\\')
            folder.DeleteTask(spec.task_name, 0)
        finally:
            folder = service = None
            pythoncom.CoUninitialize()

    def remove(self, spec):
        """Remove only an inspected bridge entry after it has been stopped."""
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        service = folder = task = None
        try:
            service = win32com.client.Dispatch('Schedule.Service')
            service.Connect()
            folder = service.GetFolder('\\')
            task = folder.GetTask(spec.task_name)
            self.validate(task, spec)
            if int(task.State) == 4 or bool(task.Enabled):
                raise ClientError('后台仍运行或启用，未移除启动任务。')
            folder.DeleteTask(spec.task_name, 0)
        finally:
            task = folder = service = None
            pythoncom.CoUninitialize()

    def enable(self, spec, enabled):
        self.invoke(spec, lambda task: setattr(task, 'Enabled', bool(enabled)))

    def invoke(self, spec, callback):
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        service = task = None
        error = None
        result = None
        stage = 'Schedule.Service'
        try:
            service = win32com.client.Dispatch('Schedule.Service')
            stage = 'Connect'
            service.Connect()
            stage = 'GetFolder'
            folder = service.GetFolder('\\')
            stage = 'GetTask'
            task = folder.GetTask(spec.task_name)
            stage = 'ValidateTask'
            self.validate(task, spec)
            stage = 'TaskOperation'
            result = callback(task)
        except Exception as exc:
            error = windows_task_error(exc, stage)
            exc.__traceback__ = None
        finally:
            task = folder = service = None
            pythoncom.CoUninitialize()
        if error:
            raise error from None
        return result

    @staticmethod
    def validate(task, spec):
        actions = task.Definition.Actions
        if actions.Count != 1:
            raise ClientError('计划任务入口不匹配，已禁止控制，避免影响其他程序。')
        action = actions.Item(1)
        argument = str(action.Arguments).strip()
        if argument.startswith('"') and argument.endswith('"'):
            argument = argument[1:-1]
        if int(action.Type) == 0 and spec.managed_executable and (normalized_path(action.Path) == normalized_path(spec.managed_executable)) and (str(action.Arguments).strip() == '--bridge ' + spec.key):
            return
        if int(action.Type) != 0 or normalized_path(action.Path) != normalized_path(spec.executable) or normalized_path(argument) != normalized_path(spec.launcher):
            raise ClientError('计划任务入口不匹配，已禁止控制，避免影响其他程序。')

    def inspect(self, spec):
        return self.invoke(spec, lambda task: {'state': int(task.State), 'enabled': bool(task.Enabled), 'last_result': int(task.LastTaskResult), 'mode': 'packaged' if spec.managed_executable and normalized_path(task.Definition.Actions.Item(1).Path) == normalized_path(spec.managed_executable) else 'legacy'})

    def xml(self, spec):
        return self.invoke(spec, lambda task: str(task.Xml))

    def replace_xml(self, spec, xml, flags=6):
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        service = folder = registered = error = None
        stage = 'Schedule.Service'
        try:
            service = win32com.client.Dispatch('Schedule.Service')
            stage = 'Connect'
            service.Connect()
            stage = 'GetFolder'
            folder = service.GetFolder('\\')
            stage = 'RegisterTask'
            registered = folder.RegisterTask(spec.task_name, xml, flags, None, None, 3)
        except Exception as exc:
            error = windows_task_error(exc, stage)
            exc.__traceback__ = None
        finally:
            registered = folder = service = None
            pythoncom.CoUninitialize()
        if error:
            raise error from None

    def pause(self, spec):

        def action(task):
            was_enabled = bool(task.Enabled)
            task.Enabled = False
            try:
                try:
                    request_bridge_stop(spec)
                except (OSError, ValueError):
                    logging.warning('本地停止请求暂不可用；继续停止已核验的桥接任务')
                task.Stop(0)
            except Exception:
                task.Enabled = was_enabled
                raise
        self.invoke(spec, action)

    def start(self, spec):

        def action(task):
            task.Enabled = True
            task.Run(None)
        self.invoke(spec, action)

class BridgeManager:

    def __init__(self, specs=None, tasks=None, processes=None, sleep=time.sleep, clock=time.monotonic):
        self.specs = specs or bridge_specs()
        if tasks is None and sys.platform != 'win32':
            from desktop_client.posix_backend import PosixTasks
            tasks = PosixTasks()
        self.tasks = tasks or WindowsTasks()
        self.processes = processes or process_rows
        self.sleep, self.clock = (sleep, clock)

    def status(self):
        rows = self.processes()
        result = []
        for spec in self.specs.values():
            item = {'key': spec.key, 'title': spec.title, 'error': '', 'controllable': False, 'app_running': native_app_running(spec.key, rows)}
            procs, roots = matching_processes(spec, rows)
            item.update(pids=[p['pid'] for p in procs], instances=len(roots))
            item['telemetry'] = valid_telemetry(spec, procs)
            telemetry = item['telemetry']
            item['relay_online'] = bool(telemetry.get('last_sync_at') and (not telemetry.get('last_error')) and (telemetry.get('phase') == 'running') and (time.time() - telemetry['last_sync_at'] < 90))
            try:
                item['config'] = read_public_config(spec)
                if hasattr(self.tasks, 'exists') and (not self.tasks.exists(spec)):
                    item.update(needs_setup=False, needs_repair=True, enabled=False)
                else:
                    item.update(self.tasks.inspect(spec))
                    item['controllable'] = True
            except FileNotFoundError:
                item.update(needs_setup=True, enabled=False)
                if spec.managed_config:
                    from desktop_client.connections import ConnectionStore
                    store = ConnectionStore(spec.managed_config.parent.parent)
                    if store.disconnect_pending(spec.key):
                        item.update(pairing_cancelled=True, unpair_pending=True, unpair_cleanup_pending=True)
                        try:
                            item['remote_revoked'] = store.disconnect_record(spec.key)['remoteRevoked']
                        except (OSError, ValueError):
                            item.update(pairing_record_error=True, unpair_pending=False, error='解除配对操作记录无法读取；不会恢复旧授权。')
                    elif store.unpaired(spec.key):
                        item.update(pairing_cancelled=True, unpair_pending=False)
                        try:
                            marker = store.pairing_marker(spec.key)
                            item['unpair_pending'] = not marker.get('remoteRevoked', False)
                        except (OSError, ValueError):
                            item.update(pairing_record_error=True, error='解除配对记录无法读取，请检查诊断日志；不会恢复旧配置或授权。')
            except Exception as exc:
                item['error'] = redact(str(exc))
            result.append(item)
        return result

    def install(self, key):
        from desktop_client.connections import ConnectionStore, client_root
        spec = self.specs[key]
        store = ConnectionStore(spec.managed_config.parent.parent, Path.home() if sys.platform != 'win32' else spec.config.parent.parent.parent) if spec.managed_config else ConnectionStore()
        if store.disconnect_pending(key):
            return spec.title + ' 解除配对尚未完成，请继续解除；不会恢复旧授权。'
        if store.unpaired(key):
            return spec.title + ' 已解除连接，请重新配对；不会恢复旧授权。'
        if getattr(self.tasks, 'app_owned', False):
            store.import_existing(key)
            if not self.tasks.exists(spec):
                self.tasks.create(spec)
            self.control(key, 'start')
            return spec.title + ' 已由客户端接入；退出客户端即断开。'
        if not self.tasks.exists(spec):
            if not spec.managed_config or not spec.managed_config.exists():
                if not spec.config.exists():
                    return spec.title + ' 尚未配对，请使用“首次接入 / 添加应用”。'
                store.import_existing(key)
            self.tasks.create(spec)
            self.control(key, 'start')
            return spec.title + ' 已建立内置后台任务并启动，配置与配对已保留。'
        before = self.tasks.inspect(spec)
        if sys.platform != 'win32':
            if before.get('mode') == 'legacy':
                self.tasks.migrate(spec, store)
                return spec.title + ' 已接管为独立客户端桥接，保留原配对与数据。'
            return spec.title + ' 已使用内置桥接后台服务。'
        if before.get('mode') == 'packaged':
            original = self.tasks.xml(spec)
            if 'RelayClientRecovery' not in original:
                backup = client_root() / 'backups' / (key + '-task-recovery-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.xml')
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_text(original, encoding='utf-8')
                try:
                    self.tasks.replace_xml(spec, packaged_task_xml(original, spec))
                except Exception:
                    self.tasks.replace_xml(spec, original)
                    raise
            return f'{spec.title} 已使用内置 EXE 桥接，已配置本地后台保活。'
        if not spec.managed_executable or not spec.managed_executable.exists():
            raise ClientError('请先安装完整客户端。')
        store.import_existing(key)
        original = self.tasks.xml(spec)
        backup = client_root() / 'backups' / (key + '-task-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.xml')
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(original, encoding='utf-8')
        candidate = packaged_task_xml(original, spec)
        running = bool(matching_processes(spec, self.processes())[0])
        try:
            self.tasks.pause(spec)
            self._wait(spec, False)
            self.tasks.replace_xml(spec, candidate)
            if running:
                self.tasks.start(spec)
                self._wait(spec, True)
                deadline = self.clock() + 65
                while self.clock() < deadline:
                    procs, _ = matching_processes(spec, self.processes())
                    state = valid_telemetry(spec, procs)
                    if state.get('last_sync_at') and (not state.get('last_error')):
                        return f'{spec.title} 已接管为独立 EXE 桥接，服务器同步成功。'
                    self.sleep(0.5)
                raise ClientError('内置桥接未在时限内同步成功，正在恢复旧入口。')
            return f'{spec.title} 已接管为独立 EXE 桥接，保持暂停。'
        except Exception:
            self.tasks.pause(spec)
            self._wait(spec, False)
            self.tasks.replace_xml(spec, original)
            if running:
                self.tasks.start(spec)
            raise

    def save_connection(self, key, url, label, token=''):
        from desktop_client.connections import ConnectionStore
        spec = self.specs[key]
        if self.tasks.inspect(spec).get('mode') not in {'packaged', 'app-owned'}:
            raise ClientError('请先接管为内置桥接，再修改客户端连接配置。')
        store = ConnectionStore(spec.managed_config.parent.parent, spec.config.parent.parent.parent) if spec.managed_config else ConnectionStore()
        before = store.load(key)
        updated = dict(before, relay_url=url.strip().rstrip('/'), label=label.strip())
        if token.strip():
            updated['agent_token'] = token.strip()
        store.validate(updated)
        if before == updated:
            return '连接配置没有变化。'
        running = bool(matching_processes(spec, self.processes())[0])
        if running:
            self.control(key, 'pause')
        try:
            store.update(key, url, label, token)
            if running:
                self.control(key, 'start')
        except Exception:
            from desktop_client.connections import atomic_json
            atomic_json(store.path(key), before)
            if running:
                self.control(key, 'start')
            raise
        return spec.title + ' 连接配置已保存；运行中的桥接已重新连接。'

    def export_diagnostics(self):
        from desktop_client.connections import client_root
        from desktop_client import VERSION
        root = client_root()
        dest = root / 'diagnostics' / ('relay-client-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.zip')
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('status.json', json.dumps({'version': VERSION, 'status': self.status()}, ensure_ascii=False, indent=2))
            for key in self.specs:
                archive.writestr(key + '.log', self.log_tail(key))
            archive.writestr('README.txt', '已排除凭证、账号数据库、对话数据库、配置备份。日志仅导出脱敏尾部。')
        return str(dest)

    def _wait(self, spec, running, timeout=12):
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            procs, roots = matching_processes(spec, self.processes())
            if running and len(roots) == 1 or (not running and (not procs)):
                return
            if running and len(roots) > 1:
                raise ClientError('检测到多个桥接实例，未继续启动；请检查计划任务。')
            self.sleep(0.25)
        raise ClientError('等待桥接进程状态超时；桌面 App 未被停止，请刷新状态后检查日志。')

    def control(self, key, operation):
        spec = self.specs[key]
        if operation in {'start', 'restart'} and spec.managed_config:
            from desktop_client.connections import ConnectionStore
            store = ConnectionStore(spec.managed_config.parent.parent)
            if store.disconnect_pending(key):
                raise ClientError('解除配对尚未完成，请继续解除；不会启动旧凭证。')
            if store.unpaired(key):
                raise ClientError('本机已解除连接，请重新配对；不会启动旧凭证。')
        self.tasks.inspect(spec)
        procs, roots = matching_processes(spec, self.processes())
        if operation == 'start':
            if len(roots) == 1:
                return f'{spec.title} 桥接已运行，未创建重复进程。'
            if procs:
                raise ClientError('存在多个或未清理的桥接实例，未启动新进程。')
            self.tasks.start(spec)
            self._wait(spec, True, timeout=30 if sys.platform != 'win32' else 12)
            return f'{spec.title} 桥接已启动。'
        if operation not in {'pause', 'restart'}:
            raise ClientError('不支持此操作。')
        self.tasks.pause(spec)
        self._wait(spec, False)
        if operation == 'restart':
            self.tasks.start(spec)
            self._wait(spec, True, timeout=30 if sys.platform != 'win32' else 12)
            return f'{spec.title} 桥接已重启；桌面 App 未重启。'
        return f'{spec.title} 桥接已暂停；桌面 App 继续运行。'

    def log_path(self, key):
        spec = self.specs[key]
        runtime = spec.runtime_file
        if runtime and runtime.with_suffix('.log').exists():
            return runtime.with_suffix('.log')
        if spec.log.exists():
            return spec.log
        if runtime and spec.managed_config and spec.managed_config.exists():
            return runtime.with_suffix('.log')
        return spec.log

    def log_tail(self, key, limit=48 * 1024):
        path = self.log_path(key)
        if not path.exists():
            return '暂无桥接日志。'
        with path.open('rb') as handle:
            size = handle.seek(0, os.SEEK_END)
            handle.seek(max(0, size - limit))
            data = handle.read(limit)
        if size > limit and b'\n' in data:
            data = data.split(b'\n', 1)[1]
        text = data.decode('utf-8', errors='replace')
        spec = self.specs[key]
        config = spec.managed_config if spec.managed_config and spec.managed_config.exists() else spec.config
        try:
            token = json.loads(config.read_text(encoding='utf-8-sig')).get('agent_token')
            if token:
                text = text.replace(token, '[配对凭证已隐藏]')
        except (OSError, ValueError):
            pass
        return redact(text) or '日志暂为空。'

    def test_connection(self):
        urls = set()
        for spec in self.specs.values():
            try:
                urls.add(read_public_config(spec)['relay_url'])
            except Exception:
                pass
        if not urls:
            raise ClientError('未读取到服务器地址。')
        messages = []
        for url in sorted(urls):
            parsed = urlsplit(url)
            if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username:
                raise ClientError('本机桥接配置中的服务器地址格式不正确。')
            port = parsed.port or (443 if parsed.scheme == 'https' else 80)
            started = time.monotonic()
            with socket.create_connection((parsed.hostname, port), timeout=3):
                messages.append(f'{parsed.hostname}:{port} 可达 · {round((time.monotonic() - started) * 1000)} ms')
        return '；'.join(messages) + '\n仅检查网络连通，不代表账号授权或桌面同步状态。'

def valid_telemetry(spec, procs, now=None):
    if not spec.runtime_file:
        return {}
    try:
        data = json.loads(spec.runtime_file.read_text(encoding='utf-8'))
        age = (now or time.time()) - data['updated_at']
        if not 0 <= age < 10 or not any((p.get('packaged') and p['pid'] == data.get('pid') and (p['created'] == data.get('created')) for p in procs)):
            return {}
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return {}

def packaged_task_xml(xml, spec):
    ns = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
    ET.register_namespace('', ns)
    tree = ET.fromstring(xml)
    action = tree.find('{' + ns + '}Actions/{' + ns + '}Exec')
    if action is None:
        raise ClientError('计划任务中缺少可识别的执行入口。')
    for name, value in [('Command', str(spec.managed_executable)), ('Arguments', '--bridge ' + spec.key), ('WorkingDirectory', str(spec.managed_executable.parent))]:
        node = action.find('{' + ns + '}' + name)
        if node is None:
            node = ET.SubElement(action, '{' + ns + '}' + name)
        node.text = value
    triggers = tree.find('{' + ns + '}Triggers')
    if triggers is None:
        triggers = ET.Element('{' + ns + '}Triggers')
        tree.insert(0, triggers)
    if not any((node.get('id') == 'RelayClientRecovery' for node in triggers)):
        recovery = ET.SubElement(triggers, '{' + ns + '}TimeTrigger', {'id': 'RelayClientRecovery'})
        repeat = ET.SubElement(recovery, '{' + ns + '}Repetition')
        ET.SubElement(repeat, '{' + ns + '}Interval').text = 'PT1M'
        ET.SubElement(repeat, '{' + ns + '}StopAtDurationEnd').text = 'false'
        ET.SubElement(recovery, '{' + ns + '}StartBoundary').text = (datetime.datetime.now() + datetime.timedelta(seconds=3)).astimezone().isoformat(timespec='seconds')
        ET.SubElement(recovery, '{' + ns + '}Enabled').text = 'true'
    settings = tree.find('{' + ns + '}Settings')
    if settings is None:
        settings = ET.SubElement(tree, '{' + ns + '}Settings')
    instances = settings.find('{' + ns + '}MultipleInstancesPolicy')
    if instances is None:
        instances = ET.SubElement(settings, '{' + ns + '}MultipleInstancesPolicy')
    instances.text = 'IgnoreNew'
    return ET.tostring(tree, encoding='unicode')

def fresh_task_xml(spec, sid):
    ns = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
    ET.register_namespace('', ns)
    task = ET.Element('{' + ns + '}Task', {'version': '1.2'})

    def add(parent, name, text=None, attrs=None):
        node = ET.SubElement(parent, '{' + ns + '}' + name, attrs or {})
        if text is not None:
            node.text = text
        return node
    info = add(task, 'RegistrationInfo')
    add(info, 'Description', 'Relay Client ' + spec.title + ' background bridge')
    triggers = add(task, 'Triggers')
    trigger = add(triggers, 'LogonTrigger')
    add(trigger, 'Enabled', 'true')
    add(trigger, 'UserId', sid)
    principal = add(add(task, 'Principals'), 'Principal', attrs={'id': 'CurrentUser'})
    add(principal, 'UserId', sid)
    add(principal, 'LogonType', 'InteractiveToken')
    add(principal, 'RunLevel', 'LeastPrivilege')
    settings = add(task, 'Settings')
    for name, text in [('MultipleInstancesPolicy', 'IgnoreNew'), ('DisallowStartIfOnBatteries', 'false'), ('StopIfGoingOnBatteries', 'false'), ('StartWhenAvailable', 'true'), ('Enabled', 'true'), ('ExecutionTimeLimit', 'PT0S')]:
        add(settings, name, text)
    restart = add(settings, 'RestartOnFailure')
    add(restart, 'Interval', 'PT1M')
    add(restart, 'Count', '999')
    action = add(add(task, 'Actions', attrs={'Context': 'CurrentUser'}), 'Exec')
    add(action, 'Command', str(spec.managed_executable))
    add(action, 'Arguments', '--bridge ' + spec.key)
    add(action, 'WorkingDirectory', str(spec.managed_executable.parent))
    return packaged_task_xml(ET.tostring(task, encoding='unicode'), spec)

class Preferences:

    def __init__(self, directory=None):
        from desktop_client.connections import client_root
        self.directory = Path(directory or client_root())
        self.path = self.directory / 'preferences.json'

    def load(self):
        defaults = {'panel_url': '', 'close_to_tray': False, 'ask_on_close': True, 'codex_question_link': True}
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding='utf-8'))
                if isinstance(saved, dict):
                    defaults.update(saved)
            except (OSError, ValueError):
                pass
        defaults['ask_on_close'] = defaults.get('ask_on_close') is not False
        defaults['close_to_tray'] = defaults.get('close_to_tray') is True
        defaults['codex_question_link'] = defaults.get('codex_question_link') is True
        if defaults.get('language') not in ('zh', 'en'):
            defaults.pop('language', None)
        return defaults

    @staticmethod
    def validate_url(value, *, allow_empty=False):
        if not isinstance(value, str):
            raise ClientError('网页面板地址需要是 http / https 地址。')
        value = value.strip()
        if not value:
            if allow_empty:
                return ''
            raise ClientError('请先填写你要连接的网页面板地址。')
        parsed = urlsplit(value)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            raise ClientError('网页面板地址需要是 http / https 地址，不能包含账号密码。')
        if parsed.query or parsed.fragment:
            raise ClientError('请填写面板首页地址，不包含查询参数或片段。')
        try:
            parsed.port
        except ValueError:
            raise ClientError('端口格式不正确。') from None
        return value.strip()

    def save(self, data):
        from desktop_client.connections import atomic_json
        previous = self.load()
        data = {'panel_url': self.validate_url(data['panel_url'], allow_empty=True), 'ask_on_close': data.get('ask_on_close', previous['ask_on_close']) is not False, 'close_to_tray': data.get('close_to_tray', previous['close_to_tray']) is True, 'codex_question_link': data.get('codex_question_link', previous['codex_question_link']) is True, **({'language': data.get('language', previous.get('language'))} if data.get('language', previous.get('language')) in ('zh', 'en') else {})}
        atomic_json(self.path, data)

    def save_language(self, value):
        if value not in ('zh', 'en'):
            raise ValueError('Unsupported UI language')
        data = self.load()
        data['language'] = value
        self.save(data)

    def snapshot(self):
        try:
            return self.path.read_bytes()
        except FileNotFoundError:
            return None

    def restore(self, previous):
        from desktop_client.connections import atomic_bytes
        if previous is None:
            self.path.unlink(missing_ok=True)
        else:
            atomic_bytes(self.path, previous)

class StartupShortcut:
    NAME = 'Relay Client.lnk'

    def __new__(cls):
        if sys.platform != 'win32':
            from desktop_client.posix_backend import PosixStartup
            return PosixStartup()
        return super().__new__(cls)

    @property
    def path(self):
        return Path(os.environ['APPDATA']) / 'Microsoft/Windows/Start Menu/Programs/Startup' / self.NAME

    def enabled(self):
        return self.path.exists()

    def configure(self, enabled, executable, previous_executable=None):
        import pythoncom
        import win32com.client
        path = self.path
        allowed = {normalized_path(executable)}
        if previous_executable is not None:
            if not getattr(sys, 'frozen', False) or normalized_path(previous_executable) != normalized_path(sys.executable) or Path(previous_executable).name.casefold() != Path(executable).name.casefold():
                raise ClientError('原客户端启动入口身份不匹配，未修改。')
            allowed.add(normalized_path(previous_executable))
        pythoncom.CoInitialize()
        shortcut = shell = None
        try:
            shell = win32com.client.Dispatch('WScript.Shell')
            if path.exists():
                shortcut = shell.CreateShortcut(str(path))
                if normalized_path(shortcut.TargetPath) not in allowed:
                    raise ClientError('同名启动快捷方式属于其他程序，未修改。')
            if not enabled:
                path.unlink(missing_ok=True)
                return
            if not str(executable).lower().endswith('.exe'):
                raise ClientError('请使用打包后的客户端设置开机启动。')
            path.parent.mkdir(parents=True, exist_ok=True)
            import tempfile
            with tempfile.TemporaryDirectory(prefix='.relay-startup-', dir=path.parent) as directory:
                stage = Path(directory) / path.name
                shortcut = shell.CreateShortcut(str(stage))
                shortcut.TargetPath = str(executable)
                shortcut.Arguments = '--tray'
                shortcut.WorkingDirectory = str(Path(executable).parent)
                shortcut.Description = 'Relay Client：本机桥接管理'
                shortcut.Save()
                shortcut = None
                os.replace(stage, path)
        finally:
            shortcut = shell = None
            pythoncom.CoUninitialize()
