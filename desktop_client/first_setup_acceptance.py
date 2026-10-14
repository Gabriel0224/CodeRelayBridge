"""Actual frozen EXE, empty private profile, fresh real Windows tasks, live relay."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from desktop_client.backend import BridgeManager, process_rows, normalized_path
from desktop_client.connections import ConnectionStore, client_root, atomic_json
from desktop_client.setup import SetupService, EnrollmentClient
from desktop_client.acceptance import protected_processes

def parent_test(report):
    if not getattr(sys, 'frozen', False):
        raise RuntimeError('Use the packaged EXE')
    profile = Path(os.environ['LOCALAPPDATA']) / ('CodexRelayClient-SetupTest-' + uuid.uuid4().hex[:12])
    profile.mkdir()
    shutil.copytree(Path(sys.executable).parent, profile / 'app')
    target = profile / 'app/RelayClient.exe'
    protected = protected_processes()
    ordinary = ConnectionStore()
    hashes = {key: hashlib.sha256(ordinary.path(key).read_bytes()).hexdigest() for key in ('codex', 'deepseek') if ordinary.path(key).exists()}
    private_report = profile / 'acceptance.json'
    subprocess.run([str(target), '--first-setup-profile', str(private_report)], timeout=320, creationflags=subprocess.CREATE_NO_WINDOW)
    value = json.loads(private_report.read_text(encoding='utf-8'))
    value['profile'] = str(profile)
    value['protected_processes_preserved'] = protected <= protected_processes()
    value['existing_configurations_preserved'] = all((hashlib.sha256(ordinary.path(key).read_bytes()).hexdigest() == digest for key, digest in hashes.items()))
    value['all_passed'] = value.get('all_passed', False) and value['protected_processes_preserved'] and value['existing_configurations_preserved']
    atomic_json(report, value)
    return value

def profile_test():
    root = client_root()
    if not root.name.startswith('CodexRelayClient-SetupTest-'):
        raise RuntimeError('Refusing first-setup acceptance against ordinary user profile')
    password = os.environ['CLIENT_PANEL_PASSWORD']
    username = os.environ.get('CLIENT_PANEL_USERNAME', 'relay')
    manager, store = (BridgeManager(), ConnectionStore())
    setup = SetupService(manager, store)
    checks = {'no_old_configs': len(setup.missing()) == 2, 'no_old_tasks': all((not manager.tasks.exists(s) for s in manager.specs.values()))}
    if not all(checks.values()):
        raise RuntimeError('Setup profile is not empty')
    base = safe_acceptance_server()
    api = EnrollmentClient(base)
    info = api.info()
    api.request('/client/login', {'username': username, 'password': password}, info['origin'])

    def post(path, data):
        return api.request(path, data, info['origin'])

    def command(host, action, payload):
        identity = str(uuid.uuid4())
        post('/api/command', {'id': identity, 'host': host, 'thread': '', 'action': action, 'payload': payload})
        for _ in range(90):
            result = api.request('/api/command?id=' + identity).get('result')
            if result is not None:
                if not result.get('ok'):
                    raise RuntimeError(result.get('error', 'Command failed'))
                return result
            time.sleep(0.25)
        raise RuntimeError('Acceptance command timed out')
    terminal = None
    errors = []
    try:
        outcome = setup.connect(base, '首次接入验收（临时）', ['codex', 'deepseek'], password, username)
        checks['pairing_from_empty'] = not setup.missing()
        states = manager.status()
        checks['real_exe_tasks_created'] = all((s['controllable'] and s.get('mode') == 'packaged' for s in states))
        checks['public_server_sync'] = all((s['relay_online'] for s in states))
        checks['both_desktop_connections'] = all((s['telemetry'].get('desktop_connected') for s in states))
        host = store.load('codex')['host_id']
        deepseek = store.load('deepseek')['host_id']
        catalog = api.request('/api/lite/catalog', limit=4 * 1024 * 1024)['hosts']
        rows = [r for r in catalog if r['id'] in (host, deepseek)]
        checks['correct_application_mapping'] = {r['applicationId'] for r in rows} == {'codex', 'deepseek'} and len({r['physicalHostId'] for r in rows}) == 1
        checks['file_terminal_without_ssh'] = all((r.get('terminal', {}).get('transport') == 'client' for r in rows))
        with tempfile.TemporaryDirectory(prefix='relay-first-setup-files-') as directory:

            def files(action, **payload):
                return post('/api/files', {'host': host, 'action': action, 'payload': payload})
            created = files('create', path=directory, name='setup-test.txt', kind='file')
            path = created['path']
            content = '独立客户端首次接入测试\n'
            command(host, 'file_write_text', {'path': path, 'content': content, 'expectedSha256': hashlib.sha256(b'').hexdigest()})
            result = files('read', path=path)
            checks['file_create_edit_read'] = base64.b64decode(result['data']).decode() == content
            listing = files('list', path=directory)
            checks['directory_index'] = any((r['name'] == 'setup-test.txt' for r in listing['entries']))
            copied = files('copy', path=path, destination=directory)
            files('delete', path=copied['path'], confirmName=Path(copied['path']).name)
            checks['file_copy_delete'] = not Path(copied['path']).exists()
            for selected in (host, deepseek):
                result = post('/api/files', {'host': selected, 'action': 'roots', 'payload': {}})
                checks['file_roots_' + ('codex' if selected == host else 'deepseek')] = bool(result['roots'])
            terminal = post('/api/terminal', {'host': host, 'action': 'start', 'payload': {'cwd': directory, 'cols': 100, 'rows': 30}})['sessionId']
            encoded = base64.b64encode(b'Write-Output ("CLIENT_FIRST_SETUP_READY_" + $PSVersionTable.PSVersion.Major)\r\n').decode()
            post('/api/terminal', {'host': host, 'action': 'write', 'payload': {'sessionId': terminal, 'data': encoded}})
            output = b''
            offset = 0
            for _ in range(12):
                result = post('/api/terminal', {'host': host, 'action': 'read', 'payload': {'sessionId': terminal, 'offset': offset}})
                output += base64.b64decode(result['data'])
                offset = result['offset']
                if b'CLIENT_FIRST_SETUP_READY_7' in output:
                    break
                time.sleep(0.25)
            checks['actual_powershell7'] = b'CLIENT_FIRST_SETUP_READY_7' in output
            listed = post('/api/terminal', {'host': '', 'action': 'list', 'payload': {}})
            checks['terminal_cross_page_inventory'] = any((r['sessionId'] == terminal for r in listed['sessions']))
            post('/api/terminal', {'host': host, 'action': 'close', 'payload': {'sessionId': terminal}})
            listed = post('/api/terminal', {'host': '', 'action': 'list', 'payload': {}})
            checks['terminal_closed_removed'] = not any((r['sessionId'] == terminal for r in listed['sessions']))
            terminal = None
        checks['password_not_saved'] = all((password not in p.read_text(encoding='utf-8', errors='replace') for p in root.rglob('*.json')))
        old = store.load('codex')
        deepseek_before = store.path('deepseek').read_bytes()
        setup.disconnect('codex')
        checks['unpair_local_task_removed'] = not manager.tasks.exists(manager.specs['codex']) and store.unpaired('codex')
        checks['unpair_other_app_preserved'] = store.path('deepseek').read_bytes() == deepseek_before and next((r for r in manager.status() if r['key'] == 'deepseek'))['relay_online']

        def revoked(config):
            try:
                EnrollmentClient(base).request('/agent/sync', {'host': config['host_id'], 'catalog': []}, token=config['agent_token'])
            except Exception as exc:
                import urllib.error
                return isinstance(exc.__context__, urllib.error.HTTPError) and exc.__context__.code == 401
            return False
        checks['old_credential_revoked'] = revoked(old)
        setup.connect(base, '首次接入验收（临时）', ['codex'], password, username)
        repaired = store.load('codex')
        checks['repairing_rotates_credential'] = repaired['host_id'] == old['host_id'] and repaired['agent_token'] != old['agent_token']
        checks['repairing_sync_confirmed'] = next((r for r in manager.status() if r['key'] == 'codex'))['relay_online']
        checks['old_credential_stays_revoked_after_repair'] = revoked(old)
        checks['repairing_other_app_preserved'] = store.path('deepseek').read_bytes() == deepseek_before
        for key in ('codex', 'deepseek'):
            setup.disconnect(key)
        checks['both_apps_unpaired_independently'] = all((store.unpaired(k) and (not manager.tasks.exists(manager.specs[k])) for k in manager.specs))
    except Exception as exc:
        from desktop_client.backend import redact
        errors.append(redact(str(exc)).replace(password, '[password-hidden]'))
    finally:
        if terminal:
            try:
                post('/api/terminal', {'host': store.load('codex')['host_id'], 'action': 'close', 'payload': {'sessionId': terminal}})
            except Exception:
                pass
        for spec in manager.specs.values():
            try:
                if manager.tasks.exists(spec):
                    manager.control(spec.key, 'pause')
                    manager.tasks.delete_test_task(spec)
            except Exception as exc:
                errors.append(type(exc).__name__ + ' during test task cleanup')
        if (root / 'installation.json').exists():
            try:
                identity = json.loads((root / 'installation.json').read_text())['id']
                post('/client/forget', {'installationId': identity})
            except Exception as exc:
                errors.append(type(exc).__name__ + ' during temporary registration cleanup')
        api.cookies.clear()
    checks['test_tasks_removed'] = all((not manager.tasks.exists(s) for s in manager.specs.values()))
    if checks['test_tasks_removed']:
        for key in manager.specs:
            store.path(key).unlink(missing_ok=True)
            store.marker_path(key).unlink(missing_ok=True)
        (root / 'installation.json').unlink(missing_ok=True)
    return {'version': __import__('desktop_client').VERSION, 'checks': checks, 'errors': errors, 'all_passed': all(checks.values()) and (not errors)}

def safe_acceptance_server():
    """Live acceptance must opt in to a caller-provided server, never a user IP."""
    from desktop_client.setup import safe_setup_url
    base = os.environ.get('CLIENT_SETUP_SERVER', '').strip()
    if not base:
        raise RuntimeError('首次接入实测需要显式指定 CLIENT_SETUP_SERVER；不会连接任何预置服务器。')
    return safe_setup_url(base)
