"""Real frozen-payload setup/worker acceptance in a disposable profile only."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
import urllib.error
import urllib.request
import uuid

def probe(bind='127.0.0.1'):
    if not getattr(sys, 'frozen', False):
        raise RuntimeError('首次设置验收必须运行实际打包的独立程序')
    from desktop_client.bridge_runtime import sanitize_child_launches
    from desktop_client.connections import atomic_json
    from desktop_client.setup import EnrollmentClient
    from desktop_server.backend import ServerManager
    from desktop_server.install import install, package_directory, package_fingerprint
    sanitize_child_launches()
    checks = {}
    child = None
    with tempfile.TemporaryDirectory(prefix='relay-server-probe-') as directory:
        root = Path(directory) / ('CodexRelayServer-SetupTest-' + uuid.uuid4().hex)
        manager = ServerManager(root, Path(directory) / 'empty-home')
        with patch('desktop_server.install.shortcut'):
            exe = install(root)
            with socket.socket(socket.AF_INET6 if ':' in bind else socket.AF_INET) as reservation:
                reservation.bind((bind, 0))
                port = reservation.getsockname()[1]
            base = 'http://' + ('[' + bind + ']' if ':' in bind else bind) + ':' + str(port)
            password = secrets.token_urlsafe(18)
            manager.create(bind, port, base, 'setup-test', password)
            checks['startup_then_create'] = manager.config()['client_login_mode'] == 'local'
            checks['repeat_install_no_overwrite'] = install(root) == exe
            checks['complete_native_runtime'] = package_fingerprint(package_directory(Path(sys.executable))) == package_fingerprint(package_directory(exe))

        def launch():
            return subprocess.Popen([str(exe), '--bridge', 'server'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

        def healthy():
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError('隔离服务启动失败，请检查打包依赖')
                if manager.status()['running']:
                    return True
                time.sleep(0.1)
            raise RuntimeError('隔离服务未通过健康检查')

        def stop():
            state = json.loads(manager.spec().runtime_file.read_text(encoding='utf-8'))
            atomic_json(manager.spec().runtime_file.with_name('server.stop.json'), {'pid': state['pid'], 'created': state['created']})
            child.wait(timeout=10)
        try:
            child = launch()
            checks['native_worker_start_and_health'] = healthy()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(base + '/app.js', timeout=3) as response:
                script = response.read()
                checks['updated_web_bundled'] = b'function timelineSegments(' in script
            try:
                opener.open(base + '/api/lite/catalog', timeout=3)
            except urllib.error.HTTPError as error:
                checks['anonymous_rejected'] = error.code == 401
                error.close()
            else:
                checks['anonymous_rejected'] = False
            client = EnrollmentClient(base)
            connections = client.enroll(str(uuid.uuid4()), 'Setup probe', ['codex', 'deepseek'], password, 'setup-test')['connections']
            checks['fresh_login_and_pairing'] = set(connections) == {'codex', 'deepseek'}
            before = manager.config_path().read_bytes()
            stop()
            checks['graceful_stop'] = child.returncode == 0
            child = launch()
            checks['native_restart'] = healthy()
            checks['restart_preserves_config'] = manager.config_path().read_bytes() == before
            stop()
        finally:
            if child and child.poll() is None:
                child.terminate()
                child.wait(timeout=10)
    return {'passed': all(checks.values()), 'checks': checks, 'frozen': True, 'temporaryProfileOnly': True, 'existingAppsChanged': False, 'desktopShortcutsChanged': False, 'productionServicesChanged': False}
