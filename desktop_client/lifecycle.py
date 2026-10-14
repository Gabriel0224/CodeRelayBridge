"""Client UI owns every bridge; no worker is an independently started service."""
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import psutil
from desktop_client.backend import ClientError, bridge_specs, normalized_path, process_rows, matching_processes
from desktop_client.connections import ConnectionStore, atomic_json
from desktop_client.backend import native_app_identity
from desktop_client.reconnect import ATTEMPT_LIMIT, RETRY_INTERVAL, EXHAUSTED_EXIT_CODE
OWNER_ENV = 'RELAY_CLIENT_OWNER'

def process_identity_alive(pid, created, executable):
    try:
        p = psutil.Process(int(pid))
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE and (abs(p.create_time() - float(created)) < 0.01) and (normalized_path(p.exe()) == normalized_path(executable))
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError, TypeError):
        return False

def worker_owner():
    try:
        owner = json.loads(os.environ[OWNER_ENV])
        if not isinstance(owner, dict) or int(owner['pid']) != os.getppid() or Path(owner['exe']).name.casefold() != Path(sys.executable).name.casefold() or (not process_identity_alive(owner['pid'], owner['created'], owner['exe'])):
            raise ValueError()
        return owner
    except (KeyError, ValueError, TypeError):
        raise ClientError('桥接只能由正在运行的客户端启动，不能独立驻留后台。') from None

def owner_alive(owner):
    return process_identity_alive(owner['pid'], owner['created'], owner['exe'])

def watch_parent_pipe(closed):
    try:
        if sys.stdin is not None:
            sys.stdin.buffer.read(1)
        elif sys.platform == 'win32':
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.GetStdHandle.argtypes = [wintypes.DWORD]
            kernel.GetStdHandle.restype = wintypes.HANDLE
            kernel.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
            buffer = ctypes.create_string_buffer(1)
            received = wintypes.DWORD()
            kernel.ReadFile(kernel.GetStdHandle(-10), buffer, 1, ctypes.byref(received), None)
        else:
            os.read(0, 1)
    finally:
        closed.set()

def worker_should_stop(owner, stop_file, pid, created):
    if not owner_alive(owner):
        return True
    try:
        request = json.loads(Path(stop_file).read_text(encoding='utf-8'))
        return isinstance(request, dict) and request.get('pid') == pid and (request.get('created') == created)
    except (OSError, ValueError):
        return False

def launch_options():
    options = {'close_fds': True}
    if sys.platform == 'win32':
        options['creationflags'] = subprocess.CREATE_NO_WINDOW
    return options

class ClientSession:

    def __init__(self, store=None, specs=None, launcher=subprocess.Popen, legacy=None):
        self.store = store or ConnectionStore()
        self.specs = specs or bridge_specs()
        self.path = self.store.root / 'client-session.json'
        self.children = {}
        self.lock = threading.RLock()
        self.closing = False
        self.launcher = launcher
        self.legacy = legacy
        self.retry_at = {}
        self.crash_attempts = {}
        self.app_identities = {}
        self.remote_restart_ready = False
        from desktop_client.control_link import ClientControlLink
        self.control_link = ClientControlLink(self)

    def state(self):
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {'version': 1, 'applications': {}}
        if not isinstance(value, dict) or value.get('version') != 1 or (not isinstance(value.get('applications'), dict)) or any((k not in self.specs or not isinstance(v, dict) or (not isinstance(v.get('enabled'), bool)) for k, v in value['applications'].items())):
            raise ClientError('客户端启停记录无法读取；未自动恢复接入，请查看诊断日志。')
        return value

    def set_entry(self, key, enabled=None, remove=False):
        with self.lock:
            value = self.state()
            if remove:
                value['applications'].pop(key, None)
            else:
                value['applications'][key] = {'enabled': bool(enabled)}
            atomic_json(self.path, value)

    def adopt(self):
        """Back up, stop and REMOVE old launch entries before owning workers."""
        from desktop_client.startup_diagnostics import boot_mark
        boot_mark('session.adopt.begin')
        if self.legacy is None:
            from desktop_client.backend import WindowsTasks
            from desktop_client.posix_backend import PosixTasks
            self.legacy = WindowsTasks() if sys.platform == 'win32' else PosixTasks()
        for spec in self.specs.values():
            if self.legacy.exists(spec):
                self.legacy.inspect(spec)
            if hasattr(self.legacy, 'legacy'):
                self.legacy.legacy(spec)
        for key, spec in self.specs.items():
            with self.lock:
                existing = self.state()['applications'].get(key)
                enabled = existing['enabled'] if existing else True
                for _ in range(3):
                    if not self.legacy.exists(spec):
                        break
                    previous = self.legacy.inspect(spec)
                    if existing is None:
                        enabled = previous['enabled']
                    backup = self.store.root / 'backups' / ('ui-owned-' + key + '-' + str(time.time_ns()))
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    if sys.platform == 'win32':
                        from desktop_client.connections import atomic_bytes
                        atomic_bytes(backup.with_suffix('.xml'), self.legacy.xml(spec).encode('utf-8'))
                    else:
                        path = self.legacy.path(spec)
                        if not path.exists():
                            path = self.legacy.legacy(spec)[1]
                        from desktop_client.connections import atomic_bytes
                        atomic_bytes(backup.with_suffix(path.suffix), path.read_bytes())
                    if self.store.available(key):
                        self.set_entry(key, enabled)
                        existing = {'enabled': enabled}
                    self.legacy.pause(spec)
                    self.legacy.remove(spec)
                if self.legacy.exists(spec):
                    raise ClientError('旧后台入口仍存在，未启动客户端桥接。')
                if self.store.available(key):
                    self.store.import_existing(key)
                    if existing is None:
                        self.set_entry(key, enabled)
        boot_mark('session.legacy-checks.end')
        self.resume()
        boot_mark('session.adopt.end')
        return '本机接入已由客户端统一管理；退出客户端即断开，不再保留独立后台服务。'

    def resume(self):
        for key, entry in self.state()['applications'].items():
            if entry['enabled'] and (not entry.get('retry_exhausted')) and self.store.available(key):
                self.start(self.specs[key], remember=False)

    def start(self, spec, remember=True):
        from desktop_client.startup_diagnostics import boot_span
        with self.lock:
            if self.closing:
                raise ClientError('客户端正在关闭，未重新启动接入。')
            self.store.import_existing(spec.key)
            child = self.children.get(spec.key)
            if child and child.poll() is None:
                return
            with boot_span('session.process-snapshot'):
                rows = process_rows()
                if matching_processes(spec, rows)[0]:
                    raise ClientError('存在其他入口启动的桥接，未创建重复进程；请重启客户端完成迁移。')
                self.app_identities[spec.key] = native_app_identity(spec.key, rows)
            if remember:
                self.set_entry(spec.key, True)
                self.crash_attempts[spec.key] = 0
                self.control_link.reset(spec.key)
            self.crash_attempts[spec.key] = self.crash_attempts.get(spec.key, 0) + 1
            owner = {'pid': os.getpid(), 'created': psutil.Process().create_time(), 'exe': psutil.Process().exe()}
            env = dict(os.environ)
            env[OWNER_ENV] = json.dumps(owner)
            env.pop('QT_QPA_PLATFORM', None)
            for name in list(env):
                if name.startswith('_PYI_'):
                    env.pop(name, None)
            with boot_span('session.worker-launch'):
                self.children[spec.key] = self.launcher([str(spec.managed_executable), '--bridge', spec.key], env=env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **launch_options())

    def stop(self, spec, remember=False):
        with self.lock:
            if remember:
                self.set_entry(spec.key, False)
            child = self.children.get(spec.key)
            if not child:
                return
            if child.poll() is None:
                if child.stdin:
                    child.stdin.close()
                try:
                    child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    child.terminate()
                    try:
                        child.wait(timeout=4)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=4)
            self.children.pop(spec.key, None)

    def shutdown(self):
        with self.lock:
            self.closing = True
        failures = []
        for spec in self.specs.values():
            try:
                self.stop(spec)
            except Exception as exc:
                child = self.children.get(spec.key)
                try:
                    if child and child.poll() is None:
                        child.terminate()
                        child.wait(timeout=5)
                    self.children.pop(spec.key, None)
                except Exception:
                    failures.append(spec.key)
                    logging.warning('客户端子进程未确认停止：%s (%s)', spec.key, type(exc).__name__)
        if failures:
            raise ClientError('接入尚未完全关闭：' + '、'.join(failures))
        return '本机接入已断开；配对和原桌面 App 保留。'

    def supervise(self):
        with self.lock:
            if self.closing:
                return
            rows = None
            for key, entry in self.state()['applications'].items():
                child = self.children.get(key)
                if not entry['enabled'] or not self.store.available(key):
                    continue
                if child and child.poll() is None:
                    try:
                        telemetry = json.loads(self.specs[key].runtime_file.read_text(encoding='utf-8'))
                        if telemetry.get('pid') == child.pid and telemetry.get('phase') == 'running' and telemetry.get('desktop_connected'):
                            self.crash_attempts[key] = 0
                    except (OSError, ValueError, AttributeError):
                        pass
                    continue
                if child and child.poll() == EXHAUSTED_EXIT_CODE and (not entry.get('retry_exhausted')):
                    entry = dict(entry, retry_exhausted=True, app_identity=self.app_identities.get(key, []))
                    self._save_exhausted(key, entry)
                if entry.get('retry_exhausted'):
                    if rows is None:
                        rows = process_rows()
                    identity = native_app_identity(key, rows)
                    if identity and identity != entry.get('app_identity', []):
                        self.start(self.specs[key], remember=True)
                    continue
                if time.monotonic() < self.retry_at.get(key, 0):
                    continue
                if self.crash_attempts.get(key, 0) >= ATTEMPT_LIMIT:
                    self._save_exhausted(key, dict(entry, retry_exhausted=True, app_identity=self.app_identities.get(key, [])))
                    continue
                self.retry_at[key] = time.monotonic() + RETRY_INTERVAL
                self.start(self.specs[key], remember=False)

    def _save_exhausted(self, key, entry):
        value = self.state()
        value['applications'][key] = entry
        atomic_json(self.path, value)

class SessionTasks:
    app_owned = True

    def __init__(self, session=None, store=None, specs=None):
        self.session = session
        self.reader = session or ClientSession(store=store, specs=specs)

    def exists(self, spec):
        return spec.key in self.reader.state()['applications']

    def inspect(self, spec):
        entry = self.reader.state()['applications'].get(spec.key)
        if not entry:
            raise ClientError('客户端接入尚未建立，请点击接管 / 修复。')
        child = self.session.children.get(spec.key) if self.session else None
        running = bool(child and child.poll() is None)
        if self.session is None:
            rows = process_rows()
            workers, roots = matching_processes(spec, rows)
            if len(workers) == len(roots) == 1 and workers[0].get('packaged'):
                from desktop_client.independent_launch import client_gui_arguments
                parent = next((row for row in rows if row['pid'] == workers[0]['ppid']), None)
                running = bool(parent and spec.managed_executable and (normalized_path(parent.get('exe') or '') == normalized_path(spec.managed_executable)) and client_gui_arguments((parent.get('cmdline') or [])[1:]))
        return {'state': 4 if running else 3, 'enabled': entry['enabled'], 'last_result': 0, 'mode': 'app-owned', 'retry_exhausted': bool(entry.get('retry_exhausted'))}

    def require_owner(self):
        if not self.session:
            raise ClientError('请从客户端 UI 操作；不会单独启动后台。')
        return self.session

    def create(self, spec):
        self.require_owner().set_entry(spec.key, True)

    def start(self, spec):
        self.require_owner().start(spec)

    def pause(self, spec):
        self.require_owner().stop(spec, remember=True)

    def enable(self, spec, enabled):
        self.require_owner().set_entry(spec.key, enabled)

    def remove(self, spec):
        owner = self.require_owner()
        owner.stop(spec)
        owner.set_entry(spec.key, remove=True)

def client_manager(session=None):
    from desktop_client.backend import BridgeManager
    return BridgeManager(specs=session.specs if session else None, tasks=SessionTasks(session))

def launch_replacement(hidden=False):
    from desktop_client.setup import persistent_client_executable
    from desktop_client.independent_launch import launch_client
    if not getattr(sys, 'frozen', False):
        raise ClientError('重启客户端需要使用已打包的独立 App。')
    executable = persistent_client_executable(install=False)
    args = [str(executable), '--relaunch-after', str(os.getpid()), str(psutil.Process().create_time()), sys.executable]
    if hidden:
        args += ['--tray']
    env = dict(os.environ)
    env.pop(OWNER_ENV, None)
    for name in list(env):
        if name.startswith('_PYI_'):
            env.pop(name, None)
    return launch_client(args, env=env)

def wait_for_previous():
    if '--relaunch-after' not in sys.argv:
        return
    index = sys.argv.index('--relaunch-after')
    pid, created = (int(sys.argv[index + 1]), float(sys.argv[index + 2]))
    previous = sys.argv[index + 3]
    if Path(previous).name.casefold() != Path(sys.executable).name.casefold():
        raise ClientError('原客户端身份不匹配，未重启。')
    deadline = time.monotonic() + 25
    while process_identity_alive(pid, created, previous):
        if time.monotonic() > deadline:
            raise ClientError('原客户端尚未结束，未启动重复客户端。')
        time.sleep(0.1)

def send_client_action(action):
    if action not in {'close', 'restart'}:
        raise ClientError('未知客户端操作。')
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtNetwork import QLocalSocket
    from desktop_client.app import SingleInstance
    app = QCoreApplication.instance() or QCoreApplication([])
    single = SingleInstance()
    socket = QLocalSocket()
    socket.connectToServer(single.name)
    if not socket.waitForConnected(2000):
        raise ClientError('客户端 UI 未运行；没有独立后台可操作。')
    socket.write((action + '-client').encode())
    socket.waitForBytesWritten(2000)
    if not socket.waitForReadyRead(4000) or bytes(socket.readAll()) != b'accepted':
        raise ClientError('客户端未接受操作，请在 UI 中查看状态。')
    return {'accepted': True, 'action': action}
