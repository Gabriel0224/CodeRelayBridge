"""Exact native-payload identity, shared by both first-run installers."""
import hashlib
import os
import shutil
from pathlib import Path
import sys
from desktop_client.backend import ClientError

def package_directory(executable):
    executable = Path(executable)
    return executable.parents[2] if sys.platform == 'darwin' else executable.parent

def package_fingerprint(directory):
    directory = Path(directory).resolve()
    files = {}
    for path in sorted(directory.rglob('*')):
        key = path.relative_to(directory).as_posix()
        if path.is_symlink():
            if sys.platform == 'win32' or not path.resolve().is_relative_to(directory):
                raise ClientError('安装包包含指向外部的链接，未复用。')
            files[key] = ('link', os.readlink(path))
        elif getattr(path, 'is_junction', lambda: False)():
            raise ClientError('安装目录包含目录联接，未复用。')
        elif path.is_file():
            with path.open('rb') as stream:
                files[key] = ('file', hashlib.file_digest(stream, 'sha256').hexdigest())
    return files

def copy_package(source, destination):
    """Copy every verified payload member, including native helper executables."""
    source, destination = (Path(source).resolve(), Path(destination).resolve())
    if destination.is_relative_to(source):
        raise ClientError('安装暂存目录不能位于源程序目录内。')
    fingerprint = package_fingerprint(source)
    shutil.copytree(source, destination, dirs_exist_ok=True, symlinks=sys.platform != 'win32')
    if package_fingerprint(destination) != fingerprint:
        raise ClientError('完整安装文件校验失败，未启用安装目录。')
