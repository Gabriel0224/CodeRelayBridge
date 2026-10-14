"""Packaged local file/terminal checks, exclusively in a temporary directory."""
import base64
import os
import sys
from pathlib import Path
import tempfile
import time

def local_probe():
    from desktop_client.bridge_runtime import load_core, sanitize_child_launches
    core = load_core('codex')
    sanitize_child_launches()
    checks = {}
    files = object.__new__(core.HostAgent)
    with tempfile.TemporaryDirectory(prefix='relay-native-probe-') as directory:
        path = Path(directory) / 'test.txt'
        path.write_text('中文文件检查\n', encoding='utf-8', newline='\n')
        first = files.read_text_file({'path': str(path)})
        checks['read_utf8'] = first['content'] == '中文文件检查\n'
        files.write_text_file({'path': str(path), 'content': '编辑成功\n', 'expectedSha256': first['sha256']})
        checks['edit_utf8'] = files.read_text_file({'path': str(path)})['content'] == '编辑成功\n'
        term = core.TerminalSession('relay-local-probe', directory)
        try:
            if os.name == 'nt':
                term.write(b'Write-Output ("RELAY_NATIVE_TERMINAL_OK_" + $PSVersionTable.PSVersion.Major)\r\n')
                marker = 'RELAY_NATIVE_TERMINAL_OK_7'
            else:
                term.write(b"printf '%s%s\\n' RELAY_ NATIVE_TERMINAL_OK\n")
                marker = 'RELAY_NATIVE_TERMINAL_OK'
            deadline = time.monotonic() + 12
            output = ''
            while time.monotonic() < deadline:
                output = base64.b64decode(term.read(0)['data']).decode('utf-8', errors='replace')
                if marker in output:
                    break
                time.sleep(0.1)
            checks['terminal_io'] = marker in output
            shell = term.shell
        finally:
            term.close()
        checks['terminal_closed'] = term.process.poll() is not None
    return {'passed': all(checks.values()), 'checks': checks, 'shell': shell, 'temporary_files_only': True}

def tray_probe():
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon
    from desktop_client.platform_support import platform_name
    app = QApplication.instance() or QApplication([])
    return {'platform': platform_name(), 'trayAvailable': QSystemTrayIcon.isSystemTrayAvailable()}

def session_probe_worker(path):
    """No credentials, network or desktop commands: exercise the exact lifetime guard."""
    import psutil
    import threading
    from desktop_client.connections import atomic_json
    from desktop_client.lifecycle import worker_owner, worker_should_stop, watch_parent_pipe
    path = Path(path)
    owner = worker_owner()
    identity = {'pid': os.getpid(), 'created': psutil.Process().create_time(), 'exe': sys.executable}
    closed = threading.Event()
    threading.Thread(target=watch_parent_pipe, args=(closed,), daemon=True).start()
    atomic_json(path, identity)
    while not closed.wait(0.1) and (not worker_should_stop(owner, path.with_suffix('.stop.json'), identity['pid'], identity['created'])):
        pass
    atomic_json(path.with_suffix('.ended.json'), {'closed': True, 'owner': owner['pid']})
    return 0

def session_probe_parent(directory, abrupt=False, until_stop=False):
    import dataclasses
    import json
    import subprocess
    from unittest.mock import patch
    from desktop_client.backend import bridge_specs
    from desktop_client.connections import ConnectionStore, atomic_json
    from desktop_client.lifecycle import ClientSession
    root = Path(directory)
    store = ConnectionStore(root / 'profile', root / 'home')
    specs = {k: dataclasses.replace(s, managed_config=store.path(k), managed_executable=Path(sys.executable), runtime_file=root / (k + '.json')) for k, s in bridge_specs(root / 'home', root / 'local').items()}

    def launcher(args, **kwargs):
        return subprocess.Popen([sys.executable, '--session-probe-worker', str(specs[args[-1]].runtime_file)], **kwargs)
    session = ClientSession(store, specs, launcher=launcher)
    for key in specs:
        atomic_json(store.path(key), {'host_id': 'isolated-' + key, 'label': 'fixture', 'relay_url': 'http://127.0.0.1:1', 'agent_token': 'never-sent-fixture', 'state_dir': str(root / key)})
        with patch('desktop_client.lifecycle.matching_processes', return_value=([], [])):
            session.start(specs[key])
    deadline = time.monotonic() + 10
    while not all((s.runtime_file.exists() for s in specs.values())):
        if time.monotonic() > deadline:
            session.shutdown()
            raise RuntimeError('Isolated lifetime workers failed to start')
        time.sleep(0.1)
    atomic_json(root / 'parent.json', {'children': [json.loads(s.runtime_file.read_text(encoding='utf-8')) for s in specs.values()]})
    if until_stop:
        try:
            deadline = time.monotonic() + 45
            while not (root / 'stop.json').exists() and time.monotonic() < deadline:
                time.sleep(0.1)
        finally:
            session.shutdown()
        return 0
    if abrupt:
        os._exit(0)
    session.shutdown()
    return 0

def session_probe():
    import json
    import subprocess
    import sys
    from desktop_client.lifecycle import launch_options, process_identity_alive
    checks = {}
    for mode in ('normal', 'abrupt'):
        with tempfile.TemporaryDirectory(prefix='relay-session-probe-') as d:
            root = Path(d)
            result = subprocess.run([sys.executable, '--session-probe-parent', d, mode], capture_output=True, timeout=25, **launch_options())
            identities = json.loads((root / 'parent.json').read_text(encoding='utf-8'))['children']
            deadline = time.monotonic() + 8
            while any((process_identity_alive(p['pid'], p['created'], p['exe']) for p in identities)) and time.monotonic() < deadline:
                time.sleep(0.1)
            checks[mode + '_no_orphan_workers'] = result.returncode == 0 and (not any((process_identity_alive(p['pid'], p['created'], p['exe']) for p in identities)))
            checks[mode + '_graceful_stop'] = all(((root / (key + '.ended.json')).exists() for key in ('codex', 'deepseek')))
    return {'passed': all(checks.values()), 'checks': checks, 'isolated': True, 'networkRequests': 0}
