"""Background-only local update; preserve credentials, App processes, and task state."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import shutil
import psutil
from desktop_client.acceptance import protected_processes
from desktop_client.backend import BridgeManager, normalized_path, process_rows
from desktop_client.connections import atomic_json, client_root
from desktop_client import VERSION
from desktop_client.independent_launch import launch_client, client_gui_arguments

def replace_windows_package(package, target):
    """A browser launched from the UI can keep its install directory as cwd.

Do not close unrelated programs to release that directory. With all client
processes stopped, a verified package can be copied in place after a full
backup. On failure restore the old files, preserving the directory itself.
"""
    package, target = (Path(package), Path(target))
    backup = target.with_name('app-backup-' + str(time.time_ns()))
    in_place = False
    try:
        target.rename(backup)
    except OSError as exc:
        if getattr(exc, 'winerror', None) != 32:
            raise
        shutil.copytree(target, backup)
        in_place = True
    try:
        shutil.copytree(package, target, dirs_exist_ok=in_place)
    except Exception:
        if in_place:
            shutil.copytree(backup, target, dirs_exist_ok=True)
        else:
            if target.exists():
                target.rename(target.with_name('app-failed-' + str(time.time_ns())))
            backup.rename(target)
        raise
    return {'backup': str(backup), 'directory_kept_for_other_programs': in_place}

def main():
    repo = Path(__file__).resolve().parent.parent
    manager = BridgeManager()
    modern = (client_root() / 'client-session.json').exists() and (not any((manager.tasks.exists(s) for s in manager.specs.values())))
    if modern:
        from desktop_client.lifecycle import client_manager
        manager = client_manager()
    original = manager.status()
    if not all((s.get('mode') in {'packaged', 'app-owned'} and s.get('controllable') for s in original)):
        raise RuntimeError('Refusing update of unexpected bridge task identities')
    paths = [p for s in manager.specs.values() for p in (s.config, s.managed_config) if p and p.exists()]
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    protected = protected_processes()
    target = client_root() / 'app/RelayClient.exe'
    owners = {r.get('telemetry', {}).get('owner_pid') for r in original}
    managers = [r for r in process_rows() if normalized_path(r.get('exe') or '') == normalized_path(target) and (r['pid'] in owners or client_gui_arguments(r.get('cmdline', [])[1:]))]
    audit = {'before': original, 'config_hashes': hashes, 'manager_pids': [r['pid'] for r in managers]}
    paused = []
    lifecycle = tuple(map(int, VERSION.split('.'))) >= (0, 6, 0)
    if lifecycle and (not (client_root() / 'client-session.json').exists()):
        atomic_json(client_root() / 'client-session.json', {'version': 1, 'applications': {r['key']: {'enabled': bool(r.get('enabled'))} for r in original if r.get('config', {}).get('paired')}})
    try:
        if modern and managers:
            report = repo / 'artifacts' / ('client-v' + VERSION.replace('.', '')) / 'close-before-update.json'
            subprocess.run([str(target), '--client-action', str(report), 'close'], check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
            deadline = time.monotonic() + 30
            while any((r['instances'] for r in manager.status())):
                if time.monotonic() > deadline:
                    raise RuntimeError('Client did not disconnect before update')
                time.sleep(0.2)
        elif modern and any((r.get('instances') for r in original)):
            raise RuntimeError('Unexpected bridge workers without a recognized UI; refusing package replacement')
        for item in original:
            if modern:
                continue
            if item.get('instances') or item.get('enabled'):
                manager.control(item['key'], 'pause')
                paused.append(item['key'])
        for row in managers:
            try:
                p = psutil.Process(row['pid'])
            except psutil.NoSuchProcess:
                continue
            if normalized_path(p.exe()) != normalized_path(target) or p.create_time() != row['create_time']:
                raise RuntimeError('Manager process identity changed')
            p.terminate()
            p.wait(timeout=10)
        if '--package' in sys.argv:
            package = Path(sys.argv[sys.argv.index('--package') + 1]).resolve()
            manifest = json.loads((package / '_internal/relay_bridge_bundle/manifest.json').read_text())
            if not manifest.get('function_bodies_unchanged') or not (package / 'RelayClient.exe').is_file():
                raise RuntimeError('Invalid verified native package')
            if target.parent.is_symlink() or target.parent.resolve().parent != client_root().resolve():
                raise RuntimeError('Unexpected installation directory identity')
            audit['package_swap'] = replace_windows_package(package, target.parent)
        else:
            subprocess.run([sys.executable, '-m', 'desktop_client.build_client', '--install-only'], cwd=repo, creationflags=subprocess.CREATE_NO_WINDOW, check=True, capture_output=True, timeout=60)
    except Exception:
        if modern and target.is_file() and (not any((normalized_path(r.get('exe') or '') == normalized_path(target) for r in process_rows()))):
            launch_client([str(target), '--tray'])
        raise
    finally:
        for item in original:
            if item['key'] not in paused:
                continue
            if lifecycle:
                continue
            if item.get('instances'):
                manager.control(item['key'], 'start')
            else:
                manager.tasks.enable(manager.specs[item['key']], item.get('enabled', False))
    if lifecycle:
        tray = launch_client([str(target), '--tray'])
        audit['tray_pid'] = tray.pid
        audit['main_window_auto_opened'] = False
        from desktop_client.lifecycle import client_manager
        manager = client_manager()
    deadline = time.monotonic() + 70
    while time.monotonic() < deadline:
        final = manager.status()
        startup_ready = not lifecycle or all((after.get('mode') == 'app-owned' and after.get('enabled') == before.get('enabled') and (after.get('instances') == 0 if not before.get('enabled') else after.get('instances') == 1 and after.get('telemetry', {}).get('version') == VERSION and (after.get('telemetry', {}).get('owner_pid') == tray.pid)) for before, after in zip(original, final)))
        if startup_ready and all((not before.get('relay_online') or after.get('relay_online') for before, after in zip(original, final))):
            break
        time.sleep(0.5)
    audit.update(after=final, client_startup_confirmed=startup_ready, credentials_preserved=all((hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p, h in hashes.items())), native_apps_and_nas_preserved=protected <= protected_processes(), bridges_synced=all((not before.get('relay_online') or after.get('relay_online') for before, after in zip(original, final))))
    if managers and (not lifecycle):
        tray = launch_client([str(target), '--tray'])
        audit['tray_pid'] = tray.pid
        audit['main_window_auto_opened'] = False
    audit['all_passed'] = audit['client_startup_confirmed'] and audit['credentials_preserved'] and audit['native_apps_and_nas_preserved'] and audit['bridges_synced']
    atomic_json(repo / 'artifacts' / ('client-v' + VERSION.replace('.', '')) / 'install.json', audit)
    print(json.dumps({k: audit[k] for k in ('all_passed', 'credentials_preserved', 'native_apps_and_nas_preserved', 'bridges_synced')}, ensure_ascii=False))
    if not audit['all_passed']:
        raise RuntimeError('Update checks failed; see protected audit')
if __name__ == '__main__':
    main()
