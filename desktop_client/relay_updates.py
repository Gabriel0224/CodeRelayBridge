"""Signed GitHub releases for both clients and the universal server.

No embedded credentials, TLS exceptions, shell downloads or silent installs.
"""
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from desktop_client import VERSION
from desktop_client.edition import edition
from desktop_client.platform_support import data_root
OWNER = 'Gabriel0224'
PUBLIC_REPO = 'CodeRelayBridge'
KEY_ID = 'crb-release-v1'
VERIFICATION_KEY = '2Jc8cJgnRJQEk9VP7X5IlGM25N0vG+Bvg/KAedY4cfw='
MANIFEST_NAME = 'crb-update-v3.json'
MAX_ARCHIVE = 2 * 1024 ** 3
MAX_EXPANDED = 4 * 1024 ** 3
DOWNLOAD_HOSTS = {'api.github.com', 'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}

class UpdateError(RuntimeError):
    pass

def canonical(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')

def version_tuple(value):
    match = re.fullmatch('v?(\\d+)\\.(\\d+)\\.(\\d+)', str(value))
    if not match:
        raise UpdateError('版本格式不受支持。')
    return tuple(map(int, match.groups()))

def platform_id():
    machine = platform.machine().lower()
    if sys.platform == 'win32' and machine in ('amd64', 'x86_64'):
        return 'Windows-x64'
    if sys.platform.startswith('linux') and machine in ('amd64', 'x86_64'):
        return 'Linux-x64'
    if sys.platform == 'darwin' and machine in ('arm64', 'aarch64'):
        return 'macOS-arm64'
    raise UpdateError('当前系统或处理器没有对应的原生更新包。')

def repository(role, client_edition):
    if role not in ('client', 'server') or client_edition != 'standard':
        raise UpdateError('程序版本类型无效。')
    return OWNER + '/' + PUBLIC_REPO

def validate_url(url, api_only=False):
    parsed = urllib.parse.urlsplit(url)
    allowed = {'api.github.com'} if api_only else DOWNLOAD_HOSTS
    if parsed.scheme != 'https' or parsed.hostname not in allowed or parsed.username or parsed.password or (parsed.port not in (None, 443)) or parsed.fragment:
        raise UpdateError('更新地址不属于受信任的 GitHub HTTPS 地址。')
    return parsed

class SafeRedirect(urllib.request.HTTPRedirectHandler):

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected:
            redirected.remove_header('Authorization')
        return redirected

class GitHubHTTP:

    def __init__(self, token='', timeout=15):
        self._token = token
        self.timeout = timeout
        self.opener = urllib.request.build_opener(SafeRedirect())

    def open(self, url, *, binary=False):
        parsed = validate_url(url)
        headers = {'Accept': 'application/octet-stream' if binary else 'application/vnd.github+json', 'User-Agent': 'CodeRelayBridge-Updater/' + VERSION}
        if self._token and parsed.hostname == 'api.github.com':
            headers['Authorization'] = 'Bearer ' + self._token
        try:
            return self.opener.open(urllib.request.Request(url, headers=headers), timeout=self.timeout)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise UpdateError('GitHub 授权无效、权限不足或请求额度受限；请检查访问令牌。') from None
            if error.code == 404:
                raise UpdateError('仓库尚无正式 Release，或当前账号没有私有仓库读取权限。') from None
            raise UpdateError('GitHub 请求失败（HTTP ' + str(error.code) + '）。') from None
        except UpdateError:
            raise
        except Exception as error:
            raise UpdateError('连接 GitHub 失败（' + type(error).__name__ + '）；原程序未改变。') from None

    def json(self, url, *, binary=False, limit=1024 * 1024):
        with self.open(url, binary=binary) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            raise UpdateError('更新元数据超过允许大小。')
        try:
            return json.loads(data)
        except (ValueError, UnicodeError):
            raise UpdateError('GitHub 返回了无效的更新元数据。') from None

class TokenStore:
    """Explicit platform backend; never fall back to a plaintext keyring."""
    service = 'CodeRelayBridge-GitHub-Updates'

    def backend(self):
        if sys.platform == 'win32':
            from keyring.backends.Windows import WinVaultKeyring
            return WinVaultKeyring()
        if sys.platform == 'darwin':
            from keyring.backends.macOS import Keyring
            return Keyring()
        from keyring.backends.SecretService import Keyring
        return Keyring()

    def get(self, repo):
        try:
            return self.backend().get_password(self.service, repo) or ''
        except Exception:
            raise UpdateError('系统密钥库不可用；可输入令牌仅供本次使用，不会明文保存。') from None

    def save(self, repo, token):
        try:
            backend = self.backend()
            if token:
                backend.set_password(self.service, repo, token)
            elif backend.get_password(self.service, repo):
                backend.delete_password(self.service, repo)
        except Exception:
            raise UpdateError('系统密钥库未能保存授权；没有写入明文令牌。') from None

def verify_manifest(envelope, expected_repo, verification_key=VERIFICATION_KEY):
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        payload, signature = (envelope['payload'], envelope['signature'])
        if signature['algorithm'] != 'ed25519' or signature['keyId'] != KEY_ID:
            raise ValueError()
        Ed25519PublicKey.from_public_bytes(base64.b64decode(verification_key, validate=True)).verify(base64.b64decode(signature['value'], validate=True), canonical(payload))
        if payload['schema'] != 1 or payload['project'] != 'CodeRelayBridge' or payload['repository'] != expected_repo:
            raise ValueError()
        version_tuple(payload['version'])
        if not isinstance(payload['packages'], list):
            raise ValueError()
        seen = set()
        for item in payload['packages']:
            name = item['file']
            if not isinstance(name, str) or not re.fullmatch('[A-Za-z0-9._-]+', name) or (not re.fullmatch('[0-9a-f]{64}', item['sha256'])) or (type(item['bytes']) is not int) or (not 0 < item['bytes'] <= MAX_ARCHIVE) or (item['version'] != payload['version']) or (name in seen) or (repository(item['role'], item['edition']) != expected_repo):
                raise ValueError()
            seen.add(name)
        return payload
    except Exception:
        raise UpdateError('更新清单签名或内容校验失败，拒绝安装。') from None

def verify_file(file, size, digest):
    file = Path(file)
    if file.is_symlink() or file.stat().st_size != size:
        raise UpdateError('更新包大小与签名清单不一致。')
    sha = hashlib.sha256()
    with file.open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(part)
    if sha.hexdigest() != digest:
        raise UpdateError('更新包 SHA-256 校验失败，原程序未改变。')

def safe_member(root, name):
    if not isinstance(name, str) or '\\' in name or '\x00' in name:
        raise UpdateError('更新包包含无效路径。')
    parts = PurePosixPath(name)
    if parts.is_absolute() or '..' in parts.parts or ':' in name:
        raise UpdateError('更新包包含越界路径。')
    target = root / Path(*parts.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise UpdateError('更新包路径越界。')
    return target

def extract_archive(archive, root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    if str(archive).endswith('.zip'):
        with zipfile.ZipFile(archive) as bundle:
            rows = bundle.infolist()
            if len(rows) > 20000 or sum((row.file_size for row in rows)) > MAX_EXPANDED:
                raise UpdateError('更新包解压大小超过限制。')
            seen = set()
            for row in rows:
                safe_member(root, row.orig_filename)
                target = safe_member(root, row.filename)
                if target in seen or row.external_attr >> 16 & 61440 == 40960:
                    raise UpdateError('ZIP 更新包含重复路径或符号链接。')
                seen.add(target)
            bundle.extractall(root)
    elif str(archive).endswith('.tar.gz'):
        with tarfile.open(archive, 'r:gz') as bundle:
            rows = bundle.getmembers()
            if len(rows) > 20000 or sum((row.size for row in rows)) > MAX_EXPANDED:
                raise UpdateError('更新包解压大小超过限制。')
            for row in rows:
                target = safe_member(root, row.name)
                if row.isdev() or row.isfifo() or row.islnk():
                    raise UpdateError('更新包包含不支持的特殊文件。')
                if row.issym():
                    safe_member(root, str(PurePosixPath(row.name).parent / row.linkname))
                    if not (target.parent / row.linkname).resolve().is_relative_to(root.resolve()):
                        raise UpdateError('更新包包含外部符号链接。')
            bundle.extractall(root, filter='data')
    else:
        raise UpdateError('当前系统不支持这种更新包格式。')

def product_name(role):
    return 'RelayClient' if role == 'client' else 'RelayServer'

def package_executable(root, role):
    product = product_name(role)
    return root / 'Contents/MacOS' / product if sys.platform == 'darwin' else root / (product + ('.exe' if sys.platform == 'win32' else ''))

def extract_package(archive, work, role):
    product = product_name(role)
    if str(archive).endswith('.deb'):
        if not sys.platform.startswith('linux'):
            raise UpdateError('当前系统不支持这种更新包格式。')
        from crb.common.deb_archive import extract_payload
        try:
            return extract_payload(archive, work / 'extracted' / product, role)
        except (OSError, ValueError, tarfile.TarError):
            raise UpdateError('更新包路径越界。') from None
    if str(archive).endswith('.exe'):
        if sys.platform != 'win32':
            raise UpdateError('当前系统不支持这种更新包格式。')
        root = work / 'extracted'
        if root.exists() or root.is_symlink():
            raise UpdateError('更新缓存目录身份异常。')
        result = subprocess.run([str(archive), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/CRBEXTRACT=' + str(root)], capture_output=True, timeout=180, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise UpdateError('安装包解压失败，原程序未改变。')
        validate_tree(root / product)
        return root / product
    if sys.platform == 'darwin':
        import plistlib
        app_name = 'Relay Client.app' if role == 'client' else 'Relay Server.app'
        proc = subprocess.run(['/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse', '-plist', str(archive)], capture_output=True, timeout=60, check=True)
        mounts = [Path(row['mount-point']) for row in plistlib.loads(proc.stdout)['system-entities'] if row.get('mount-point')]
        if len(mounts) != 1:
            raise UpdateError('无法确认更新磁盘映像的挂载点。')
        try:
            source = mounts[0] / app_name
            if source.is_symlink() or not source.is_dir():
                raise UpdateError('磁盘映像没有对应的原生程序。')
            target = work / app_name
            shutil.copytree(source, target, symlinks=True)
            validate_tree(target)
            return target
        finally:
            subprocess.run(['/usr/bin/hdiutil', 'detach', str(mounts[0])], capture_output=True, timeout=30, check=True)
    extract_archive(archive, work / 'extracted')
    return work / 'extracted' / product

def validate_tree(root):
    root = Path(root).resolve()
    for path in root.rglob('*'):
        if getattr(path, 'is_junction', lambda: False)():
            raise UpdateError('程序目录含不支持的目录联接。')
        if path.is_symlink() and (not path.resolve().is_relative_to(root)):
            raise UpdateError('程序目录含指向外部的符号链接。')

def validate_identity(root, role, client_edition):
    validate_tree(root)
    resources = root / ('Contents/Frameworks' if sys.platform == 'darwin' else '_internal')
    try:
        identity = json.loads((resources / 'relay-edition.json').read_text(encoding='utf-8'))
    except Exception:
        raise UpdateError('更新包缺少版本类型标识。') from None
    if identity != {'role': role, 'edition': client_edition} or not package_executable(root, role).is_file():
        raise UpdateError('更新包不属于当前程序或版本类型，拒绝替换。')

class ReleaseUpdater:

    def __init__(self, role='client', client_edition=None, http=None, current_version=VERSION):
        self.role = role
        self.edition = client_edition or edition()
        self.repo = repository(role, self.edition)
        self.current = current_version
        self.http = http or GitHubHTTP()

    def check(self):
        release = self.http.json('https://api.github.com/repos/' + self.repo + '/releases/latest')
        if release.get('draft') or release.get('prerelease'):
            raise UpdateError('不自动安装草稿或预发布版本。')
        if version_tuple(release.get('tag_name')) <= version_tuple(self.current):
            return {'available': False, 'current': self.current, 'latest': release['tag_name'], 'repository': self.repo}
        assets = release.get('assets', [])
        manifests = [row for row in assets if row.get('name') == MANIFEST_NAME]
        if len(manifests) != 1:
            raise UpdateError('正式 Release 缺少唯一的签名更新清单。')
        url = manifests[0]['url']
        validate_url(url, api_only=True)
        if not url.startswith('https://api.github.com/repos/' + self.repo + '/releases/assets/'):
            raise UpdateError('更新清单来自不匹配的仓库。')
        envelope = self.http.json(url, binary=True)
        payload = verify_manifest(envelope, self.repo)
        if version_tuple(payload['version']) != version_tuple(release['tag_name']):
            raise UpdateError('更新清单版本与 Release 不一致。')
        candidates = [row for row in payload['packages'] if row['role'] == self.role and row['edition'] == self.edition and (row['platform'] == platform_id())]
        if len(candidates) != 1:
            raise UpdateError('Release 缺少当前系统与版本类型的唯一原生包。')
        package = candidates[0]
        selected = [row for row in assets if row.get('name') == package['file']]
        if len(selected) != 1 or selected[0].get('size') != package['bytes']:
            raise UpdateError('安装包附件与签名清单不一致。')
        validate_url(selected[0]['url'], api_only=True)
        if not selected[0]['url'].startswith('https://api.github.com/repos/' + self.repo + '/releases/assets/'):
            raise UpdateError('安装包附件来自不匹配的仓库。')
        if sys.platform == 'darwin' and package.get('minimumMacOS'):
            installed = tuple(map(int, platform.mac_ver()[0].split('.')))
            required = tuple(map(int, package['minimumMacOS'].split('.')))
            if installed < required:
                raise UpdateError('此更新需要 macOS ' + package['minimumMacOS'] + ' 或更高版本。')
        return {'available': True, 'current': self.current, 'latest': payload['version'], 'repository': self.repo, 'package': package, 'assetUrl': selected[0]['url'], 'manifest': envelope}

    def prepare(self, release, progress=None):
        if not release.get('available'):
            raise UpdateError('没有可安装的新版本。')
        payload = verify_manifest(release['manifest'], self.repo)
        package = release['package']
        if package not in payload['packages'] or package['edition'] != self.edition or package['role'] != self.role or (package['platform'] != platform_id()) or (version_tuple(payload['version']) <= version_tuple(self.current)):
            raise UpdateError('更新包选择不匹配。')
        root = data_root('CodexRelayClient' if self.role == 'client' else 'CodexRelayServer')
        cache = root / 'updates'
        cache.mkdir(parents=True, exist_ok=True)
        if cache.is_symlink() or cache.resolve().parent != root.resolve():
            raise UpdateError('更新缓存目录身份异常。')
        if shutil.disk_usage(cache).free < package['bytes'] * 8 + 512 * 1024 ** 2:
            raise UpdateError('磁盘空间不足，原程序未改变。')
        work = cache / str(uuid.uuid4())
        work.mkdir(mode=448)
        archive = work / package['file']
        received = 0
        with self.http.open(release['assetUrl'], binary=True) as response, archive.open('xb') as out:
            while True:
                data = response.read(256 * 1024)
                if not data:
                    break
                received += len(data)
                if received > package['bytes']:
                    raise UpdateError('GitHub 下载大小超过签名清单限制。')
                out.write(data)
                if progress:
                    progress(received, package['bytes'])
        verify_file(archive, package['bytes'], package['sha256'])
        candidate = extract_package(archive, work, self.role)
        validate_identity(candidate, self.role, self.edition)
        return {'work': str(work), 'archive': str(archive), 'candidate': str(candidate), 'role': self.role, 'edition': self.edition, 'release': release}
