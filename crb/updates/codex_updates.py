"""Shared Codex-only updates; no optional account manager dependencies."""
import gzip
import io
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.request import Request, urlopen
OFFICIAL_CODEX_REPO = 'https://persistent.oaistatic.com/codex-app-prod/linux/deb'

def _version(value):
    match = re.match('^(\\d+)\\.(\\d+)\\.(\\d+)', value or '')
    return tuple(map(int, match.groups())) if match else None

def _run(argv, timeout=30, input_text=None, env=None):
    env = dict(os.environ, LC_ALL='C') if env is None else dict(env, LC_ALL='C')
    return subprocess.run(argv, input=input_text, text=True, capture_output=True, timeout=timeout, check=False, env=env, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

def _installed(package):
    result = _run(['dpkg-query', '-W', '-f=${Version}', package])
    return result.stdout.strip() if result.returncode == 0 else ''

def _candidate(package):
    result = _run(['apt-cache', 'policy', package])
    for line in result.stdout.splitlines():
        if line.strip().startswith('Candidate:'):
            value = line.split(':', 1)[1].strip()
            return '' if value == '(none)' else value
    return ''

def _official_codex_latest():
    source = Path('/etc/apt/sources.list.d/chatgpt.sources')
    try:
        declaration = source.read_text(encoding='utf-8')
    except OSError:
        return ''
    if f'URIs: {OFFICIAL_CODEX_REPO}' not in declaration:
        return ''
    import platform
    arch = {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(platform.machine())
    if not arch:
        return ''
    url = f'{OFFICIAL_CODEX_REPO}/dists/stable/main/binary-{arch}/Packages.gz'
    request = Request(url, headers={'User-Agent': 'codex-desktop-relay-updater/1'})
    with urlopen(request, timeout=15) as stream:
        raw = stream.read(128 * 1024 + 1)
    if len(raw) > 128 * 1024:
        raise RuntimeError('官方软件源索引过大')
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
        catalog = compressed.read(1024 * 1024 + 1)
    if len(catalog) > 1024 * 1024:
        raise RuntimeError('官方软件源索引解压后过大')
    versions = []
    for paragraph in catalog.decode('utf-8').split('\n\n'):
        fields = dict((line.split(':', 1) for line in paragraph.splitlines() if ':' in line))
        if fields.get('Package', '').strip() == 'chatgpt':
            value = fields.get('Version', '').strip()
            if _version(value):
                versions.append(value)
    return max(versions, key=_version) if versions else ''

def _sudo(argv, password, timeout=180):
    if not isinstance(password, str) or len(password) > 256:
        raise ValueError('授权密码格式错误')
    env = dict(os.environ, DEBIAN_FRONTEND='noninteractive')
    result = _run(['sudo', '-S', '-p', '', '--', *argv], timeout=timeout, input_text=password + '\n', env=env)
    if result.returncode:
        if 'incorrect password' in result.stderr.lower() or '密码' in result.stderr:
            raise RuntimeError('系统授权失败：密码不正确')
        raise RuntimeError(f'安装命令失败（退出码 {result.returncode}）')

def check(installed=None, candidate=None, latest=None):
    if not sys.platform.startswith('linux'):
        return {'ok': True, 'supported': False, 'error': '此设备的 Codex App 使用内置更新器；尚无安全的远程无界面安装接口'}
    installed_fn, candidate_fn, latest_fn = (installed or _installed, candidate or _candidate, latest or _official_codex_latest)
    before, target = (installed_fn('chatgpt'), candidate_fn('chatgpt'))
    source = 'cache'
    try:
        online = latest_fn()
        if online:
            target, source = (online, 'official-online')
    except (OSError, RuntimeError, TimeoutError, ValueError):
        pass
    return {'ok': True, 'supported': bool(before and target), 'installed': before, 'latest': target, 'updateAvailable': bool(_version(before) and _version(target) and (_version(before) < _version(target))), 'package': 'codex', 'source': source, 'requiresPassword': True}

def install(password, installed=None, candidate=None, sudo=None):
    if not sys.platform.startswith('linux'):
        raise RuntimeError('此设备的 Codex App 暂无安全的远程无界面安装接口')
    installed_fn, candidate_fn, sudo_fn = (installed or _installed, candidate or _candidate, sudo or _sudo)
    before = installed_fn('chatgpt')
    if not before:
        raise RuntimeError('目标机没有安装官方 chatgpt 软件包')
    sudo_fn(['apt-get', 'update'], password, timeout=240)
    target = candidate_fn('chatgpt')
    if not target:
        raise RuntimeError('官方软件源没有可用的 chatgpt 版本')
    if target == before:
        return {'ok': True, 'changed': False, 'version': before}
    sudo_fn(['apt-get', 'install', '--only-upgrade', '-y', 'chatgpt'], password, timeout=420)
    after = installed_fn('chatgpt')
    if after != target:
        raise RuntimeError('安装结束后版本与软件源候选版本不一致')
    return {'ok': True, 'changed': True, 'version': after}
