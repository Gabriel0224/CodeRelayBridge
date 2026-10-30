import json
import os
from pathlib import Path
import sys

def worker():
    if os.name != 'nt':
        os.umask(63)
    from desktop_server.backend import ServerManager
    from desktop_server.state_status import ServerStateWriter
    from desktop_client.backend import process_rows, matching_processes
    from logging.handlers import RotatingFileHandler
    import logging
    import threading
    import psutil
    from crb.server.relay import Relay, RelayHTTPServer, handler_factory
    from desktop_client.bridge_runtime import sanitize_child_launches
    sanitize_child_launches()
    manager = ServerManager()
    spec = manager.spec()
    if any((p['pid'] != os.getpid() for p in matching_processes(spec, process_rows())[0])):
        return 0
    cfg = manager.config()
    cfg['_config_path'] = str(manager.config_path())
    cfg['_native_manager'] = True
    logging.basicConfig(handlers=[RotatingFileHandler(manager.root / 'server.log', maxBytes=1024 * 1024, backupCount=2, encoding='utf-8')], level=logging.INFO)
    app = Relay(cfg)
    from desktop_server.native_restart import NativeRestartManager
    app.panel_restart = NativeRestartManager(manager)
    server = RelayHTTPServer((cfg['bind'], cfg['port']), handler_factory(app))
    state = {'pid': os.getpid(), 'created': psutil.Process().create_time(), 'phase': 'running'}
    status_writer = ServerStateWriter(spec.runtime_file)
    status_writer.publish(state)
    monitor_stop = threading.Event()

    def stop_check():
        while not monitor_stop.wait(1):
            if status_writer.pending:
                status_writer.publish(state)
            try:
                request = json.loads(spec.runtime_file.with_name('server.stop.json').read_text())
                if request.get('pid') == state['pid'] and request.get('created') == state['created']:
                    server.shutdown()
                    return
            except (OSError, ValueError):
                pass
    monitor = threading.Thread(target=stop_check, daemon=True)
    monitor.start()
    try:
        server.serve_forever()
    finally:
        monitor_stop.set()
        monitor.join(2)
        server.server_close()
        app.ssh_terminals.close_all()
        app.local_preview.close()
        status_writer.publish(dict(state, phase='stopped'))
    return 0

def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--installer-uninstall':
        from desktop_client.installer_runtime import uninstall
        from desktop_client.connections import atomic_json
        try:
            atomic_json(Path(sys.argv[2]), uninstall('server'))
            return 0
        except Exception as error:
            atomic_json(Path(sys.argv[2]), {'passed': False, 'errorType': type(error).__name__})
            return 1
    if len(sys.argv) == 3 and sys.argv[1] == '--apply-crb-update':
        from desktop_client.update_apply import apply
        return apply(sys.argv[2])
    if len(sys.argv) == 4 and sys.argv[1] == '--system-authorized':
        from desktop_client.system_setup import authorized_main
        return authorized_main(sys.argv[2], sys.argv[3])
    if len(sys.argv) >= 3 and sys.argv[1] == '--system-probe':
        from desktop_client.system_setup import probe_args
        from desktop_client.connections import atomic_json
        try:
            value = probe_args('server', sys.argv[3:])
        except Exception as exc:
            value = {'passed': False, 'error': str(exc), 'systemChanges': 0}
        atomic_json(Path(sys.argv[2]), value)
        return 0 if value['passed'] else 1
    from desktop_client.native_tls import configure_native_tls
    configure_native_tls()
    if sys.platform != 'win32':
        from desktop_client.bridge_runtime import sanitize_child_launches
        sanitize_child_launches()
    if len(sys.argv) > 2 and sys.argv[1] in {'--first-setup-probe', '--ipv6-setup-probe'}:
        from desktop_server.first_setup_probe import probe
        from desktop_client.connections import atomic_json
        from desktop_client.backend import redact
        try:
            value = probe('::1' if sys.argv[1] == '--ipv6-setup-probe' else '127.0.0.1')
        except Exception as error:
            value = {'passed': False, 'error': redact(str(error)), 'temporaryProfileOnly': True}
        atomic_json(Path(sys.argv[2]), value)
        return 0 if value['passed'] else 1
    if len(sys.argv) > 2 and sys.argv[1] in {'--control', '--cleanup-test'}:
        from desktop_server.backend import ServerManager
        from desktop_client.connections import atomic_json
        from desktop_client.backend import redact
        manager = ServerManager()
        try:
            if sys.argv[1] == '--cleanup-test':
                if not manager.root.name.startswith('CodexRelayServer-SetupTest-'):
                    raise RuntimeError('拒绝移除非测试服务')
                tasks, spec = (manager.tasks(), manager.spec())
                if tasks.exists(spec):
                    tasks.pause(spec)
                    tasks.remove(spec)
                value = {'removed': True}
            else:
                value = {'result': manager.control(sys.argv[3])}
            atomic_json(Path(sys.argv[2]), value)
            return 0
        except Exception as exc:
            atomic_json(Path(sys.argv[2]), {'error': redact(str(exc))})
            return 1
    if len(sys.argv) == 4 and sys.argv[1] == '--restart-helper':
        from desktop_server.native_restart import helper
        return helper(sys.argv[2], sys.argv[3])
    if len(sys.argv) > 2 and sys.argv[1] == '--install':
        from desktop_server.install import install
        from desktop_client.connections import atomic_json
        atomic_json(Path(sys.argv[2]), {'executable': str(install())})
        return 0
    if len(sys.argv) > 2 and sys.argv[1] == '--migrate':
        from desktop_server.backend import ServerManager
        from desktop_client.connections import atomic_json
        from desktop_client.backend import redact
        try:
            atomic_json(Path(sys.argv[2]), {'result': ServerManager().migrate()})
            return 0
        except Exception as exc:
            atomic_json(Path(sys.argv[2]), {'error': redact(str(exc))})
            return 1
    if len(sys.argv) > 2 and sys.argv[1] == '--self-test':
        from desktop_client.connections import atomic_json
        from desktop_client.self_check import check
        value = check('server')
        atomic_json(Path(sys.argv[2]), value)
        return 0 if value['passed'] else 1
    if len(sys.argv) > 2 and sys.argv[1] == '--ui-check':
        from desktop_client.ui_check import check
        return check('server', sys.argv[2])
    if sys.argv[1:3] == ['--bridge', 'server']:
        try:
            return worker()
        except Exception:
            import traceback
            from desktop_server.backend import server_root
            from desktop_client.backend import redact
            root = server_root()
            root.mkdir(parents=True, exist_ok=True)
            (root / 'boot-error.log').write_text(redact(traceback.format_exc()), encoding='utf-8')
            return 1
    if len(sys.argv) > 2 and sys.argv[1] == '--status':
        from desktop_server.backend import ServerManager
        from desktop_client.connections import atomic_json
        atomic_json(Path(sys.argv[2]), ServerManager().status())
        return 0
    from desktop_server.app import main as gui
    return gui()
if __name__ == '__main__':
    raise SystemExit(main())
