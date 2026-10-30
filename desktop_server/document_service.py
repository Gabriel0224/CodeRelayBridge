"""Owned document-container lifecycle and the Linux native-authorized agent.

This module is also installed as a root-owned standalone helper by the server
DEB. Its local socket grants control over one fixed CRB engine, never a Docker
socket, arbitrary command, host mount or network configuration.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
IMAGE = 'onlyoffice/documentserver@sha256:ca17889f10b11141cc6441836aebdd2835cf3e1b49681b2fd6e155c94b7850b3'
LABEL = 'io.coderelaybridge.document-owner'
PARTS = {'logs': '/var/log/onlyoffice', 'lib': '/var/lib/onlyoffice', 'data': '/var/www/onlyoffice/Data', 'fonts': '/usr/share/fonts/truetype/custom'}
HELPER = Path('/opt/coderelaybridge/server/office-runtime/service.py')
MANIFESTS = Path('/etc/coderelaybridge-office')
SOCKETS = Path('/run/coderelaybridge-office')

def identity(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{32}', value):
        raise ValueError('文档引擎身份无效')
    return value

def private_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=448)
    fd, name = tempfile.mkstemp(prefix='.engine-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(data, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)

class EngineResources:

    def __init__(self, owner, command, progress=lambda text: None):
        self.owner = identity(owner)
        self.command = command
        self.progress = progress
        self.name = 'crb-docs-' + owner

    def run(self, *args, timeout=30, check=True):
        result = self.command(list(args), timeout=timeout)
        if check and result.returncode:
            raise ValueError('文档引擎操作失败，请检查运行环境和可用空间')
        return result

    def inspect(self):
        result = self.run('inspect', self.name, check=False)
        if result.returncode:
            return None
        rows = json.loads(result.stdout)
        if not isinstance(rows, list) or len(rows) != 1:
            raise ValueError('文档引擎登记异常')
        row = rows[0]
        host = row['HostConfig']
        expected = {self.name + '-' + part: destination for part, destination in PARTS.items()}
        actual = {entry.get('Name'): entry.get('Destination') for entry in row.get('Mounts', [])}
        if row['Config'].get('Labels', {}).get(LABEL) != self.owner or row['Config'].get('Image') != IMAGE or host.get('NetworkMode') != 'none' or host.get('Privileged') or (host.get('PidMode') not in ('', None)) or host.get('CapAdd') or host.get('Devices') or (actual != expected) or any((entry.get('Type') != 'volume' for entry in row.get('Mounts', []))):
            raise ValueError('同名组件身份或隔离配置不匹配，未修改')
        return row

    def volume(self, part):
        name = self.name + '-' + part
        result = self.run('volume', 'inspect', name, check=False)
        if result.returncode:
            self.run('volume', 'create', '--label', LABEL + '=' + self.owner, name)
        else:
            row = json.loads(result.stdout)[0]
            if row.get('Labels', {}).get(LABEL) != self.owner or row.get('Driver') != 'local' or row.get('Options'):
                raise ValueError('文档数据卷身份不匹配，未修改')
        return name

    def install(self, secret):
        if not re.fullmatch('[A-Za-z0-9_-]{43,128}', secret):
            raise ValueError('文档引擎密钥格式无效')
        old = self.inspect()
        if old:
            values = old['Config'].get('Env', [])
            if 'JWT_SECRET=' + secret not in values:
                raise ValueError('现有引擎密钥不匹配，未替换')
            self.start()
            return
        self.progress('正在下载文档编辑组件…')
        self.run('pull', IMAGE, timeout=1800)
        self.progress('正在初始化文档编辑组件…')
        mounts = []
        for part, destination in PARTS.items():
            mounts += ['--mount', 'type=volume,source=' + self.volume(part) + ',target=' + destination]
        fd, name = tempfile.mkstemp(prefix='crb-engine-env-')
        try:
            with os.fdopen(fd, 'w', encoding='ascii') as output:
                output.write('JWT_ENABLED=true\nJWT_IN_BODY=true\nJWT_SECRET=' + secret + '\nALLOW_PRIVATE_IP_ADDRESS=true\nPLUGINS_ENABLED=false\n')
            self.run('create', '--name', self.name, '--label', LABEL + '=' + self.owner, '--network=none', '--restart=unless-stopped', '--memory=4g', '--cpus=2', '--pids-limit=512', '--shm-size=256m', '--security-opt=no-new-privileges', '--env-file', name, *mounts, IMAGE, timeout=120)
        finally:
            os.unlink(name)
        self.start()

    def start(self):
        row = self.inspect()
        if row is None:
            raise ValueError('文档编辑组件尚未安装')
        if not row.get('State', {}).get('Running'):
            self.run('start', self.name, timeout=60)

    def stop(self):
        row = self.inspect()
        if row and row.get('State', {}).get('Running'):
            self.run('stop', '--time', '30', self.name, timeout=45)

    def status(self):
        row = self.inspect()
        return {'installed': row is not None, 'running': bool(row and row.get('State', {}).get('Running')), 'healthy': bool(row and row.get('State', {}).get('Health', {}).get('Status') == 'healthy')}

    def health(self):
        row = self.inspect()
        if not row or not row.get('State', {}).get('Running'):
            return False
        code = "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1/healthcheck',timeout=5).read().strip()==b'true'"
        return self.run('exec', self.name, 'python3', '-c', code, timeout=10, check=False).returncode == 0

    def image_present(self):
        return self.run('image', 'inspect', IMAGE, check=False).returncode == 0

    def remove_owned_image(self, owned):
        if owned:
            used = self.run('ps', '-a', '--filter', 'ancestor=' + IMAGE, '--format', '{{.ID}}')
            if not used.stdout.strip():
                self.run('image', 'rm', IMAGE, check=False, timeout=60)

    def remove(self):
        row = self.inspect()
        if row:
            self.run('rm', '-f', self.name, timeout=60)
        for part in PARTS:
            name = self.name + '-' + part
            result = self.run('volume', 'inspect', name, check=False)
            if result.returncode:
                continue
            volume = json.loads(result.stdout)[0]
            if volume.get('Labels', {}).get(LABEL) != self.owner or volume.get('Driver') != 'local' or volume.get('Options'):
                raise ValueError('文档数据卷身份不匹配，未删除')
            self.run('volume', 'rm', name, timeout=60)

def socket_request(path, request, timeout=30):
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(timeout)
    try:
        connection.connect(str(path))
        connection.sendall(json.dumps(request).encode() + b'\n')
        stream = connection.makefile('rb')
        data = stream.readline(65537)
        if len(data) > 65536:
            raise ValueError('文档服务响应过长')
        result = json.loads(data)
        if not result.get('ok'):
            raise ValueError(result.get('error') or '文档服务未接受操作')
        return result
    finally:
        connection.close()

def root_command(args, timeout=30):
    environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'DOCKER_HOST': 'unix:///var/run/docker.sock', 'DOCKER_CONFIG': '/var/lib/coderelaybridge-office/docker-cli'}
    return subprocess.run(['/usr/bin/docker', *args], capture_output=True, timeout=timeout, env=environment)

def root_manifest(owner):
    path = MANIFESTS / (identity(owner) + '.json')
    info = path.lstat()
    if path.is_symlink() or info.st_uid != 0 or info.st_mode & 63:
        raise ValueError('文档服务登记权限无效')
    data = json.loads(path.read_text())
    if data['owner'] != owner or type(data.get('uid')) is not int:
        raise ValueError('文档服务登记身份无效')
    return data

def serve(owner, manifest=None, socket_path=None, command=root_command, wire_source=None):
    data = manifest or root_manifest(owner)
    resources = EngineResources(owner, command)
    resources.inspect()
    target = Path(socket_path) if socket_path else SOCKETS / (owner + '.sock')
    target.parent.mkdir(parents=True, exist_ok=True, mode=493)
    if target.is_symlink():
        raise ValueError('文档服务套接字无效')
    if target.exists():
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.connect(str(target))
        except ConnectionRefusedError:
            if not stat.S_ISSOCK(target.lstat().st_mode):
                raise ValueError('文档服务套接字无效')
            target.unlink()
        else:
            raise ValueError('文档服务已运行')
        finally:
            probe.close()
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(target))
    os.chmod(target, 384)
    os.chown(target, data['uid'], -1)
    listener.listen(16)
    tunnel_guard = threading.Lock()
    if wire_source is None:
        path = HELPER.with_name('office-wire.txt')
        info = path.lstat()
        if path.is_symlink() or info.st_uid != 0 or info.st_mode & 18:
            raise ValueError('文档通道程序身份无效')
        wire_source = path.read_text()

    def handle(connection):
        connection.settimeout(10)
        acquired = False
        child = None
        try:
            _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid not in (0, data['uid']):
                raise ValueError('文档服务身份不匹配')
            stream = connection.makefile('rb', buffering=0)
            raw = stream.readline(4097)
            if len(raw) > 4096:
                raise ValueError('文档服务请求过长')
            request = json.loads(raw)
            action = request.get('action')
            if action == 'tunnel':
                acquired = tunnel_guard.acquire(blocking=False)
                if not acquired:
                    raise ValueError('文档通道已被服务端使用')
                resources.start()
                connection.settimeout(None)
                child = subprocess.Popen(['/usr/bin/docker', 'exec', '-i', resources.name, 'python3', '-u', '-c', wire_source + '\nserve_container()\n'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0, env={'PATH': '/usr/bin:/bin', 'DOCKER_HOST': 'unix:///var/run/docker.sock', 'DOCKER_CONFIG': '/var/lib/coderelaybridge-office/docker-cli'})
                connection.sendall(b'{"ok":true}\n')

                def output():
                    try:
                        while True:
                            block = child.stdout.read(65536)
                            if not block:
                                break
                            connection.sendall(block)
                    except OSError:
                        pass
                    finally:
                        try:
                            connection.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                threading.Thread(target=output, daemon=True).start()
                while True:
                    block = stream.read(65536)
                    if not block:
                        break
                    block = memoryview(block)
                    while block:
                        written = child.stdin.write(block)
                        if not written:
                            raise OSError('Document tunnel closed')
                        block = block[written:]
                return
            if action == 'status':
                result = {**resources.status(), 'healthy': resources.health()}
            elif action in ('start', 'stop'):
                getattr(resources, action)()
                result = resources.status()
            else:
                raise ValueError('不支持的文档服务操作')
            connection.sendall(json.dumps({'ok': True, **result}).encode() + b'\n')
        except Exception:
            if child is None:
                try:
                    connection.sendall(b'{"ok":false,"error":"Document engine operation failed"}\n')
                except OSError:
                    pass
        finally:
            if child:
                try:
                    child.stdin.close()
                    child.wait(timeout=3)
                except (OSError, subprocess.TimeoutExpired):
                    child.kill()
                    child.wait(timeout=3)
            if acquired:
                tunnel_guard.release()
            connection.close()
    try:
        while True:
            connection, _ = listener.accept()
            threading.Thread(target=handle, args=(connection,), daemon=True).start()
    finally:
        listener.close()
        target.unlink(missing_ok=True)

def unit_text(owner):
    identity(owner)
    return '[Unit]\nDescription=CodeRelayBridge document engine agent\nAfter=docker.service\n[Service]\nType=simple\nExecStart=/usr/bin/python3 -I ' + str(HELPER) + ' serve ' + owner + '\nRestart=on-failure\nRestartSec=3\nNoNewPrivileges=true\nPrivateTmp=true\nProtectHome=true\n[Install]\nWantedBy=multi-user.target\n'

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('install', 'serve', 'remove'))
    parser.add_argument('owner')
    parser.add_argument('--uid', type=int)
    args = parser.parse_args()
    identity(args.owner)
    if os.name != 'posix' or os.geteuid() != 0:
        raise ValueError('此操作需要系统原生授权')
    if Path(__file__).resolve() != HELPER or HELPER.is_symlink() or HELPER.stat().st_uid != 0 or HELPER.stat().st_mode & 18:
        raise ValueError('请使用系统安装包提供的文档组件管理程序')
    for parent in (HELPER.parent, MANIFESTS, SOCKETS):
        if parent.exists() and (parent.is_symlink() or parent.stat().st_uid != 0 or parent.stat().st_mode & 18):
            raise ValueError('系统文档目录权限无效')
    if args.action == 'serve':
        return serve(args.owner)
    unit = Path('/etc/systemd/system') / ('coderelaybridge-office-' + args.owner + '.service')
    if args.action == 'install':
        import pwd
        if args.uid is None or args.uid < 1:
            raise ValueError('文档服务用户无效')
        pwd.getpwuid(args.uid)
        incoming = json.loads(sys.stdin.buffer.read(4097))
        secret = incoming.get('secret', '')
        if not re.fullmatch('[A-Za-z0-9_-]{43,128}', secret):
            raise ValueError('文档引擎密钥格式无效')
        if not Path('/usr/bin/docker').is_file():
            if not Path('/usr/bin/apt-get').is_file():
                raise ValueError('此系统尚不支持自动安装运行环境')
            with tempfile.TemporaryDirectory(prefix='crb-runtime-packages-') as cache:
                subprocess.run(['/usr/bin/apt-get', 'update'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
                subprocess.run(['/usr/bin/apt-get', '-y', '-o', 'Dir::Cache::archives=' + cache, 'install', '--no-install-recommends', 'docker.io'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1200)
            subprocess.run(['systemctl', 'enable', '--now', 'docker.service'], check=True, capture_output=True, timeout=60)
        subprocess.run(['systemctl', 'start', 'docker.service'], check=True, capture_output=True, timeout=60)
        path = MANIFESTS / (args.owner + '.json')
        if path.exists():
            previous = root_manifest(args.owner)
            if previous['uid'] != args.uid or previous['secret'] != secret:
                raise ValueError('文档服务登记不匹配，未修改')
        else:
            private_json(path, {'owner': args.owner, 'uid': args.uid, 'secret': secret, 'imageOwned': not EngineResources(args.owner, root_command).image_present()})
        resources = EngineResources(args.owner, root_command)
        resources.install(secret)
        if unit.is_symlink() or (unit.exists() and unit.read_text() != unit_text(args.owner)):
            raise ValueError('文档服务启动项无效')
        unit.write_text(unit_text(args.owner))
        unit.chmod(420)
        subprocess.run(['systemctl', 'daemon-reload'], check=True, capture_output=True, timeout=30)
        subprocess.run(['systemctl', 'enable', '--now', unit.name], check=True, capture_output=True, timeout=60)
        print(json.dumps({'ok': True, 'mode': 'system', 'owner': args.owner}), flush=True)
    else:
        path = MANIFESTS / (args.owner + '.json')
        if not path.exists():
            print('{"ok":true}', flush=True)
            return
        data = root_manifest(args.owner)
        if unit.exists() and (unit.is_symlink() or unit.read_text() != unit_text(args.owner)):
            raise ValueError('文档服务启动项身份不匹配')
        subprocess.run(['systemctl', 'disable', '--now', unit.name], capture_output=True, timeout=60)
        resources = EngineResources(args.owner, root_command)
        resources.remove()
        resources.remove_owned_image(data.get('imageOwned') is True)
        unit.unlink(missing_ok=True)
        (MANIFESTS / (args.owner + '.json')).unlink()
        subprocess.run(['systemctl', 'daemon-reload'], check=True, capture_output=True, timeout=30)
        print('{"ok":true}', flush=True)
if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('{"ok":false,"error":"Document component setup failed; existing user data was retained"}', file=sys.stderr)
        raise SystemExit(1)
