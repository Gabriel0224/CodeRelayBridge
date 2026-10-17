"""Use dpkg for system installations; never overwrite /opt behind its database."""
import email.parser
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
from desktop_client.package_layout import deb_layout, deb_package_name
from desktop_client.relay_updates import UpdateError, verify_file

def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def rollback_package(plan, layout):
    package = deb_package_name(plan['role'])
    old = subprocess.run(['/usr/bin/dpkg-query', '--status', package], check=True, capture_output=True, text=True, timeout=15)
    control = email.parser.Parser().parsestr(old.stdout)
    if control.get('Status') != 'install ok installed' or control.get('Package') != package or control.get('X-CRB-Role') != plan['role'] or (control.get('X-CRB-Edition') != plan['edition']) or (control.get('Version') != layout['version'] + '-1'):
        raise UpdateError('已安装程序与包管理器记录不一致，未更新。')
    tree = Path(plan['work']) / 'system-rollback'
    tree.mkdir(mode=448, exist_ok=False)
    tree.chmod(493)
    roots = [Path(layout['directory']), Path('/usr/share/doc') / package]
    singles = {Path('/usr/share/applications') / (package + '.desktop'), Path('/usr/bin') / package}
    files = subprocess.run(['/usr/bin/dpkg-query', '--listfiles', package], check=True, capture_output=True, text=True, timeout=15).stdout.splitlines()
    for name in files:
        source = Path(name)
        if source.is_dir() and (not source.is_symlink()):
            continue
        if not (source in singles or any((source.is_relative_to(root) for root in roots))):
            raise UpdateError('包管理器包含意外安装路径，未更新。')
        stat = source.lstat()
        if stat.st_uid != 0 or (not source.is_symlink() and stat.st_mode & 18):
            raise UpdateError('系统安装文件权限异常，未更新。')
        if source.is_symlink() and (not source.resolve().is_relative_to(Path(layout['directory']))):
            raise UpdateError('系统安装文件权限异常，未更新。')
        target = tree / source.relative_to('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            target.symlink_to(os.readlink(source))
        else:
            shutil.copy2(source, target)
    metadata = tree / 'DEBIAN'
    metadata.mkdir()
    allowed = ('Package', 'Version', 'Architecture', 'Section', 'Priority', 'Maintainer', 'Depends', 'Conflicts', 'Breaks', 'Replaces', 'Provides', 'Description', 'Homepage', 'X-CRB-Role', 'X-CRB-Edition')
    (metadata / 'control').write_text(''.join((key + ': ' + control[key] + '\n' for key in allowed if key in control)), encoding='utf-8')
    for name in ('preinst', 'prerm', 'postinst', 'postrm'):
        source = Path('/var/lib/dpkg/info') / (package + '.' + name)
        if source.exists():
            if source.is_symlink() or source.stat().st_uid != 0 or source.stat().st_mode & 18:
                raise UpdateError('系统安装文件权限异常，未更新。')
            shutil.copy2(source, metadata / name)
    archive = Path(plan['work']) / (package + '-rollback.deb')
    subprocess.run(['/usr/bin/dpkg-deb', '-Zxz', '--root-owner-group', '--build', str(tree), str(archive)], check=True, capture_output=True, timeout=180)
    return archive

def install_command(archive):
    command = ['/usr/bin/dpkg', '--install', str(archive)]
    if os.geteuid() != 0:
        command = ['/usr/bin/pkexec', *command]
    return subprocess.run(command, capture_output=True, timeout=300)

def apply(plan):
    layout = deb_layout(plan['ownerExecutable'], plan['role'])
    if not layout or not str(plan['archive']).endswith('.deb'):
        raise UpdateError('系统安装文件权限异常，未更新。')
    from crb.common.deb_archive import read_control
    package = plan['release']['package']
    control = read_control(plan['archive'])
    if control.get('Package') != deb_package_name(plan['role']) or control.get('X-CRB-Role') != plan['role'] or control.get('X-CRB-Edition') != plan['edition'] or (control.get('Version') != package['version'] + '-1'):
        raise UpdateError('更新包不属于当前程序或版本类型，拒绝替换。')
    backup = rollback_package(plan, layout)
    backup_digest, backup_size = (digest(backup), backup.stat().st_size)
    verify_file(plan['archive'], package['bytes'], package['sha256'])
    result = install_command(plan['archive'])
    if result.returncode in {126, 127}:
        raise UpdateError('系统授权已取消，原程序未改变。')
    if result.returncode:
        verify_file(backup, backup_size, backup_digest)
        if install_command(backup).returncode:
            raise UpdateError('系统安装和回滚未完成，请使用保存的回滚安装包修复。')
        raise UpdateError('系统安装失败，已通过包管理器恢复旧程序。')
    return {'archive': str(backup), 'sha256': backup_digest, 'bytes': backup_size}

def restore(transaction):
    verify_file(transaction['archive'], transaction['bytes'], transaction['sha256'])
    if install_command(transaction['archive']).returncode:
        raise UpdateError('系统安装和回滚未完成，请使用保存的回滚安装包修复。')
