"""Installed-program health checks, independent of local development tests."""
import hashlib
import importlib
import json
from pathlib import Path
import ssl
import sys
import tempfile
from desktop_client import VERSION
from desktop_client.connections import atomic_json
from desktop_client.edition import edition

def check(role):
    if role not in ('client', 'server'):
        raise ValueError('Unknown program role')
    rows = []

    def run(name, operation):
        try:
            operation()
        except Exception as error:
            rows.append({'name': name, 'passed': False, 'errorType': type(error).__name__})
        else:
            rows.append({'name': name, 'passed': True})

    def identity():
        from desktop_client.relay_updates import version_tuple
        assert version_tuple(VERSION)
        if getattr(sys, 'frozen', False):
            value = json.loads((Path(sys._MEIPASS) / 'relay-edition.json').read_text(encoding='utf-8'))
            assert value == {'role': role, 'edition': edition()}

    def dependencies():
        for name in ('PySide6.QtCore', 'psutil', 'yaml', 'websocket', 'cryptography', 'keyring'):
            importlib.import_module(name)

    def isolated_state():
        from desktop_client.backend import Preferences
        from desktop_client.relay_updates import verify_file, UpdateError
        with tempfile.TemporaryDirectory(prefix='crb-release-health-') as directory:
            path = Path(directory) / 'state.json'
            first = {'label': '中文状态', 'version': VERSION}
            atomic_json(path, first)
            assert json.loads(path.read_text(encoding='utf-8')) == first
            atomic_json(path, dict(first, replaced=True))
            assert json.loads(path.read_text(encoding='utf-8')) == dict(first, replaced=True)
            assert not list(Path(directory).glob('*.tmp'))
            assert Preferences(directory).load()['panel_url'] == ''
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            verify_file(path, path.stat().st_size, digest)
            try:
                verify_file(path, path.stat().st_size + 1, digest)
            except UpdateError:
                pass
            else:
                raise AssertionError('Invalid package size was accepted')

    def signatures():
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = Ed25519PrivateKey.generate()
        data = b'CodeRelayBridge runtime dependency check'
        key.public_key().verify(key.sign(data), data)

    def tls():
        from desktop_client.native_tls import configure_native_tls
        configure_native_tls()
        context = ssl.create_default_context()
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED

    def role_payload():
        if role == 'client':
            from desktop_client.bridge_runtime import load_core
            codex = load_core('codex')
            assert callable(codex.HostAgent)
            assert callable(load_core('deepseek').HarnessAgent)
            sample = {'id': 'health-capacity', 'resumeState': 'resumed', 'turns': [{'turnId': 'health-turn', 'status': 'failed', 'error': {'codexErrorInfo': 'serverOverloaded'}, 'items': []}]}
            for runtime in ('idle', 'systemError', 'active'):
                sample['threadRuntimeStatus'] = {'type': runtime}
                projected = codex.view_state(sample, 1, 'health-owner')
                assert projected['canContinue'] and projected['executionState']['phase'] == 'capacity_wait'
            sample['turns'].append({'turnId': 'health-retry', 'status': 'inProgress', 'items': []})
            assert codex.view_state(sample, 2, 'health-owner')['capacityRetry'] is None
        else:
            from crb.paths import web_root
            server = importlib.import_module('crb.server.relay')
            assert callable(server.Relay) and callable(server.RelayHTTPServer)
            for name in ('index.html', 'app.js', 'requests.js', 'style.css', 'i18n.js', 'i18n-catalog.js'):
                assert (web_root() / name).is_file() and (web_root() / name).stat().st_size

    def persisted_names():
        from desktop_client.connections import ConnectionStore
        with tempfile.TemporaryDirectory(prefix='crb-persisted-names-') as directory:
            root = Path(directory)
            assert ConnectionStore(root / 'client', root / 'home').legacy_path('codex').name == 'agent.json'
            if role == 'server':
                persistence = importlib.import_module('crb.server.persistence')
                assert persistence.relay_database(root / 'state').name == 'relay.sqlite'

    def local_tests_absent():
        if getattr(sys, 'frozen', False):
            from importlib.util import find_spec
            assert find_spec('tests') is None
            assert not any((name.startswith(('tests.', 'desktop_client.test_', 'desktop_server.test_')) for name in sys.modules))
    for name, operation in (('identity', identity), ('runtime_dependencies', dependencies), ('unicode_atomic_state_and_hash_validation', isolated_state), ('ed25519_dependency', signatures), ('tls_verification', tls), ('stable_persisted_filenames', persisted_names), ('role_payload', role_payload), ('development_tests_absent', local_tests_absent)):
        run(name, operation)
    return {'passed': all((row['passed'] for row in rows)), 'checks': rows, 'role': role, 'version': VERSION, 'edition': edition(), 'frozen': bool(getattr(sys, 'frozen', False)), 'developmentTestsRequired': False, 'configurationChanged': False, 'originalAppsChanged': False}
