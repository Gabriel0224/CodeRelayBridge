"""Transactional replacement of owned Relay packages, never original model Apps."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import psutil
from desktop_client.connections import atomic_json
from desktop_client.platform_support import data_root, installed_executable
from desktop_client.relay_updates import UpdateError, package_executable, repository, validate_identity, verify_manifest, verify_file

class RollbackError(UpdateError):
    """Keep backups and do not launch a partially restored installation."""

def package_root(executable):
    executable = Path(executable).absolute()
    return executable.parents[2] if sys.platform == 'darwin' else executable.parent

def update_targets(executable, role):
    from desktop_client.package_layout import deb_layout
    layout = deb_layout(executable, role)
    if layout:
        return [Path(layout['directory'])]
    target = package_root(executable)
    if target.is_symlink() or getattr(target, 'is_junction', lambda: False)():
        raise UpdateError('当前程序路径含重定向，未准备替换。')
    target = target.resolve()
    targets = [target]
    root = data_root('CodexRelayClient' if role == 'client' else 'CodexRelayServer')
    managed = installed_executable(root, 'RelayClient' if role == 'client' else 'RelayServer')
    if managed.is_file():
        installed = package_root(managed)
        from desktop_client.platform_support import windows_install_directory
        registered = windows_install_directory(role)
        custom_windows = registered is not None and installed.resolve() == registered.resolve()
        if installed.is_symlink() or (not custom_windows and (not installed.resolve().is_relative_to(root.resolve()))):
            raise UpdateError('已安装程序路径身份异常。')
        installed = installed.resolve()
        if installed not in targets:
            targets.append(installed)
    return targets

def tree_hashes(root):
    from desktop_client.relay_updates import validate_tree
    root = Path(root)
    validate_tree(root)
    rows = {}
    for file in sorted(root.rglob('*')):
        relative = file.relative_to(root).as_posix()
        if file.is_symlink():
            rows[relative] = {'link': os.readlink(file)}
        elif file.is_file():
            digest = hashlib.sha256()
            with file.open('rb') as stream:
                for part in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(part)
            rows[relative] = {'sha256': digest.hexdigest(), 'bytes': file.stat().st_size}
    return rows

def build_plan(prepared):
    if not getattr(sys, 'frozen', False):
        raise UpdateError('自动安装仅用于完整的独立 App，源码运行不会替换程序。')
    owner = psutil.Process()
    work = Path(prepared['work']).resolve()
    targets = update_targets(sys.executable, prepared['role'])
    for target in targets:
        validate_identity(target, prepared['role'], prepared['edition'])
        if work.is_relative_to(target):
            raise UpdateError('更新暂存目录不能位于待替换的程序目录中。')
    plan = dict(prepared, schema=1, ownerPid=owner.pid, ownerCreated=owner.create_time(), ownerExecutable=str(Path(sys.executable).resolve()), ownerUser=owner.username(), targets=list(map(str, targets)), tree=tree_hashes(prepared['candidate']), resumeServer=False)
    file = work / 'install-plan.json'
    atomic_json(file, plan)
    if os.name != 'nt':
        file.chmod(384)
    return file

def schedule(plan_file):
    plan = json.loads(Path(plan_file).read_text(encoding='utf-8'))
    if tree_hashes(plan['candidate']) != plan['tree']:
        raise UpdateError('已校验的程序文件发生变化，拒绝启动更新。')
    from desktop_client.independent_launch import launch_independent
    candidate = package_executable(Path(plan['candidate']), plan['role'])
    return launch_independent([str(candidate), '--apply-crb-update', str(plan_file)], env=clean_launch_environment())

def clean_launch_environment():
    from desktop_client.launch_environment import external_app_environment
    from desktop_client.lifecycle import OWNER_ENV
    env = external_app_environment()
    env.pop(OWNER_ENV, None)
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return env

def wait_for_owner(plan, timeout=90):
    deadline = time.monotonic() + timeout
    while True:
        try:
            owner = psutil.Process(plan['ownerPid'])
            if owner.create_time() != plan['ownerCreated']:
                return
            if str(Path(owner.exe()).resolve()) != plan['ownerExecutable'] or owner.username() != plan['ownerUser']:
                raise UpdateError('原程序进程身份不匹配，未替换。')
            if owner.status() == psutil.STATUS_ZOMBIE:
                return
        except psutil.NoSuchProcess:
            return
        if time.monotonic() >= deadline:
            raise UpdateError('原 Relay 尚未完全退出，更新取消；没有强制结束其他进程。')
        time.sleep(0.2)

def replace_contents(source, target):
    """Mirror an owned program tree while keeping its root directory inode."""

    def remove(path):
        if path.is_symlink():
            path.unlink()
        elif getattr(path, 'is_junction', lambda: False)():
            path.rmdir()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()

    def prune(src, dst):
        for path in dst.iterdir():
            expected = src / path.name
            if path.is_dir() and (not path.is_symlink()) and (not getattr(path, 'is_junction', lambda: False)()) and expected.is_dir() and (not expected.is_symlink()):
                prune(expected, path)
            elif not expected.exists() and (not expected.is_symlink()) or path.is_symlink() or expected.is_symlink() or getattr(path, 'is_junction', lambda: False)() or (path.is_dir() != expected.is_dir()):
                remove(path)
    prune(Path(source), Path(target))
    shutil.copytree(source, target, dirs_exist_ok=True, symlinks=True)

def transactional_replace(candidate, targets):
    """Keep full backups; restore all targets on any copy/swap failure."""
    candidate = Path(candidate)
    expected = tree_hashes(candidate)
    stamp = str(time.time_ns())
    records = []
    try:
        for target in map(Path, targets):
            if target.is_symlink() or getattr(target, 'is_junction', lambda: False)():
                raise UpdateError('待替换目录身份异常。')
            target = target.resolve()
            backup = target.with_name(target.name + '-crb-backup-' + stamp)
            replacement = target.with_name(target.name + '-crb-new-' + stamp)
            record = {'target': target, 'backup': backup, 'new': replacement, 'inPlace': False, 'replaced': False, 'backupReady': False}
            records.append(record)
            shutil.copytree(candidate, replacement, symlinks=True)
            try:
                target.rename(backup)
            except OSError as error:
                if sys.platform != 'win32' or getattr(error, 'winerror', None) != 32:
                    raise
                shutil.copytree(target, backup, symlinks=True)
                record['inPlace'] = True
            record['backupReady'] = True
            if record['inPlace']:
                replace_contents(replacement, target)
            else:
                replacement.rename(target)
            if tree_hashes(target) != expected:
                raise UpdateError('更新后的程序文件与已校验的候选不一致，恢复旧程序。')
            record['replaced'] = True
            if replacement.exists():
                shutil.rmtree(replacement)
        return records
    except Exception:
        try:
            rollback(records)
        finally:
            for row in records:
                if row['new'].exists():
                    shutil.rmtree(row['new'], ignore_errors=True)
                if not row['backupReady'] and row['backup'].exists():
                    shutil.rmtree(row['backup'], ignore_errors=True)
        raise

def rollback(records):
    failures = []
    for row in reversed(records):
        target, backup = (row['target'], row['backup'])
        if not row.get('backupReady', True) or row.get('restored'):
            continue
        try:
            if not backup.is_dir():
                raise UpdateError('恢复备份不可用')
            expected = tree_hashes(backup)
            if row['inPlace']:
                replace_contents(backup, target)
            else:
                failed = target.with_name(target.name + '-crb-failed-' + str(time.time_ns()))
                if target.exists():
                    target.rename(failed)
                backup.rename(target)
                if failed.exists():
                    shutil.rmtree(failed, ignore_errors=True)
            if tree_hashes(target) != expected:
                raise UpdateError('恢复文件与备份不一致')
            row['restored'] = True
        except (OSError, UpdateError):
            failures.append(str(backup))
    if failures:
        raise RollbackError('更新未完成，部分程序尚未恢复；完整备份已保留：' + '、'.join(failures))

def launch_updated(plan):
    target = package_executable(Path(plan['targets'][0]), plan['role'])
    from desktop_client.independent_launch import launch_independent, launch_client
    if plan['role'] == 'client':
        return launch_client([str(target), '--tray'], env=clean_launch_environment())
    if plan.get('resumeServer'):
        launch_independent([str(target), '--control', str(Path(plan['work']) / 'resume-server.json'), 'start'], env=clean_launch_environment())
    return launch_independent([str(target), '--tray'], env=clean_launch_environment())

def confirm_launch(receipt, target, seconds=3):
    """Do not acknowledge a crashed bootloader as a successful update."""
    deadline = time.monotonic() + seconds
    created = None
    while True:
        try:
            process = psutil.Process(receipt.pid)
            if created is None:
                created = process.create_time()
            if process.create_time() != created or process.status() == psutil.STATUS_ZOMBIE or Path(process.exe()).resolve() != Path(target).resolve():
                raise UpdateError('更新后的 Relay 启动身份不匹配，恢复旧程序。')
        except psutil.NoSuchProcess:
            raise UpdateError('更新后的 Relay 启动失败，恢复旧程序。') from None
        if time.monotonic() >= deadline:
            return
        time.sleep(0.1)

def apply(plan_file):
    plan_file = Path(plan_file).resolve()
    result_file = plan_file.with_name('install-result.json')
    records = []
    native_transaction = None
    previous_registration_version = None
    owner_gone = False
    plan = None
    try:
        plan = json.loads(plan_file.read_text(encoding='utf-8'))
        if plan['schema'] != 1 or plan_file.parent != Path(plan['work']).resolve():
            raise UpdateError('更新计划身份异常。')
        root = data_root('CodexRelayClient' if plan['role'] == 'client' else 'CodexRelayServer') / 'updates'
        if not plan_file.is_relative_to(root.resolve()) or plan_file.parent.is_symlink():
            raise UpdateError('更新计划不属于本机更新缓存。')
        expected = list(map(str, update_targets(plan['ownerExecutable'], plan['role'])))
        if plan['targets'] != expected or psutil.Process().username() != plan['ownerUser']:
            raise UpdateError('更新目标或用户不匹配。')
        payload = verify_manifest(plan['release']['manifest'], repository(plan['role'], plan['edition']))
        package = plan['release']['package']
        if package not in payload['packages']:
            raise UpdateError('更新包不在签名清单内。')
        verify_file(plan['archive'], package['bytes'], package['sha256'])
        validate_identity(Path(plan['candidate']), plan['role'], plan['edition'])
        if tree_hashes(plan['candidate']) != plan['tree']:
            raise UpdateError('更新程序文件发生变化。')
        wait_for_owner(plan)
        owner_gone = True
        target_exes = {str(package_executable(Path(p), plan['role'])) for p in plan['targets']}
        for process in psutil.process_iter(['pid', 'exe', 'username']):
            executable = process.info.get('exe')
            if process.info['username'] == plan['ownerUser'] and executable and (str(Path(executable).resolve()) in target_exes):
                raise UpdateError('本机 Relay 进程尚未全部退出，未替换程序。')
        from desktop_client.package_layout import deb_layout
        if deb_layout(plan['ownerExecutable'], plan['role']):
            from desktop_client.system_deb_update import apply as install_deb
            native_transaction = install_deb(plan)
        else:
            records = transactional_replace(plan['candidate'], plan['targets'])
        for target in plan['targets']:
            validate_identity(Path(target), plan['role'], plan['edition'])
        if sys.platform == 'win32':
            previous_registration_version = update_installer_registration(plan['role'], plan['targets'][0], payload['version'])
        launched = launch_updated(plan)
        confirm_launch(launched, package_executable(Path(plan['targets'][0]), plan['role']))
        atomic_json(result_file, {'passed': True, 'version': payload['version'], 'role': plan['role'], 'edition': plan['edition'], 'restartedPid': launched.pid, 'backups': ([native_transaction['archive']] if native_transaction else []) + [str(row['backup']) for row in records], 'configurationReplaced': False})
        return 0
    except Exception as error:
        recovered = not isinstance(error, RollbackError)
        try:
            rollback(records)
        except UpdateError as recovery_error:
            error, recovered = (recovery_error, False)
        if native_transaction:
            try:
                from desktop_client.system_deb_update import restore
                restore(native_transaction)
            except UpdateError as recovery_error:
                error, recovered = (recovery_error, False)
        if previous_registration_version is not None:
            try:
                update_installer_registration(plan['role'], plan['targets'][0], previous_registration_version)
            except OSError:
                pass
        if owner_gone and plan and recovered:
            try:
                launch_updated(plan)
            except Exception:
                pass
        atomic_json(result_file, {'passed': False, 'errorType': type(error).__name__, 'message': str(error) if isinstance(error, UpdateError) else '更新未完成，旧程序已恢复。', 'configurationReplaced': False})
        return 1

def update_installer_registration(role, target, version):
    """Keep the Windows uninstall entry aligned after a verified payload update."""
    import winreg
    path = 'Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\CodeRelayBridge.' + role + '_is1'
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY)
    except FileNotFoundError:
        return
    with key:
        location = winreg.QueryValueEx(key, 'InstallLocation')[0]
        if Path(location).resolve() != Path(target).resolve():
            raise UpdateError('当前程序路径含重定向，未准备替换。')
        previous = winreg.QueryValueEx(key, 'DisplayVersion')[0]
        winreg.SetValueEx(key, 'DisplayVersion', 0, winreg.REG_SZ, version)
        return previous
