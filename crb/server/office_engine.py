"""Native, recoverable setup of the shared, network-isolated document engine."""
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
import uuid
from desktop_server.document_service import EngineResources, HELPER, IMAGE, SOCKETS, identity, private_json, socket_request
from crb.server.office_wire import WIRE_SOURCE
RUNTIMES = {('Windows', 'amd64'): ('win/main/amd64/244584/Docker%20Desktop%20Installer.exe', '2668273f82b342a058a28f9ab53ea7bddde10bb6d51307ce2ec54c2d36459744'), ('Darwin', 'arm64'): ('mac/main/arm64/244584/Docker.dmg', '94e4ccec4a035b7dcdf67cfff0520d4b46ee873ae04b155abc0d22b333cc90ad'), ('Darwin', 'amd64'): ('mac/main/amd64/244584/Docker.dmg', '98da1a82dac734ba6a14c3e0379f5655ce0bd52a04cf8bb47f4e4642c914f695')}

def state_path(config):
    return Path(config['state_dir']) / 'managed-document-engine.json'

def state(config, required=True):
    path = state_path(config)
    if not path.exists():
        if required:
            raise ValueError('文档组件尚未安装，请在服务端点击“安装并启用”')
        return None
    if path.is_symlink():
        raise ValueError('文档组件登记无效')
    data = json.loads(path.read_text(encoding='utf-8'))
    identity(data.get('owner'))
    if data.get('image') != IMAGE or data.get('mode') not in ('direct', 'system', 'pending'):
        raise ValueError('文档组件版本或运行方式不匹配')
    if not isinstance(data.get('secret'), str) or len(data['secret']) < 43:
        raise ValueError('文档组件密钥登记无效')
    return data

def docker_path():
    candidates = []
    if os.name == 'nt':
        for root in (os.environ.get('ProgramFiles', 'C:/Program Files'), str(Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Programs')):
            candidates.append(Path(root) / 'Docker/Docker/resources/bin/docker.exe')
    elif platform.system() == 'Darwin':
        candidates = [Path('/Applications/Docker.app/Contents/Resources/bin/docker'), Path.home() / 'Applications/Docker.app/Contents/Resources/bin/docker', Path('/usr/local/bin/docker')]
    else:
        candidates = [Path('/usr/bin/docker'), Path('/usr/local/bin/docker')]
    found = shutil.which('docker')
    if found:
        candidates.append(Path(found))
    return next((str(path) for path in candidates if path.is_file()), None)

def docker_command(data):
    executable = data.get('docker')
    if not executable or not Path(executable).is_absolute() or (not Path(executable).is_file()):
        raise ValueError('未找到已登记的文档运行环境')
    context = data.get('context', '')
    if not isinstance(context, str) or len(context) > 256 or context.startswith('-'):
        raise ValueError('文档运行环境登记无效')
    prefix = [executable] + (['--context', context] if context else [])

    def command(args, timeout=30):
        return subprocess.run(prefix + args, capture_output=True, timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    command.prefix = prefix
    return command

def runtime(data=None):
    executable = (data or {}).get('docker') or docker_path()
    if not executable:
        return None
    probe = {'docker': executable, 'context': (data or {}).get('context', '')}
    command = docker_command(probe)
    if not probe['context']:
        selected = command(['context', 'show'], timeout=15)
        if selected.returncode:
            return None
        probe['context'] = selected.stdout.decode().strip()
        command = docker_command(probe)
    inspected = command(['context', 'inspect', probe['context']], timeout=15)
    if inspected.returncode:
        return None
    endpoint = json.loads(inspected.stdout)[0].get('Endpoints', {}).get('docker', {}).get('Host', '')
    if not endpoint.startswith(('unix://', 'npipe://')):
        raise ValueError('自动安装只支持本机运行环境，请选择本机 Docker context')
    result = command(['info', '--format', '{{json .}}'], timeout=15)
    if result.returncode:
        return None
    information = json.loads(result.stdout)
    if information.get('OSType') != 'linux':
        raise ValueError('文档编辑需要 Linux 容器，请在 Docker Desktop 中切换到 Linux 容器')
    if information.get('MemTotal', 0) < 3500 * 1024 * 1024:
        raise ValueError('请给文档运行环境分配至少 4 GB 内存')
    return probe

def start_desktop():
    if os.name == 'nt':
        roots = [Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Docker/Docker', Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Programs/Docker/Docker']
        for root in roots:
            executable = root / 'Docker Desktop.exe'
            if executable.is_file():
                subprocess.Popen([str(executable)], close_fds=True)
                return True
    elif platform.system() == 'Darwin':
        for app in (Path('/Applications/Docker.app'), Path.home() / 'Applications/Docker.app'):
            if app.is_dir():
                subprocess.run(['/usr/bin/open', str(app)], check=True, capture_output=True)
                return True
    return False

def install_desktop(progress):
    arch = 'arm64' if platform.machine().lower() in ('arm64', 'aarch64') else 'amd64'
    entry = RUNTIMES.get((platform.system(), arch))
    if entry is None:
        raise ValueError('此平台请先安装可用的 Docker 运行环境')
    progress('正在下载并校验运行环境；随后请完成官方安装和首次启动页面…')
    with tempfile.TemporaryDirectory(prefix='crb-document-runtime-') as temporary:
        package = Path(temporary) / ('DockerInstaller.exe' if os.name == 'nt' else 'Docker.dmg')
        digest = hashlib.sha256()
        size = 0
        with urllib.request.urlopen('https://desktop.docker.com/' + entry[0], timeout=60) as source, package.open('wb') as output:
            while True:
                block = source.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > 2 * 1024 ** 3:
                    raise ValueError('运行环境下载超过上限')
                digest.update(block)
                output.write(block)
        if digest.hexdigest() != entry[1]:
            raise ValueError('运行环境安装包校验失败，未执行')
        if os.name == 'nt':
            result = subprocess.run([str(package), 'install', '--user'], timeout=1800)
            if result.returncode not in (0, 3010):
                raise ValueError('运行环境安装已取消或未完成，可稍后重试')
        else:
            mount = Path(temporary) / 'mount'
            mount.mkdir()
            subprocess.run(['/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse', '-mountpoint', str(mount), str(package)], check=True, capture_output=True, timeout=60)
            try:
                destination = Path.home() / 'Applications/Docker.app'
                if destination.exists():
                    raise ValueError('已有 Docker 应用，请启动它后重试')
                destination.parent.mkdir(exist_ok=True)
                with tempfile.TemporaryDirectory(prefix='.crb-document-runtime-', dir=destination.parent) as staging:
                    candidate = Path(staging) / 'Docker.app'
                    subprocess.run(['/usr/bin/ditto', str(mount / 'Docker.app'), str(candidate)], check=True, capture_output=True, timeout=300)
                    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(candidate)], check=True, capture_output=True, timeout=60)
                    os.setxattr(candidate, 'com.apple.quarantine', ('0081;' + format(int(time.time()), 'x') + ';CodeRelayBridge;' + uuid.uuid4().hex).encode())
                    if destination.exists():
                        raise ValueError('已有 Docker 应用，请启动它后重试')
                    os.rename(candidate, destination)
            finally:
                subprocess.run(['/usr/bin/hdiutil', 'detach', str(mount)], check=True, capture_output=True, timeout=60)
    start_desktop()

def authorize(action, data):
    if not HELPER.is_file():
        raise ValueError('请先使用新版服务端 DEB 安装器安装系统文档管理组件')
    command = ['/usr/bin/pkexec', '/usr/bin/python3', '-I', str(HELPER), action, data['owner']]
    if action == 'install':
        command += ['--uid', str(os.getuid())]
    result = subprocess.run(command, input=json.dumps({'secret': data['secret']}).encode(), capture_output=True, timeout=2400)
    if result.returncode:
        raise ValueError('系统安装未完成或授权已取消；可点击“安装并启用”重试')

def assert_idle(config):
    directory = Path(config['state_dir']) / 'document-editing'
    for path in directory.glob('*/session.json'):
        row = json.loads(path.read_text())
        if not row.get('closed') and (not row.get('finalCallback')) and (not (row.get('closeRequested') and row.get('state') in ('failed', 'conflict'))) and (not (not row.get('engineTouched') and row.get('state') == 'editing' and (time.time() - row['created'] > 900))):
            raise ValueError('仍有文档会话未结束，请先关闭编辑器并确认回存结果')

class Setup:

    def __init__(self, config, progress=lambda text: None):
        self.config = config
        self.progress = progress

    def remember(self, data):
        private_json(state_path(self.config), data)

    def status(self):
        data = state(self.config, False)
        if data is None:
            return {'installed': False, 'running': False, 'healthy': False, 'mode': 'none'}
        if data['mode'] == 'pending':
            return {'installed': False, 'running': False, 'healthy': False, 'mode': 'pending'}
        try:
            if data['mode'] == 'system':
                result = socket_request(SOCKETS / (data['owner'] + '.sock'), {'action': 'status'}, timeout=15)
            else:
                resources = EngineResources(data['owner'], docker_command(data))
                result = {**resources.status(), 'healthy': resources.health()}
            return {key: result.get(key, False) for key in ('installed', 'running', 'healthy')} | {'mode': data['mode']}
        except (OSError, ValueError, subprocess.SubprocessError):
            return {'installed': True, 'running': False, 'healthy': False, 'mode': data['mode'], 'unavailable': True}

    def install(self):
        from crb.server.office import settings_lock
        with settings_lock(dict(self.config, state_dir=str(Path(self.config['state_dir']) / 'managed-document-setup'))):
            return self._install()

    def _install(self):
        from crb.server.office import save_settings, settings_lock
        with settings_lock(self.config):
            assert_idle(self.config)
        data = state(self.config, False) or {'owner': uuid.uuid4().hex, 'secret': secrets.token_urlsafe(48), 'image': IMAGE, 'mode': 'pending'}
        self.remember(data)
        if data['mode'] == 'system':
            authorize('install', data)
        else:
            detected = runtime(data if data['mode'] == 'direct' else None)
            if detected is None and platform.system() == 'Linux':
                self.progress('请在系统授权窗口确认安装文档运行环境…')
                data['systemAttempted'] = True
                self.remember(data)
                authorize('install', data)
                data['mode'] = 'system'
                self.remember(data)
            else:
                if detected is None:
                    if not start_desktop():
                        install_desktop(self.progress)
                    self.progress('等待运行环境就绪；请完成官方首次启动页面，必要时按系统提示重启后再点安装…')
                    until = time.monotonic() + 180
                    while time.monotonic() < until:
                        detected = runtime()
                        if detected:
                            break
                        time.sleep(2)
                    if detected is None:
                        raise ValueError('运行环境尚未就绪；完成首次启动或系统重启后，点击“安装并启用”继续')
                data.update(detected, mode='direct')
                self.remember(data)
                resources = EngineResources(data['owner'], docker_command(data), self.progress)
                if 'imageOwned' not in data:
                    data['imageOwned'] = not resources.image_present()
                    self.remember(data)
                resources.install(data['secret'])
        self.progress('正在等待文档引擎启动…')
        until = time.monotonic() + 300
        while time.monotonic() < until:
            if self.status().get('healthy'):
                break
            time.sleep(2)
        else:
            raise ValueError('组件已安装，但启动尚未完成；稍后点击“安装并启用”继续检查')
        data['paused'] = False
        self.remember(data)
        save_settings(self.config, True, '', '', '', data['secret'], managed_owner=data['owner'])
        return '文档编辑已就绪，所有连接此服务端的客户端共用，无需逐台安装。'

    def action(self, action):
        from crb.server.office import settings_lock, settings_path, atomic_json
        if action not in ('start', 'stop', 'remove'):
            raise ValueError('文档组件操作无效')
        data = state(self.config)
        with settings_lock(self.config):
            if action != 'start':
                assert_idle(self.config)
            if data['mode'] == 'system':
                if action == 'remove':
                    authorize('remove', data)
                else:
                    socket_request(SOCKETS / (data['owner'] + '.sock'), {'action': action}, timeout=60)
            elif data['mode'] == 'direct':
                resources = EngineResources(data['owner'], docker_command(data))
                getattr(resources, action)()
                if action == 'remove':
                    resources.remove_owned_image(data.get('imageOwned') is True)
            elif action == 'remove' and data.get('systemAttempted'):
                authorize('remove', data)
            elif action != 'remove':
                raise ValueError('请先完成文档组件安装')
            if action in ('start', 'stop'):
                data['paused'] = action == 'stop'
                self.remember(data)
            if action == 'remove':
                path = settings_path(self.config)
                if path.exists():
                    config = json.loads(path.read_text())
                    if config.get('managed_owner') == data['owner']:
                        atomic_json(path, {'enabled': False})
                state_path(self.config).unlink()
        return {'start': '文档引擎已启动。', 'stop': '文档引擎已停止；文档回存副本保留。', 'remove': '文档组件及其专属数据卷已移除；已有用户文档和回存副本保留。'}[action]

class Broker:

    def __init__(self, reader, writer, close):
        self.reader = reader
        self.writer = writer
        self._close = close

    def close(self):
        self._close()

def open_broker(config, owner):
    data = state(config)
    if data.get('paused'):
        raise ValueError('文档组件已停止，请在服务端启动')
    if data['owner'] != identity(owner):
        raise ValueError('文档通道身份不匹配')
    if data['mode'] == 'system':
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(30)
        try:
            connection.connect(str(SOCKETS / (owner + '.sock')))
            connection.sendall(b'{"action":"tunnel"}\n')
            reader = connection.makefile('rb', buffering=0)
            writer = connection.makefile('wb', buffering=0)
            response = json.loads(reader.readline(4097))
            if not response.get('ok'):
                raise ValueError('文档通道正在使用或尚未就绪')
            connection.settimeout(None)
        except BaseException:
            connection.close()
            raise

        def close():
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            reader.close()
            writer.close()
            connection.close()
        return Broker(reader, writer, close)
    if data['mode'] != 'direct':
        raise ValueError('请先完成文档组件安装')
    command = docker_command(data)
    EngineResources(owner, command).start()
    process = subprocess.Popen(command.prefix + ['exec', '-i', 'crb-docs-' + owner, 'python3', '-u', '-c', WIRE_SOURCE + '\nserve_container()\n'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def close():
        try:
            process.stdin.close()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
            process.wait(timeout=3)
        finally:
            process.stdout.close()
    return Broker(process.stdout, process.stdin, close)
