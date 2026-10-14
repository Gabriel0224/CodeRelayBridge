"""Launch the client outside the invoking App's Windows job, not just invisibly.

CREATE_NO_WINDOW/DETACHED_PROCESS do not escape job objects. Even
CREATE_BREAKAWAY_FROM_JOB cannot escape a non-breakaway ancestor job.
Local WMI starts a fresh process through the OS provider instead. No service,
scheduled task, extra resident helper, shell, or external Python is installed.
Bridge workers deliberately do NOT use this launcher: they belong to the UI.
"""
import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
import psutil
from desktop_client.backend import ClientError, normalized_path
BOOTSTRAP_FLAG = '--independent-client-start'

def windows_job_member(pid=None, job=None):
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    kernel.IsProcessInJob.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.GetCurrentProcess() if pid is None else kernel.OpenProcess(4096, False, int(pid))
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        member = wintypes.BOOL()
        if not kernel.IsProcessInJob(handle, job, ctypes.byref(member)):
            raise ctypes.WinError(ctypes.get_last_error())
        return bool(member.value)
    finally:
        if pid is not None:
            kernel.CloseHandle(handle)

def windows_session_id(pid):
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel.ProcessIdToSessionId.restype = wintypes.BOOL
    session = wintypes.DWORD()
    if not kernel.ProcessIdToSessionId(int(pid), ctypes.byref(session)):
        raise ctypes.WinError(ctypes.get_last_error())
    return session.value

@dataclass(frozen=True)
class LaunchReceipt:
    pid: int
    created: float

def _wmi_create(args):
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    locator = service = startup = process = params = result = None
    try:
        locator = win32com.client.Dispatch('WbemScripting.SWbemLocator')
        service = locator.ConnectServer('.', 'root\\cimv2')
        service.Security_.ImpersonationLevel = 3
        startup = service.Get('Win32_ProcessStartup').SpawnInstance_()
        startup.CreateFlags = subprocess.CREATE_BREAKAWAY_FROM_JOB | subprocess.CREATE_NO_WINDOW
        process = service.Get('Win32_Process')
        params = process.Methods_('Create').InParameters.SpawnInstance_()
        params.CommandLine = subprocess.list2cmdline(args)
        params.CurrentDirectory = str(Path(args[0]).parent)
        params.ProcessStartupInformation = startup
        result = process.ExecMethod_('Create', params)
        code, pid = (int(result.ReturnValue), int(result.ProcessId or 0))
        if code or pid <= 0:
            raise ClientError('Windows 独立启动失败（代码 ' + str(code) + '）；未使用依附原 App 的启动方式。')
        return pid
    finally:
        result = params = process = startup = service = locator = None
        pythoncom.CoUninitialize()

def launch_independent(args, *, env=None):
    args = [str(value) for value in args]
    if not args or not Path(args[0]).is_absolute() or (not Path(args[0]).is_file()):
        raise ClientError('独立启动需要完整、存在的程序路径。')
    if any(('\x00' in value for value in args)):
        raise ClientError('独立启动参数无效。')
    if sys.platform != 'win32':
        return subprocess.Popen(args, env=env, cwd=str(Path(args[0]).parent), start_new_session=True, close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        pid = _wmi_create(args)
        child = psutil.Process(pid)
        if normalized_path(child.exe()) != normalized_path(args[0]):
            raise ClientError('独立启动的程序身份不匹配。')
        if child.username().casefold() != psutil.Process().username().casefold():
            raise ClientError('独立启动的用户身份不匹配。')
        if windows_session_id(pid) != windows_session_id(os.getpid()):
            raise ClientError('独立启动未进入当前桌面会话，未确认托盘客户端可用。')
        return LaunchReceipt(pid, child.create_time())
    except ClientError:
        raise
    except Exception as exc:
        raise ClientError('Windows 独立启动未成功（' + type(exc).__name__ + '）；请从桌面快捷方式启动或查看日志。') from None

def client_gui_arguments(args):
    """Recognize a UI, including its temporary bootstrap/restart arguments."""
    args = list(args)
    if BOOTSTRAP_FLAG in args:
        args.remove(BOOTSTRAP_FLAG)
    if '--tray' in args:
        args.remove('--tray')
    if '--relaunch-after' in args:
        index = args.index('--relaunch-after')
        if len(args[index:index + 4]) != 4:
            return False
        del args[index:index + 4]
    return not args

def launch_client(args, *, env=None):
    args = list(map(str, args))
    if sys.platform == 'win32' and BOOTSTRAP_FLAG not in args:
        args.insert(1, BOOTSTRAP_FLAG)
    return launch_independent(args, env=env)

def detach_gui_if_needed():
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        return False
    if not client_gui_arguments(sys.argv[1:]):
        return False
    bootstrapped = BOOTSTRAP_FLAG in sys.argv[1:]
    if bootstrapped:
        sys.argv.remove(BOOTSTRAP_FLAG)
        return False
    if windows_job_member():
        launch_client([sys.executable, *sys.argv[1:]])
        return True
    return False
