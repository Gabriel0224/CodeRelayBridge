"""Identify the signed-in stock desktop and its open thread writer on Mac/Windows."""
import ctypes
import os
from pathlib import Path
import plistlib
import re
import sys
from uuid import UUID
import psutil

def windows_file_users(path):
    """Read Restart Manager's list for one lock file. Never stop any resource user."""
    from ctypes import wintypes as w

    class Process(ctypes.Structure):
        _fields_ = [('pid', w.DWORD), ('started', w.FILETIME)]

    class Info(ctypes.Structure):
        _fields_ = [('process', Process), ('name', w.WCHAR * 256), ('service', w.WCHAR * 64), ('kind', w.DWORD), ('status', w.ULONG), ('session', w.DWORD), ('restartable', w.BOOL)]
    rm = ctypes.WinDLL('rstrtmgr')
    session, key = (w.DWORD(), ctypes.create_unicode_buffer(33))
    if rm.RmStartSession(ctypes.byref(session), 0, key):
        raise OSError('不能读取 Windows 文件使用状态')
    try:
        files = (w.LPCWSTR * 1)(str(path))
        if rm.RmRegisterResources(session, 1, files, 0, None, 0, None):
            raise OSError('无法登记用于查询的对话文件')
        cap, needed, why = (w.UINT(32), w.UINT(), w.DWORD())
        rows = (Info * cap.value)()
        if rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(cap), rows, ctypes.byref(why)):
            raise OSError('不能确认对话文件的使用者')
        return {row.process.pid for row in rows[:cap.value]}
    finally:
        rm.RmEndSession(session)

def owns_thread(backend, home, thread_id):
    path = home / 'thread-writer-locks' / (str(UUID(thread_id)) + '.lock')
    if path.is_symlink() or not path.is_file() or (not backend.is_running()):
        return False
    try:
        if sys.platform == 'win32':
            return backend.pid in windows_file_users(path) and backend.is_running()
        return any((f.path == str(path) for f in backend.open_files())) and backend.is_running()
    except (OSError, psutil.Error):
        return False

def stock_backend(home):
    if home != (Path.home() / '.codex').resolve():
        raise ConnectionError('桌面配置目录与当前用户不符')
    user = psutil.Process().username()
    if sys.platform == 'darwin':
        bundle = Path('/Applications/ChatGPT.app/Contents')
        with (bundle / 'Info.plist').open('rb') as f:
            version = plistlib.load(f)
        if version.get('CFBundleIdentifier') != 'com.openai.codex' or version.get('CFBundleShortVersionString') != '26.1120.50957':
            raise ConnectionError('此原 App 版本尚未经桌面队列验证')
        app_exe = bundle / 'MacOS/ChatGPT'
        cli = bundle / 'Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'
    elif sys.platform == 'win32':
        from desktop_client.independent_launch import windows_session_id
        session = windows_session_id(os.getpid())
        if not session:
            raise ConnectionError('需要已登录的 Windows 桌面')
        app_exe = Path(os.environ['ProgramFiles']) / 'WindowsApps/OpenAI.Codex_26.1120.6357.0_x64__2p2nqsd0c76g0/app/ChatGPT.exe'
        cli_dir = Path(os.environ['LOCALAPPDATA']) / 'OpenAI/Codex/bin'
    else:
        raise ConnectionError('平台不支持此连接')
    candidates = []
    for proc in psutil.process_iter(['name']):
        if (proc.info['name'] or '').lower() not in ('codex', 'codex.exe'):
            continue
        try:
            if proc.username() != user or 'app-server' not in proc.cmdline()[1:]:
                continue
            executable = Path(proc.exe())
            if sys.platform == 'darwin':
                if executable != cli:
                    continue
            elif executable.name.lower() != 'codex.exe' or executable.parent.parent != cli_dir or (not re.fullmatch('[0-9a-f]{16}', executable.parent.name)) or (windows_session_id(proc.pid) != session):
                continue
            for ancestor in proc.parents()[:4]:
                if ancestor.username() != user or Path(ancestor.exe()) != app_exe:
                    continue
                args = ancestor.cmdline()[1:]
                if any((a.startswith('--type=') for a in args)):
                    continue
                if not any((a == '--user-data-dir' or a.startswith('--user-data-dir=') for a in args)):
                    candidates.append((ancestor, proc))
                break
        except (OSError, psutil.Error):
            continue
    if len(candidates) != 1:
        raise ConnectionError('没有唯一匹配的原 Codex 桌面后端')
    return candidates[0]
