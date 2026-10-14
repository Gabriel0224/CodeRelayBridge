import os
from pathlib import Path
import sys
repo = Path(SPECPATH).parent
role = os.environ['RELAY_BUILD_ROLE']
work = Path(os.environ['RELAY_BUILD_WORK'])
edition = os.environ['RELAY_BUILD_EDITION']
product = 'RelayClient' if role == 'client' else 'RelayServer'
stage = work / 'bridge-stage/relay_bridge_bundle'
hidden = ['desktop_client.self_check', 'yaml', 'websocket'] + (['win32timezone', 'keyring.backends.Windows'] if sys.platform == 'win32' else ['keyring.backends.macOS'] if sys.platform == 'darwin' else ['keyring.backends.SecretService', 'secretstorage', 'jeepney'])
if edition != 'standard':
    hidden += []
datas, binaries = ([(str(work / 'relay-edition.json'), '.')], [])
datas += [(str(repo / 'crb/codex/question_ui.js'), 'crb/codex')]
excludes = ['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtQml']
if edition == 'standard':
    excludes += ['software_updates', 'relay_bridge_bundle.software_updates']
if role == 'server':
    excludes += []
if role == 'client':
    import json
    manifest = json.loads((stage / 'manifest.json').read_text())
    hidden += ['relay_bridge_bundle.' + name for name in manifest['modules']]
    hidden += ['yaml', 'websocket', 'crb.files.file_access']
    for file in stage.iterdir():
        if file.suffix not in {'.py', '.pyc'}:
            datas.append((str(file), 'relay_bridge_bundle'))
else:
    hidden += ['crb.server.relay', 'crb.server.panel_restart', 'crb.common.client_pairing', 'crb.terminal.windows_pty']
    if sys.platform == 'win32':
        hidden += ['winpty']
    datas += [(str(repo / 'web'), 'web'), (str(repo / 'crb/updates/upstreams.json'), '.')]
excludes += ['tests']
a = Analysis([str(repo / ('desktop_client/run_client.py' if role == 'client' else 'desktop_server/run_server.py'))], pathex=[str(repo), str(stage.parent)], datas=datas, binaries=binaries, hiddenimports=hidden, excludes=excludes, optimize=0)
if any((item[0].startswith(('tests.', 'desktop_client.test_', 'desktop_server.test_')) for item in a.pure)):
    raise RuntimeError('Development tests must never enter the native package')
if sys.platform == 'win32':
    a.binaries = [item for item in a.binaries if Path(item[0]).name.lower() not in {'icuuc.dll', 'icudt78.dll'}]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=product, console=False, upx=False, codesign_identity=None, entitlements_file=None, argv_emulation=False)
coll = COLLECT(exe, a.binaries, a.datas, name=product, strip=False, upx=False)
if sys.platform == 'darwin':
    app = BUNDLE(coll, name='Relay Client.app' if role == 'client' else 'Relay Server.app', bundle_identifier='com.codexrelay.' + role, info_plist={'LSUIElement': True, 'NSHighResolutionCapable': True})
