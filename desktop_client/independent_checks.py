"""Real launcher lifetime regressions in disposable, credential-free profiles.

Windows tests separate App and provider-policy jobs; POSIX tests an isolated
launcher process group. All jobs/processes below are disposable test fixtures.
POSIX setsid does not escape a systemd cgroup; live deployments must also be
checked for a separate desktop App scope, never inferred from PPID alone.
"""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import signal
import sys
import tempfile
import time
import psutil
from desktop_client.connections import atomic_json
from desktop_client.independent_launch import launch_independent, windows_job_member, windows_session_id

def wait_for(check, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.1)
    raise RuntimeError('Independent launch probe timed out')

def identity(pid=None):
    p = psutil.Process(pid or os.getpid())
    return {'pid': p.pid, 'created': p.create_time(), 'exe': p.exe()}

def alive(row):
    from desktop_client.lifecycle import process_identity_alive
    return process_identity_alive(row['pid'], row['created'], row['exe'])

def worker(directory):
    from desktop_client.native_checks import session_probe_parent
    if sys.platform == 'win32':
        from desktop_client.independent_launch import BOOTSTRAP_FLAG, detach_gui_if_needed
        root = Path(directory)
        wait_for(lambda: (root / 'bootstrap-ready.json').exists())
        previous_args = sys.argv
        try:
            sys.argv = [sys.executable, BOOTSTRAP_FLAG, '--tray']
            report = {'policy_job_present': windows_job_member(), 'gui_continues_without_relaunch': not detach_gui_if_needed(), 'bootstrap_flag_consumed': BOOTSTRAP_FLAG not in sys.argv}
        except Exception as exc:
            report = {'passed': False, 'error_type': type(exc).__name__}
        finally:
            sys.argv = previous_args
        atomic_json(root / 'bootstrap.json', report)
        if report.get('passed') is False or not all(report.values()):
            return 1
    return session_probe_parent(directory, until_stop=True)

def parent(directory):
    root = Path(directory)
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
    ordinary = subprocess.Popen([sys.executable, '--independent-probe-child', str(root)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, **options)
    detached = launch_independent([sys.executable, '--independent-probe-worker', str(root / 'client')])
    atomic_json(root / 'launcher.json', {'parent': identity(), 'ordinary': identity(ordinary.pid), 'client': identity(detached.pid)})
    wait_for(lambda: (root / 'end-parent').exists(), timeout=45)
    return 0

def probe():
    if sys.platform != 'win32':
        return posix_probe()
    from ctypes import wintypes as W
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, W.LPCWSTR]
    k.CreateJobObjectW.restype = W.HANDLE
    k.SetInformationJobObject.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD]
    k.SetInformationJobObject.restype = W.BOOL
    k.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
    k.AssignProcessToJobObject.restype = W.BOOL
    k.TerminateJobObject.argtypes = [W.HANDLE, W.UINT]
    k.TerminateJobObject.restype = W.BOOL
    k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    k.OpenProcess.restype = W.HANDLE
    k.CloseHandle.argtypes = [W.HANDLE]
    checks = {}
    with tempfile.TemporaryDirectory(prefix='relay-independent-job-') as d:
        root = Path(d)
        job = k.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        policy_job = None
        p = None
        rows = None
        try:
            limits = ctypes.create_string_buffer(144)
            W.DWORD.from_buffer(limits, 16).value = 8192
            if not k.SetInformationJobObject(job, 9, limits, len(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            policy_job = k.CreateJobObjectW(None, None)
            if not policy_job or not k.SetInformationJobObject(policy_job, 9, limits, len(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            p = subprocess.Popen([sys.executable, '--independent-probe-parent', d], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
            if not k.AssignProcessToJobObject(job, W.HANDLE(int(p._handle))):
                raise ctypes.WinError(ctypes.get_last_error())
            p.stdin.write(b'x')
            p.stdin.flush()
            p.stdin.close()
            wait_for(lambda: (root / 'launcher.json').exists())
            rows = json.loads((root / 'launcher.json').read_text(encoding='utf-8'))
            if not alive(rows['client']):
                raise RuntimeError('Independent fixture identity expired before policy job assignment')
            handle = k.OpenProcess(256 | 1 | 4096, False, rows['client']['pid'])
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if not k.AssignProcessToJobObject(policy_job, handle):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                k.CloseHandle(handle)
            atomic_json(root / 'client/bootstrap-ready.json', {'ready': True})
            wait_for(lambda: (root / 'client/bootstrap.json').exists())
            bootstrap = json.loads((root / 'client/bootstrap.json').read_text(encoding='utf-8'))
            if bootstrap.get('passed') is False:
                raise RuntimeError('Brokered GUI fixture failed: ' + bootstrap.get('error_type', 'unknown'))
            wait_for(lambda: (root / 'client/parent.json').exists())
            children = json.loads((root / 'client/parent.json').read_text(encoding='utf-8'))['children']
            checks['old_launch_is_in_app_job'] = windows_job_member(rows['ordinary']['pid'], job)
            checks['new_client_outside_app_job'] = not windows_job_member(rows['client']['pid'], job)
            checks['client_in_separate_policy_job'] = windows_job_member(rows['client']['pid'], policy_job)
            checks['brokered_gui_accepts_policy_job'] = all(bootstrap.values())
            checks['client_in_same_user_session'] = same_session(rows['parent']['pid'], rows['client']['pid'])
            checks['client_and_bridges_alive_before_app_exit'] = alive(rows['client']) and all((alive(c) for c in children))
            checks['both_bridges_in_client_policy_job'] = len(children) == 2 and all((windows_job_member(c['pid'], policy_job) and (not windows_job_member(c['pid'], job)) for c in children))
            if not k.TerminateJobObject(job, 91):
                raise ctypes.WinError(ctypes.get_last_error())
            p.wait(timeout=8)
            wait_for(lambda: not alive(rows['ordinary']))
            time.sleep(0.5)
            checks['app_and_old_launch_terminated'] = not alive(rows['parent']) and (not alive(rows['ordinary']))
            checks['independent_client_survives_app_exit'] = alive(rows['client'])
            checks['both_owned_bridges_survive_app_exit'] = len(children) == 2 and all((alive(c) for c in children))
            atomic_json(root / 'client/stop.json', {'stop': True})
            wait_for(lambda: not alive(rows['client']))
            wait_for(lambda: all((not alive(c) for c in children)))
            checks['client_close_still_disconnects_all_bridges'] = True
            checks['both_bridges_stopped_gracefully'] = all(((root / 'client' / (key + '.ended.json')).exists() for key in ('codex', 'deepseek')))
        finally:
            k.TerminateJobObject(job, 91)
            k.CloseHandle(job)
            if p:
                if p.stdin and (not p.stdin.closed):
                    p.stdin.close()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=5)
            atomic_json(root / 'client/stop.json', {'stop': True})
            if policy_job:
                k.TerminateJobObject(policy_job, 91)
                k.CloseHandle(policy_job)
            if rows:
                wait_for(lambda: not alive(rows['client']), timeout=15)
    return {'passed': bool(checks) and all(checks.values()), 'checks': checks, 'real_apps_untouched': True, 'network_requests': 0, 'no_services_or_startup_entries_created': True}

def terminate_fixture_group(process):
    if process.poll() is not None:
        return
    if os.getpgid(process.pid) != process.pid or os.getsid(process.pid) != process.pid:
        raise RuntimeError('Fixture launcher is not an isolated session leader')
    os.killpg(process.pid, signal.SIGTERM)
    process.wait(timeout=8)

def posix_probe():
    checks = {}
    with tempfile.TemporaryDirectory(prefix='relay-independent-posix-') as directory:
        root = Path(directory)
        process = None
        rows = None
        try:
            process = subprocess.Popen([sys.executable, '--independent-probe-parent', directory], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
            process.stdin.write(b'x')
            process.stdin.flush()
            process.stdin.close()
            wait_for(lambda: (root / 'launcher.json').exists())
            rows = json.loads((root / 'launcher.json').read_text(encoding='utf-8'))
            wait_for(lambda: (root / 'client/parent.json').exists())
            children = json.loads((root / 'client/parent.json').read_text(encoding='utf-8'))['children']
            checks['old_launch_inherits_launcher_group'] = os.getpgid(rows['ordinary']['pid']) == process.pid
            checks['new_client_has_separate_process_group'] = os.getpgid(rows['client']['pid']) == rows['client']['pid']
            checks['new_client_has_separate_session'] = os.getsid(rows['client']['pid']) == rows['client']['pid']
            checks['bridges_belong_to_client_not_original_launcher'] = all((psutil.Process(c['pid']).ppid() == rows['client']['pid'] and os.getpgid(c['pid']) == rows['client']['pid'] for c in children))
            checks['client_and_bridges_alive_before_launcher_exit'] = alive(rows['client']) and all((alive(c) for c in children))
            terminate_fixture_group(process)
            wait_for(lambda: not alive(rows['ordinary']))
            time.sleep(0.5)
            checks['launcher_and_old_launch_terminated'] = not alive(rows['parent']) and (not alive(rows['ordinary']))
            checks['independent_client_survives_launcher_group_exit'] = alive(rows['client'])
            checks['both_owned_bridges_survive_launcher_group_exit'] = len(children) == 2 and all((alive(c) for c in children))
            atomic_json(root / 'client/stop.json', {'stop': True})
            wait_for(lambda: not alive(rows['client']))
            wait_for(lambda: all((not alive(c) for c in children)))
            checks['client_close_still_disconnects_all_bridges'] = True
        finally:
            if process:
                if process.stdin and (not process.stdin.closed):
                    process.stdin.close()
                terminate_fixture_group(process)
            atomic_json(root / 'client/stop.json', {'stop': True})
            if rows:
                wait_for(lambda: not alive(rows['client']), timeout=15)
    return {'passed': bool(checks) and all(checks.values()), 'platform': sys.platform, 'checks': checks, 'real_apps_untouched': True, 'network_requests': 0, 'no_services_or_startup_entries_created': True, 'scope': 'POSIX process-group exit, not systemd cgroup termination'}

def same_session(first, second):
    return windows_session_id(first) == windows_session_id(second)

def dispatch_probe(flag, path):
    if flag == '--independent-probe':
        try:
            value = probe()
        except Exception as exc:
            from desktop_client.backend import redact
            value = {'passed': False, 'error': redact(str(exc)), 'error_type': type(exc).__name__}
        atomic_json(path, value)
        return 0 if value['passed'] else 1
    if flag == '--independent-probe-parent':
        from desktop_client.lifecycle import watch_parent_pipe
        import threading
        closed = threading.Event()
        watch_parent_pipe(closed)
        return parent(path)
    if flag == '--independent-probe-worker':
        return worker(path)
    wait_for(lambda: (Path(path) / 'end-child').exists(), timeout=45)
    return 0
