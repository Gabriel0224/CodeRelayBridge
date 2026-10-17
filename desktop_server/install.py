"""Install a complete native package in the current user's data directory."""
import os
from pathlib import Path
import shutil
import sys
import uuid
from desktop_client.backend import ClientError, StartupShortcut
from desktop_client.platform_support import installed_executable
from desktop_server.backend import server_root
from crb.common.native_payload import package_directory, package_fingerprint

def install(root=None):
    if not getattr(sys, 'frozen', False):
        raise ClientError('请使用独立服务端 App 安装包。')
    root = Path(root or server_root())
    target = installed_executable(root, 'RelayServer')
    source = Path(sys.executable).resolve()
    if source == target.resolve():
        shortcut(target)
        return target
    source_fingerprint = package_fingerprint(package_directory(source))
    if target.exists():
        if target.is_symlink() or (root / 'app').is_symlink() or getattr(root / 'app', 'is_junction', lambda: False)():
            raise ClientError('已有安装目录为链接，未复用或覆盖。')
        if source_fingerprint != package_fingerprint(package_directory(target)):
            raise ClientError('已有其他版本或不完整的服务端安装，请从原安装打开；未覆盖运行中的程序。')
        shortcut(target)
        return target
    destination = root / 'app'
    stage = root / ('app-install-' + uuid.uuid4().hex)
    stage.mkdir(parents=True, exist_ok=False)
    try:
        if sys.platform == 'darwin':
            shutil.copytree(source.parents[2], stage / 'Relay Server.app', symlinks=True)
        else:
            if not (source.parent / '_internal').is_dir():
                raise ClientError('安装包缺少 _internal 运行时。')
            from crb.common.native_payload import copy_package
            copy_package(source.parent, stage)
        stage.rename(destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    shortcut(target)
    return target

def shortcut(target):
    target = Path(target)
    from desktop_client.package_layout import deb_layout
    if deb_layout(target, 'server'):
        return
    if sys.platform == 'darwin':
        directory = Path.home() / 'Applications'
        directory.mkdir(exist_ok=True)
        link = directory / 'Relay Server.app'
        if link.exists() or link.is_symlink():
            if link.resolve() != target.parents[2].resolve():
                raise ClientError('Applications 中已有同名其他程序，未覆盖。')
        else:
            link.symlink_to(target.parents[2], target_is_directory=True)
    elif sys.platform == 'win32':
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        try:
            shell = win32com.client.Dispatch('WScript.Shell')
            path = Path(shell.SpecialFolders('Desktop')) / 'Relay Server.lnk'
            link = shell.CreateShortcut(str(path))
            if path.exists() and Path(link.TargetPath).resolve() != target.resolve():
                raise ClientError('桌面有同名其他程序，未覆盖。')
            link.TargetPath = str(target)
            link.WorkingDirectory = str(target.parent)
            link.Description = 'Relay Server · 服务端管理'
            link.Save()
        finally:
            link = shell = None
            pythoncom.CoUninitialize()
    else:
        path = Path.home() / '.local/share/applications/relay-server.desktop'
        path.parent.mkdir(parents=True, exist_ok=True)
        q = str(target).replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace('`', '\\`').replace('%', '%%')
        if path.exists() and 'X-Relay-Executable=' + str(target) + '\n' not in path.read_text(encoding='utf-8'):
            raise ClientError('应用菜单有同名其他程序，未覆盖。')
        path.write_text('[Desktop Entry]\nType=Application\nName=Relay Server\nExec="' + q + '"\nTerminal=false\nCategories=Network;Utility;\nX-Relay-Executable=' + str(target) + '\n', encoding='utf-8')

class ServerStartup(StartupShortcut):
    NAME = 'Relay Server.lnk'

    def __new__(cls):
        if sys.platform != 'win32':
            from desktop_client.posix_backend import PosixStartup
            return PosixStartup('server')
        return object.__new__(cls)
