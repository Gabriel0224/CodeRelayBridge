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

def package_root(executable):
    executable = Path(executable).absolute()
    return executable.parents[2] if sys.platform == 'darwin' else executable.parent

def update_targets(executable, role):
    target = package_root(executable)
    if target.is_symlink() or getattr(target, 'is_junction', lambda: False)():
        raise UpdateError('当前程序路径含重定向，未准备替换。')
    target = target.resolve()
    targets = [target]
    root = data_root('CodexRelayClient' if role == 'client' else 'CodexRelayServer')
    managed = installed_executable(root, 'RelayClient' if role == 'client' else 'RelayServer')
    if managed.is_file():
        installed = package_root(managed)
        if installed.is_symlink() or not installed.resolve().is_relative_to(root.resolve()):
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

def transactional_replace(candidate, targets):
    """Keep full backups; restore all targets on any copy/swap failure."""
    candidate = Path(candidate)
    stamp = str(time.time_ns())
    records = []
    try:
        for target in map(Path, targets):
            if target.is_symlink() or getattr(target, 'is_junction', lambda: False)():
                raise UpdateError('待替换目录身份异常。')
            target = target.resolve()
            backup = target.with_name(target.name + '-crb-backup-' + stamp)
            replacement = target.with_name(target.name + '-crb-new-' + stamp)
            shutil.copytree(candidate, replacement, symlinks=True)
            record = {'target': target, 'backup': backup, 'new': replacement, 'inPlace': False, 'replaced': False}
            records.append(record)
            try:
                target.rename(backup)
            except OSError as error:
                if sys.platform != 'win32' or getattr(error, 'winerror', None) != 32:
                    raise
                shutil.copytree(target, backup, symlinks=True)
                record['inPlace'] = True
            if record['inPlace']:
                shutil.copytree(replacement, target, dirs_exist_ok=True, symlinks=True)
            else:
                replacement.rename(target)
            record['replaced'] = True
        return records
    except Exception:
        rollback(records)
        raise

def rollback(records):
    for row in reversed(records):
        target, backup = (row['target'], row['backup'])
        if not backup.exists():
            continue
        if row['inPlace']:
            shutil.copytree(backup, target, dirs_exist_ok=True, symlinks=True)
        else:
            if target.exists():
                target.rename(target.with_name(target.name + '-crb-failed-' + str(time.time_ns())))
            backup.rename(target)

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
        records = transactional_replace(plan['candidate'], plan['targets'])
        for target in plan['targets']:
            validate_identity(Path(target), plan['role'], plan['edition'])
        launched = launch_updated(plan)
        confirm_launch(launched, package_executable(Path(plan['targets'][0]), plan['role']))
        atomic_json(result_file, {'passed': True, 'version': payload['version'], 'role': plan['role'], 'edition': plan['edition'], 'restartedPid': launched.pid, 'backups': [str(row['backup']) for row in records], 'configurationReplaced': False})
        return 0
    except Exception as error:
        rollback(records)
        if owner_gone and plan:
            try:
                launch_updated(plan)
            except Exception:
                pass
        atomic_json(result_file, {'passed': False, 'errorType': type(error).__name__, 'message': str(error) if isinstance(error, UpdateError) else '更新未完成，旧程序已恢复。', 'configurationReplaced': False})
        return 1
