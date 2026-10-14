import io
import json
import os
import sys
import traceback
from pathlib import Path

def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--apply-crb-update':
        from desktop_client.update_apply import apply
        return apply(sys.argv[2])
    from desktop_client.startup_diagnostics import configure_boot_trace, boot_mark
    configure_boot_trace(sys.argv)
    boot_mark('native.main')
    if len(sys.argv) == 3 and sys.argv[1] == '--startup-cost-probe':
        import time
        entered_at = time.time()
        from desktop_client.startup_diagnostics import probe
        from desktop_client.connections import atomic_json
        try:
            value = probe(entered_at)
        except Exception as exc:
            value = {'passed': False, 'errorType': type(exc).__name__, 'systemChanges': 0}
        atomic_json(Path(sys.argv[2]), value)
        return 0 if value['passed'] else 1
    if len(sys.argv) == 4 and sys.argv[1] == '--system-authorized':
        from desktop_client.system_setup import authorized_main
        return authorized_main(sys.argv[2], sys.argv[3])
    if len(sys.argv) >= 3 and sys.argv[1] == '--system-probe':
        from desktop_client.system_setup import probe_args
        from desktop_client.connections import atomic_json
        try:
            value = probe_args('client', sys.argv[3:])
        except Exception as exc:
            value = {'passed': False, 'error': str(exc), 'systemChanges': 0}
        atomic_json(Path(sys.argv[2]), value)
        return 0 if value['passed'] else 1
    boot_mark('native.tls-import.begin')
    from desktop_client.native_tls import configure_native_tls
    configure_native_tls()
    boot_mark('native.tls-import.end')
    if len(sys.argv) == 3 and sys.argv[1] == '--environment-probe':
        from desktop_client.bridge_runtime import sanitize_child_launches
        sanitize_child_launches()
        from desktop_client.launch_environment import desktop_session_environment
        from desktop_client.connections import atomic_json
        environment = desktop_session_environment({})
        atomic_json(Path(sys.argv[2]), {'environment': environment, 'displayRecovered': bool(environment.get('DISPLAY') or environment.get('WAYLAND_DISPLAY')), 'frozen': bool(getattr(sys, 'frozen', False))})
        return 0
    if len(sys.argv) == 4 and sys.argv[1] == '--question-ui-probe':
        from desktop_client.bridge_runtime import load_core
        from desktop_client.connections import atomic_json
        core = load_core('codex')
        request = json.loads(Path(sys.argv[3]).read_text(encoding='utf-8'))
        value = core.CodexQuestionUI().acknowledge(request['threadId'], request['turnId'], request['replies'])
        atomic_json(Path(sys.argv[2]), value)
        return 0 if value['status'] in {'closed', 'not-open', 'unanswered-preserved', 'new-draft-preserved'} else 1
    if sys.platform != 'win32':
        from desktop_client.bridge_runtime import sanitize_child_launches
        sanitize_child_launches()
    if len(sys.argv) >= 3 and sys.argv[1] in {'--independent-probe', '--independent-probe-parent', '--independent-probe-worker', '--independent-probe-child'}:
        from desktop_client.independent_checks import dispatch_probe
        return dispatch_probe(sys.argv[1], sys.argv[2])
    if len(sys.argv) >= 3 and sys.argv[1] == '--app-close-probe':
        from desktop_client.native_apps import close_probe
        from desktop_client.connections import atomic_json
        value = close_probe()
        atomic_json(Path(sys.argv[2]), value)
        return 0 if value['passed'] else 1
    if len(sys.argv) >= 3 and sys.argv[1] == '--app-launch-probe':
        from desktop_client.native_apps import launch_probe
        from desktop_client.connections import atomic_json
        value = launch_probe(exercise='--exercise' in sys.argv[3:])
        atomic_json(Path(sys.argv[2]), value)
        return 0 if value['passed'] else 1
    if len(sys.argv) >= 3 and sys.argv[1] in {'--session-probe-worker', '--session-probe-parent'}:
        from desktop_client.native_checks import session_probe_worker, session_probe_parent
        return session_probe_worker(sys.argv[2]) if sys.argv[1] == '--session-probe-worker' else session_probe_parent(sys.argv[2], len(sys.argv) > 3 and sys.argv[3] == 'abrupt')
    if len(sys.argv) >= 3 and sys.argv[1] == '--session-probe':
        from desktop_client.native_checks import session_probe
        from desktop_client.connections import atomic_json
        value = session_probe()
        atomic_json(Path(sys.argv[2]), value)
        return 0 if value['passed'] else 1
    if len(sys.argv) >= 3 and sys.argv[1] == '--legacy-probe':
        from desktop_client.backend import BridgeManager, redact
        from desktop_client.connections import atomic_json
        try:
            manager = BridgeManager()
            value = []
            for key, spec in manager.specs.items():
                exists = manager.tasks.exists(spec)
                if exists:
                    manager.tasks.inspect(spec)
                legacy = manager.tasks.legacy(spec) if hasattr(manager.tasks, 'legacy') else None
                value.append({'key': key, 'ownedEntry': exists, 'legacyEntry': bool(legacy)})
            atomic_json(Path(sys.argv[2]), {'passed': True, 'entries': value})
            return 0
        except Exception as exc:
            atomic_json(Path(sys.argv[2]), {'passed': False, 'error': redact(str(exc))})
            return 1
    if len(sys.argv) >= 3 and sys.argv[1] == '--client-action':
        from desktop_client.lifecycle import send_client_action
        from desktop_client.connections import atomic_json
        try:
            value = send_client_action(sys.argv[3])
            atomic_json(Path(sys.argv[2]), value)
            return 0
        except Exception as exc:
            from desktop_client.backend import redact
            atomic_json(Path(sys.argv[2]), {'error': redact(str(exc))})
            return 1
    if len(sys.argv) >= 3 and sys.argv[1] == '--ui-check':
        from desktop_client.ui_check import check
        return check('client', sys.argv[2])
    if len(sys.argv) >= 3 and sys.argv[1] == '--bridge':
        from desktop_client.bridge_runtime import run_worker
        try:
            return run_worker(sys.argv[2])
        except Exception:
            from desktop_client.connections import client_root
            from desktop_client.backend import redact
            root = client_root() / 'runtime'
            root.mkdir(parents=True, exist_ok=True)
            (root / 'boot-error.log').write_text(redact(traceback.format_exc()), encoding='utf-8')
            return 1
    if len(sys.argv) >= 3 and sys.argv[1] in {'--package-probe', '--self-test', '--install-bridges', '--status', '--control', '--diagnostics', '--acceptance', '--feature-probe', '--first-setup-test', '--first-setup-profile', '--install', '--local-probe', '--tray-probe'}:
        from desktop_client.connections import atomic_json
        from desktop_client.backend import BridgeManager, redact
        report = Path(sys.argv[2])
        try:
            if sys.argv[1] == '--install':
                from desktop_client.setup import ensure_client_installed
                value = {'executable': str(ensure_client_installed())}
            elif sys.argv[1] in {'--local-probe', '--tray-probe'}:
                from desktop_client.native_checks import local_probe, tray_probe
                value = local_probe() if sys.argv[1] == '--local-probe' else tray_probe()
            elif sys.argv[1] in {'--first-setup-test', '--first-setup-profile'}:
                from desktop_client.first_setup_acceptance import parent_test, profile_test
                value = parent_test(report) if sys.argv[1] == '--first-setup-test' else profile_test()
            elif sys.argv[1] == '--package-probe':
                from desktop_client.bridge_runtime import package_probe
                value = package_probe()
            elif sys.argv[1] == '--self-test':
                from desktop_client.self_check import check
                value = check('client')
            elif sys.argv[1] in {'--acceptance', '--feature-probe'}:
                from desktop_client.acceptance import acceptance, feature_probe
                value = acceptance() if sys.argv[1] == '--acceptance' else feature_probe()
            else:
                from desktop_client.lifecycle import client_manager
                manager = client_manager()
                if sys.argv[1] == '--install-bridges':
                    value = {'results': [manager.install(key) for key in manager.specs]}
                elif sys.argv[1] == '--control':
                    value = {'result': manager.control(sys.argv[3], sys.argv[4])}
                elif sys.argv[1] == '--diagnostics':
                    value = {'path': manager.export_diagnostics()}
                else:
                    value = {'status': manager.status()}
            atomic_json(report, value)
            return 1 if value.get('passed') is False or value.get('all_passed') is False else 0
        except Exception:
            atomic_json(report, {'error': redact(traceback.format_exc())})
            return 1
    from desktop_client.independent_launch import detach_gui_if_needed
    boot_mark('gui.independence.begin')
    try:
        if detach_gui_if_needed():
            return 0
    except Exception:
        from desktop_client.connections import client_root
        from desktop_client.backend import redact
        from PySide6.QtWidgets import QApplication, QMessageBox
        root = client_root() / 'runtime'
        root.mkdir(parents=True, exist_ok=True)
        (root / 'independent-start-error.log').write_text(redact(traceback.format_exc()), encoding='utf-8')
        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.warning(None, 'Relay Client', '客户端独立启动失败，未启动桥接。请查看诊断日志或从桌面快捷方式打开。')
        return 1
    boot_mark('gui.independence.end')
    from desktop_client.lifecycle import wait_for_previous
    wait_for_previous()
    from desktop_client.launch_environment import prepare_gui_environment
    boot_mark('gui.environment.begin')
    prepare_gui_environment()
    boot_mark('gui.environment.end')
    boot_mark('gui.import.begin')
    from desktop_client.app import main as gui_main
    boot_mark('gui.import.end')
    return gui_main()
if __name__ == '__main__':
    raise SystemExit(main())
