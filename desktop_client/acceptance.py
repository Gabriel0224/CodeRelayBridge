"""Executed by the installed EXE. No external Python bridge or user-chat changes."""
import base64
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import psutil
from desktop_client.backend import BridgeManager, native_app_running, process_rows
from desktop_client.bridge_runtime import load_core, sanitize_child_launches, package_probe
from desktop_client.connections import ConnectionStore

def protected_processes():
    return {(r['pid'], r.get('create_time')) for r in process_rows() if native_app_running('codex', [r]) or native_app_running('deepseek', [r]) or any(('read_only_unc_http_bridge.py' in str(a) for a in r.get('cmdline', [])))}

def wait_synced(manager, key):
    deadline = time.monotonic() + 65
    while time.monotonic() < deadline:
        state = next((r for r in manager.status() if r['key'] == key))
        if state.get('relay_online') and state.get('telemetry', {}).get('desktop_connected'):
            return state
        time.sleep(0.5)
    raise RuntimeError(key + ' 未恢复服务器 / 桌面同步')

def feature_probe():
    result = {'package': package_probe(), 'checks': {}}
    core = load_core('codex')
    sanitize_child_launches()
    files = object.__new__(core.HostAgent)
    with tempfile.TemporaryDirectory(prefix='relay-client-exe-test-') as temp:
        path = Path(temp) / 'fixture.txt'
        path.write_text('客户端文件测试\n', encoding='utf-8', newline='\n')
        read = files.read_text_file({'path': str(path)})
        result['checks']['file_read_utf8'] = read['content'] == '客户端文件测试\n'
        files.write_text_file({'path': str(path), 'content': '客户端编辑成功\n', 'expectedSha256': read['sha256']})
        result['checks']['file_edit_utf8'] = files.read_text_file({'path': str(path)})['content'] == '客户端编辑成功\n'
        term = core.TerminalSession('relay-client-isolated-probe', temp)
        try:
            term.write(b'Write-Output ("RELAY_CLIENT_NATIVE_TERMINAL_OK_" + $PSVersionTable.PSVersion.Major)\r\n')
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                output = base64.b64decode(term.read(0)['data']).decode('utf-8', errors='replace')
                if 'RELAY_CLIENT_NATIVE_TERMINAL_OK_7' in output:
                    break
                time.sleep(0.1)
            result['checks']['powershell7_terminal_io'] = 'RELAY_CLIENT_NATIVE_TERMINAL_OK_7' in output
            result['terminal_executable'] = term.process.args[0]
        finally:
            term.close()
        result['checks']['terminal_process_closed'] = term.process.poll() is not None
    harness = load_core('deepseek')
    cfg = ConnectionStore().load('deepseek')
    client = harness.HarnessClient(cfg.get('harness_home'), cfg.get('harness_url', 'http://127.0.0.1:19387'))
    try:
        sessions = client.rpc('session/list', {'_request': {}})
        models = client.rpc('session/modelCatalog')
        result['checks']['deepseek_native_sessions'] = isinstance(sessions.get('items'), list)
        result['deepseek_session_count'] = len(sessions.get('items', []))
        result['checks']['deepseek_native_models'] = len(harness.public_models(models)) > 0
    finally:
        client.close()
    result['all_passed'] = all(result['checks'].values())
    return result

def acceptance():
    if not getattr(sys, 'frozen', False):
        raise RuntimeError('请测试安装后的 EXE。')
    manager = BridgeManager()
    original = manager.status()
    if not all((s.get('mode') == 'packaged' and s.get('relay_online') for s in original)):
        raise RuntimeError('请先完成独立 EXE 桥接接管并同步成功。')
    protected = protected_processes()
    hashes = {k: hashlib.sha256(s.config.read_bytes()).hexdigest() for k, s in manager.specs.items()}
    checks = {}
    try:
        for key in manager.specs:
            before = next((s for s in manager.status() if s['key'] == key))
            manager.control(key, 'start')
            checks[key + '_start_is_noop'] = next((s for s in manager.status() if s['key'] == key))['pids'] == before['pids']
            manager.control(key, 'restart')
            after = wait_synced(manager, key)
            checks[key + '_restart_and_sync'] = after['pids'] != before['pids'] and after['instances'] == 1
        manager.control('deepseek', 'pause')
        state = next((s for s in manager.status() if s['key'] == 'deepseek'))
        checks['pause_disables_task_and_stops_worker'] = not state['enabled'] and (not state['pids']) and (not state['telemetry'])
        manager.control('deepseek', 'start')
        state = wait_synced(manager, 'deepseek')
        checks['resume_restores_task_and_sync'] = state['enabled'] and state['instances'] == 1
        before = state['pids']
        duplicate = subprocess.run([sys.executable, '--bridge', 'deepseek'], env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1'), creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
        checks['duplicate_worker_refused'] = duplicate.returncode == 0 and next((s for s in manager.status() if s['key'] == 'deepseek'))['pids'] == before
        features = feature_probe()
        checks['packaged_native_features'] = features['all_passed']
        final = manager.status()
        checks['native_apps_and_nas_unchanged'] = protected_processes() == protected
        checks['legacy_configs_unchanged'] = all((hashlib.sha256(s.config.read_bytes()).hexdigest() == hashes[k] for k, s in manager.specs.items()))
        checks['no_external_python_bridge'] = all((s['mode'] == 'packaged' and s['instances'] == 1 for s in final))
        return {'checks': checks, 'features': features, 'before': original, 'after': final, 'all_passed': all(checks.values())}
    finally:
        for key, spec in manager.specs.items():
            if not manager.tasks.inspect(spec)['enabled'] or not next((s for s in manager.status() if s['key'] == key))['pids']:
                manager.control(key, 'start')
