"""Build self-contained native applications on the target OS, never cross-compile."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from desktop_client.bundle_core import stage_core

def build(role, core_source=None, harness_source=None, bridge_source=None, edition=None):
    edition = edition or 'standard'
    if edition not in ('standard',):
        raise ValueError('Client and server editions must be standard or optional provider')
    repo = Path(__file__).resolve().parent.parent
    product = 'RelayClient' if role == 'client' else 'RelayServer'
    suffix = '-standard' if edition == 'standard' else ''
    work = repo / 'desktop_client/build' / (role + '-' + sys.platform + suffix)
    dist = repo / 'desktop_client/dist' / sys.platform
    if edition == 'standard':
        dist = dist / 'standard'
    work.mkdir(parents=True, exist_ok=True)
    if sys.platform == 'darwin':
        from desktop_client.native_icons import mac_icon
        mac_icon(work, role)
    args = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--onedir', '--name', product, '--distpath', str(dist), '--workpath', str(work / 'pyinstaller'), '--specpath', str(work), '--paths', str(repo), '--exclude-module', 'PySide6.QtWebEngineCore', '--exclude-module', 'PySide6.QtWebEngineWidgets', '--exclude-module', 'PySide6.QtQml']
    delimiter = ';' if sys.platform == 'win32' else ':'
    if role == 'client':
        source = Path(core_source or repo)
        extras = {n: Path(harness_source) for n in ('harness_agent', 'harness_client')} if harness_source else {}
        if bridge_source:
            pass
        if sys.platform.startswith('linux'):
            extras['modern_desktop_client'] = repo / 'desktop_client/vendor'
        manifest = stage_core(source, work / 'bridge-stage', extras, edition=edition)
        if sys.platform.startswith('linux') and 'modern_desktop_client' not in manifest['modules']:
            raise RuntimeError('Linux RC2 desktop discovery module was not bundled')
        package = work / 'bridge-stage/relay_bridge_bundle'
        args += ['--paths', str(package.parent)]
        for name in manifest['modules']:
            args += ['--hidden-import', 'relay_bridge_bundle.' + name]
        for file in package.iterdir():
            if file.suffix not in {'.py', '.pyc'}:
                args += ['--add-data', str(file) + delimiter + 'relay_bridge_bundle']
        for name in ('yaml', 'websocket', 'file_access'):
            args += ['--hidden-import', name]
        entry = repo / 'desktop_client/run_client.py'
    else:
        args += ['--add-data', str(repo / 'web') + delimiter + 'web', '--add-data', str(repo / 'upstreams.json') + delimiter + '.', '--hidden-import', 'relay', '--hidden-import', 'client_pairing', '--hidden-import', 'panel_restart']
        entry = repo / 'desktop_server/run_server.py'
    if sys.platform == 'darwin':
        args += ['--osx-bundle-identifier', 'com.codexrelay.' + role]
    args.append(str(entry))
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--distpath', str(dist), '--workpath', str(work / 'pyinstaller'), str(repo / 'desktop_client/native-relay.spec')]
    (work / 'relay-edition.json').write_text(json.dumps({'edition': edition, 'role': role}), encoding='utf-8')
    subprocess.run(command, cwd=repo, check=True, env=dict(os.environ, RELAY_BUILD_ROLE=role, RELAY_BUILD_WORK=str(work), RELAY_BUILD_EDITION=edition))
    executable = dist / (('Relay Client' if role == 'client' else 'Relay Server') + '.app/Contents/MacOS/' + product if sys.platform == 'darwin' else product + '/' + product + ('.exe' if sys.platform == 'win32' else ''))
    print(json.dumps({'role': role, 'edition': edition, 'platform': sys.platform, 'executable': str(executable), 'credentialsBundled': False}))
    return executable
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('role', choices=('client', 'server'))
    parser.add_argument('--core-source')
    parser.add_argument('--harness-source')
    parser.add_argument('--bridge-source')
    parser.add_argument('--edition', choices=['standard', 'universal'])
    args = parser.parse_args()
    build(args.role, args.core_source, args.harness_source, args.bridge_source, args.edition)
