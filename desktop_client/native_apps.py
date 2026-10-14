"""Launch or request exit of desktop applications, never CLI/bridge engines.

Discovery is local and on demand. Starting an App does not pair it, resume a
paused bridge, restart an existing App, or make it part of the client's lifetime.
"""
from __future__ import annotations
from dataclasses import dataclass
import os
from pathlib import Path
import plistlib
import shutil
import signal
import subprocess
import sys
import threading
import time
import psutil
from desktop_client.backend import ClientError, native_app_running, process_rows
from desktop_client.launch_environment import external_app_environment
TITLES = {'codex': 'Codex', 'deepseek': 'DeepSeek'}

@dataclass(frozen=True)
class LaunchTarget:
    method: str
    value: str

def windows_codex_id():
    """Use the current user's registered MSIX App identity, not a versioned EXE."""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    try:
        folder = win32com.client.Dispatch('Shell.Application').Namespace('shell:AppsFolder')
        if folder is not None:
            for item in folder.Items():
                app_id = str(item.ExtendedProperty('System.AppUserModel.ID') or '')
                if app_id.startswith('OpenAI.Codex_') and '!' in app_id:
                    return app_id
    finally:
        pythoncom.CoUninitialize()
    return None

def find_target(key, platform=None, home=None, local=None):
    if key not in TITLES:
        raise ClientError('该应用尚未支持从客户端启动。')
    platform = platform or sys.platform
    home = Path(home or Path.home())
    if platform == 'win32':
        local = Path(local or os.environ.get('LOCALAPPDATA', home / 'AppData/Local'))
        if key == 'codex':
            app_id = windows_codex_id()
            if app_id:
                return LaunchTarget('windows-app', app_id)
            candidates = [local / 'Programs/Codex/Codex.exe', local / 'Programs/Codex/ChatGPT.exe']
        else:
            candidates = [local / 'Programs/DeepSeek Harness/DeepSeek Harness.exe']
            candidates += [Path(os.environ[var]) / 'DeepSeek Harness/DeepSeek Harness.exe' for var in ('PROGRAMFILES', 'PROGRAMFILES(X86)') if os.environ.get(var)]
        for path in candidates:
            if path.is_file():
                return LaunchTarget('executable', str(path))
    elif platform == 'darwin':
        names = ('Codex.app', 'ChatGPT.app') if key == 'codex' else ('DeepSeek Harness.app',)
        for root in (Path('/Applications'), home / 'Applications'):
            for name in names:
                bundle = root / name
                if not bundle.is_dir():
                    continue
                if name == 'ChatGPT.app':
                    try:
                        info = plistlib.loads((bundle / 'Contents/Info.plist').read_bytes())
                    except (OSError, ValueError, plistlib.InvalidFileException):
                        continue
                    if info.get('CFBundleIdentifier') != 'com.openai.codex':
                        continue
                return LaunchTarget('mac-app', str(bundle))
    else:
        names = ('codex.desktop', 'chatgpt.desktop') if key == 'codex' else ('dsh-desktop.desktop', 'deepseek-harness.desktop')
        roots = [Path(os.environ.get('XDG_DATA_HOME', home / '.local/share')) / 'applications']
        roots += [Path(p) / 'applications' for p in os.environ.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share').split(':') if p]
        for root in roots:
            for name in names:
                entry = root / name
                if not entry.is_file():
                    continue
                if name == 'chatgpt.desktop':
                    try:
                        text = entry.read_text(encoding='utf-8')
                    except (OSError, UnicodeError):
                        continue
                    if 'x-scheme-handler/codex' not in text:
                        continue
                if shutil.which('gio'):
                    return LaunchTarget('linux-desktop', str(entry))
        candidates = [home / '.local/bin/dsh-desktop', home / '.local/share/deepseek-harness/dsh-desktop-launcher'] if key == 'deepseek' else []
        for path in candidates:
            if path.is_file() and os.access(path, os.X_OK):
                return LaunchTarget('executable', str(path))
    raise ClientError(f'未找到 {TITLES[key]} 桌面 App。请先安装该应用，再点击启动；配对和桥接未改变。')

def dispatch(target):
    """Use the OS desktop launcher with no shell, console, or bridge ownership."""
    if target.method == 'windows-app':
        os.startfile('shell:AppsFolder\\' + target.value)
    elif target.method in {'mac-app', 'linux-desktop'}:
        args = ['/usr/bin/open', '-a', target.value] if target.method == 'mac-app' else [shutil.which('gio') or 'gio', 'launch', target.value]
        options = {'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL} if target.method == 'linux-desktop' else {'capture_output': True}
        result = subprocess.run(args, stdin=subprocess.DEVNULL, text=True, timeout=12, start_new_session=True, env=external_app_environment(), **options)
        if result.returncode:
            raise ClientError('系统未能启动桌面 App：' + ((result.stderr or '').strip() or str(result.returncode)))
    elif target.method == 'executable':
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {'start_new_session': True}
        subprocess.Popen([target.value], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, env=external_app_environment(), **options)
    else:
        raise ClientError('不支持的桌面 App 启动方式。')

def activate_windows_question_link(app_id):
    """MSIX activation, with a fixed loopback-only UI channel; no shell script."""
    import ctypes
    from ctypes import wintypes
    import uuid
    import pythoncom
    if sys.platform != 'win32' or not app_id.startswith('OpenAI.Codex_') or '!' not in app_id:
        raise ClientError('此联动启动方式仅支持 Windows 官方 Codex 桌面 App。')

    class GUID(ctypes.Structure):
        _fields_ = [('data1', wintypes.DWORD), ('data2', wintypes.WORD), ('data3', wintypes.WORD), ('data4', ctypes.c_ubyte * 8)]
    clsid = GUID.from_buffer_copy(uuid.UUID('45ba127d-10a8-46ea-8ab7-56ea9078943c').bytes_le)
    iid = GUID.from_buffer_copy(uuid.UUID('2e941141-7f97-4756-ba1d-9decde894a3d').bytes_le)
    ole = ctypes.OleDLL('ole32')
    create = ole.CoCreateInstance
    create.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    create.restype = ctypes.c_long
    pointer = ctypes.c_void_p()
    pythoncom.CoInitialize()
    try:
        hr = create(ctypes.byref(clsid), None, 4, ctypes.byref(iid), ctypes.byref(pointer))
        if hr < 0 or not pointer:
            raise ClientError('Windows 未能创建 Codex 应用启动器。')
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        activate = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))(table[3])
        pid = wintypes.DWORD()
        hr = activate(pointer, app_id, '--remote-debugging-address=127.0.0.1 --remote-debugging-port=19489', 2, ctypes.byref(pid))
        if hr < 0:
            raise ClientError('Windows 未能以提问窗口联动模式启动 Codex。')
        return pid.value
    finally:
        if pointer:
            table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(table[2])(pointer)
        pythoncom.CoUninitialize()

def dispatch_question_link(target):
    if target.method not in {'windows-app', 'mac-app', 'linux-desktop'}:
        raise ClientError('该桌面 App 安装方式尚未验证提问窗口联动。')
    import socket
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', 19489))
        except OSError as exc:
            raise ClientError('本机联动端口已占用，未启动或重启 App。') from exc
    flags = ['--remote-debugging-address=127.0.0.1', '--remote-debugging-port=19489']
    if target.method == 'windows-app':
        activate_windows_question_link(target.value)
    elif target.method == 'mac-app':
        try:
            info = plistlib.loads((Path(target.value) / 'Contents/Info.plist').read_bytes())
        except (OSError, ValueError, plistlib.InvalidFileException) as exc:
            raise ClientError('未能核验 Codex App 身份，未启动。') from exc
        if info.get('CFBundleIdentifier') != 'com.openai.codex':
            raise ClientError('不是 Codex App，未启用控制通道。')
        result = subprocess.run(['/usr/bin/open', '-a', target.value, '--args', *flags], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=12, start_new_session=True, env=external_app_environment())
        if result.returncode:
            raise ClientError('macOS 未能联动启动 Codex App。')
    else:
        image = Path('/usr/lib/chatgpt/ChatGPT')
        entry = Path(target.value)
        if entry.name not in {'chatgpt.desktop', 'codex.desktop'} or not image.is_file():
            raise ClientError('该 Linux Codex 安装方式尚未验证，未启动。')
        if 'x-scheme-handler/codex' not in entry.read_text(encoding='utf-8'):
            raise ClientError('Linux 桌面入口不是 Codex，未启动。')
        subprocess.Popen([str(image), *flags], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True, env=external_app_environment())

def desktop_roots(key, rows):
    """Only desktop App roots, not their renderer/helper children or Codex CLI."""
    candidates = [row for row in rows if native_app_running(key, [row])]
    pids = {row.get('pid') for row in candidates if row.get('pid')}
    return [row for row in candidates if row.get('ppid') not in pids]

def checked_app_process(key, row):
    """Revalidate PID, image, owner and start time immediately before exit.

Never trust stale telemetry, terminate another user's App, or target a PID that
has been reused. Electron renderers may share the main executable but are not
independent desktop applications.
"""
    try:
        proc = psutil.Process(row['pid'])
        if proc.create_time() != row.get('create_time'):
            return None
        exe = proc.exe()
        expected = str(row.get('exe') or '')
        compare = lambda p: p.replace('\\', '/').casefold() if sys.platform == 'win32' else p
        if not expected or compare(exe) != compare(expected):
            return None
        if not native_app_running(key, [{'name': proc.name(), 'exe': exe}]):
            return None
        if any((arg.startswith('--type=') for arg in proc.cmdline())):
            return None
        owner, current = (proc.username(), psutil.Process().username())
        if owner.casefold() != current.casefold() if sys.platform == 'win32' else owner != current:
            raise ClientError('该桌面 App 属于其他系统用户，未关闭。')
        return proc
    except psutil.NoSuchProcess:
        return None
    except (KeyError, psutil.AccessDenied) as exc:
        raise ClientError('无法核验桌面 App 进程，未关闭：' + str(exc)) from exc

def request_app_close(key, roots):
    """OS-native non-forced exit, scoped to currently verified App processes."""
    return request_verified_app_close(roots, lambda row: checked_app_process(key, row), 'com.openai.codex' if key == 'codex' else None)

def request_verified_app_close(roots, checker, expected_bundle_id=None):
    """Shared OS quit transport; identity verification remains provider-owned."""
    live = [(row, proc) for row in roots if (proc := checker(row)) is not None]
    if not live:
        return
    if sys.platform == 'win32':
        import win32con
        import win32gui
        import win32process
        by_pid = {proc.pid: row for row, proc in live}
        posted = []

        def close_window(hwnd, _):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid in by_pid and (not win32gui.GetWindow(hwnd, win32con.GW_OWNER)) and (win32gui.IsWindowVisible(hwnd) or win32gui.GetWindowText(hwnd)) and (checker(by_pid[pid]) is not None):
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                posted.append(hwnd)
        win32gui.EnumWindows(close_window, None)
        if not posted:
            raise ClientError('未找到可正常关闭的 App 窗口，未强制结束；请先显示该 App 再试。')
    elif sys.platform == 'darwin':
        bundles = set()
        for row, proc in live:
            bundle = next((p for p in Path(proc.exe()).parents if p.suffix == '.app'), None)
            if not bundle:
                raise ClientError('未找到可核验的桌面 App 包，未关闭。')
            info = plistlib.loads((bundle / 'Contents/Info.plist').read_bytes())
            identity = info.get('CFBundleIdentifier')
            if not isinstance(identity, str) or not identity or (expected_bundle_id and identity != expected_bundle_id):
                raise ClientError('桌面 App 身份不匹配，未关闭。')
            if identity in bundles or checker(row) is None:
                continue
            bundles.add(identity)
            escaped = identity.replace('\\', '\\\\').replace('"', '\\"')
            script = f'if application id "{escaped}" is running then\n tell application id "{escaped}" to quit\nend if'
            result = subprocess.run(['/usr/bin/osascript', '-e', script], capture_output=True, text=True, timeout=12, start_new_session=True)
            if result.returncode:
                raise ClientError('系统未能关闭桌面 App：' + (result.stderr.strip() or str(result.returncode)))
    else:
        for row, _ in live:
            proc = checker(row)
            if proc is not None:
                try:
                    proc.send_signal(signal.SIGTERM)
                except psutil.NoSuchProcess:
                    pass

class DesktopAppLauncher:

    def __init__(self, processes=None, finder=None, start=None, sleep=time.sleep, clock=time.monotonic, stop=None, link_start=None):
        self.processes = processes or process_rows
        self.finder = finder or find_target
        self.start = start or dispatch
        self.link_start = link_start or dispatch_question_link
        self.stop = stop or request_app_close
        self.sleep, self.clock = (sleep, clock)
        self.lock = threading.Lock()

    def launch(self, key, timeout=10, question_link=False):
        if key not in TITLES:
            raise ClientError('该应用尚未支持从客户端启动。')
        title = TITLES[key]
        if question_link and key != 'codex':
            raise ClientError('此联动启动方式仅支持 Codex。')
        with self.lock:
            if native_app_running(key, self.processes()):
                return f'{title} 桌面 App 已在运行，未重复启动。'
            target = self.finder(key)
            (self.link_start if question_link else self.start)(target)
            deadline = self.clock() + timeout
            while True:
                if native_app_running(key, self.processes()):
                    return f'{title} 桌面 App 已启动；原有配对和桥接开关保持不变。'
                if self.clock() >= deadline:
                    return f'{title} 启动请求已发出，暂未检测到运行；请检查桌面 App 或稍后刷新状态。'
                self.sleep(0.2)

    def close(self, key, timeout=10):
        if key not in TITLES:
            raise ClientError('该应用尚未支持从客户端关闭。')
        title = TITLES[key]
        with self.lock:
            roots = desktop_roots(key, self.processes())
            if not roots:
                return f'{title} 桌面 App 未运行，无需关闭。'
            self.stop(key, roots)
            deadline = self.clock() + timeout
            while True:
                if not native_app_running(key, self.processes()):
                    return f'{title} 桌面 App 已关闭；原有配对和桥接开关保持不变。'
                if self.clock() >= deadline:
                    return f'{title} 关闭请求已发出，但 App 仍在运行；可能需要处理 App 内的确认。未强制结束，请检查后重试。'
                self.sleep(0.2)

def launch_probe(exercise=False):
    launcher = DesktopAppLauncher()
    items = []
    for key, title in TITLES.items():
        row = {'key': key, 'title': title, 'running': native_app_running(key, process_rows())}
        try:
            target = find_target(key)
            row.update(installed=True, method=target.method, target=target.value)
            if exercise:
                row['result'] = launcher.launch(key)
                row['running_after'] = native_app_running(key, process_rows())
        except Exception as exc:
            from desktop_client.backend import redact
            row.update(installed=False, error=redact(str(exc)))
        items.append(row)
    return {'platform': sys.platform, 'exercised': exercise, 'applications': items, 'passed': all((r.get('installed') and (not exercise or r.get('running_after')) for r in items))}

def close_probe():
    """Read-only installed-process validation. Never send a close request."""
    from desktop_client.backend import redact
    items = []
    rows = process_rows()
    for key, title in TITLES.items():
        roots = desktop_roots(key, rows)
        value = {'key': key, 'title': title, 'running': native_app_running(key, rows), 'verifiedRootPids': [], 'method': 'WM_CLOSE' if sys.platform == 'win32' else 'Apple quit event' if sys.platform == 'darwin' else 'SIGTERM'}
        try:
            for row in roots:
                if checked_app_process(key, row) is not None:
                    value['verifiedRootPids'].append(row['pid'])
            value['passed'] = not value['running'] or bool(value['verifiedRootPids'])
        except Exception as exc:
            value.update(passed=False, error=redact(str(exc)))
        items.append(value)
    return {'platform': sys.platform, 'readOnly': True, 'closeRequestsSent': 0, 'applications': items, 'passed': all((row['passed'] for row in items))}
