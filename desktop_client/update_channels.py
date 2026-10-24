"""Explicit, signed GitHub/Gitee update discovery with bounded mirror probes."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import http.client
from pathlib import Path
import time
from desktop_client import VERSION
from desktop_client.relay_updates import GitHubHTTP, ReleaseUpdater, UpdateError, UpdateTransportError, UpdateCancelled, MANIFEST_NAME, repository, platform_id, version_tuple, verify_manifest
CHANNELS = ('auto', 'github', 'gitee')
PROBE_BYTES = 256 * 1024
PROBE_SECONDS = 3

class ChannelSettings:

    def __init__(self, role='client', directory=None):
        from desktop_client.platform_support import data_root
        self.path = Path(directory or data_root('CodexRelayClient' if role == 'client' else 'CodexRelayServer')) / 'update-preferences.json'

    def load(self):
        value = self.read().get('channel')
        return value if value in CHANNELS else 'auto'

    def read(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self, channel):
        if channel not in CHANNELS:
            raise UpdateError('更新通道无效。')
        from desktop_client.connections import atomic_json
        data = self.read()
        data['channel'] = channel
        atomic_json(self.path, data)

    def skip_version(self, repository, version):
        version_tuple(version)
        from desktop_client.connections import atomic_json
        data = self.read()
        data['skippedVersion'] = {'repository': repository, 'version': version}
        atomic_json(self.path, data)

    def is_skipped(self, repository, version):
        saved = self.read().get('skippedVersion')
        if not isinstance(saved, dict) or saved.get('repository') != repository:
            return False
        try:
            return version_tuple(saved.get('version')) == version_tuple(version)
        except UpdateError:
            return False

def check_gitee(updater):
    """Gitee mirrors the SAME signed repository identity, never a renamed payload."""
    owner, repo = updater.repo.split('/')
    base = 'https://gitee.com/api/v5/repos/' + owner.lower() + '/' + repo
    releases = updater.http.json(base + '/releases?per_page=20&direction=desc')
    if not isinstance(releases, list) or len(releases) > 20:
        raise UpdateError('通道返回了无效的更新元数据。')
    stable = []
    for release in releases:
        if not isinstance(release, dict) or release.get('draft') or release.get('prerelease'):
            continue
        try:
            key = version_tuple(release.get('tag_name'))
        except UpdateError:
            continue
        stable.append((key, release))
    if not stable:
        raise UpdateTransportError('仓库尚无正式 Release，或当前账号没有私有仓库读取权限。')
    for version, release in sorted(stable, key=lambda row: row[0], reverse=True):
        if version <= version_tuple(updater.current):
            return {'available': False, 'current': updater.current, 'latest': updater.current, 'repository': updater.repo, 'channel': 'gitee'}
        ident = release.get('id')
        if type(ident) is not int or ident <= 0:
            raise UpdateError('通道返回了无效的更新元数据。')
        assets_base = base + '/releases/' + str(ident) + '/attach_files'
        assets = updater.http.json(assets_base + '?per_page=100')
        if not isinstance(assets, list) or len(assets) > 100:
            raise UpdateError('通道返回了无效的更新元数据。')

        def asset_url(asset):
            value = asset.get('id')
            if type(value) is not int or value <= 0:
                raise UpdateError('安装包附件来自不匹配的仓库。')
            return assets_base + '/' + str(value) + '/download'
        manifests = [a for a in assets if isinstance(a, dict) and a.get('name') == MANIFEST_NAME]
        if len(manifests) != 1:
            raise UpdateError('正式 Release 缺少唯一的签名更新清单。')
        envelope = updater.http.json(asset_url(manifests[0]), binary=True)
        payload = verify_manifest(envelope, updater.repo)
        if version_tuple(payload['version']) != version:
            raise UpdateError('更新清单版本与 Release 不一致。')
        candidates = [p for p in payload['packages'] if p['role'] == updater.role and p['edition'] == updater.edition and (p['platform'] == platform_id())]
        if not candidates:
            continue
        if len(candidates) != 1:
            raise UpdateError('Release 缺少当前系统与版本类型的唯一原生包。')
        package = candidates[0]
        selected = [a for a in assets if isinstance(a, dict) and a.get('name') == package['file']]
        if len(selected) != 1 or selected[0].get('size') != package['bytes']:
            raise UpdateError('安装包附件与签名清单不一致。')
        from desktop_client.relay_updates import validate_macos_version
        validate_macos_version(package)
        return {'available': True, 'current': updater.current, 'latest': payload['version'], 'repository': updater.repo, 'package': package, 'assetUrl': asset_url(selected[0]), 'manifest': envelope, 'channel': 'gitee'}
    return {'available': False, 'current': updater.current, 'latest': updater.current, 'repository': updater.repo, 'channel': 'gitee'}

def measure(http, url, *, cancelled=None, clock=time.monotonic):
    """At most 256 KiB read per eligible channel, only on explicit new-version check."""
    began = clock()
    total = 0
    try:
        with http.open(url, binary=True, byte_range=PROBE_BYTES, timeout=2) as response:
            read = getattr(response, 'read1', response.read)
            while total < PROBE_BYTES and clock() - began < PROBE_SECONDS:
                if cancelled and cancelled():
                    raise UpdateCancelled('更新已取消，原程序未改变。')
                block = read(min(32 * 1024, PROBE_BYTES - total))
                if not block:
                    break
                total += len(block)
    except UpdateCancelled:
        raise
    except UpdateError as error:
        if not isinstance(error, UpdateTransportError):
            raise
    except (OSError, EOFError, http.client.HTTPException):
        pass
    elapsed = max(clock() - began, 0.001)
    return {'bytes': total, 'seconds': elapsed, 'bytesPerSecond': total / elapsed if total else 0}

class ChannelUpdater:

    def __init__(self, role='client', client_edition=None, current_version=VERSION, sources=None):
        from desktop_client.edition import edition
        self.role = role
        self.edition = client_edition or edition()
        self.repo = repository(role, self.edition)
        self.current = current_version
        self.requires_auth = self.edition != 'standard'
        self.tokens = {}
        self.sources = sources
        self.checked = {}

    def source(self, channel):
        if self.sources is not None:
            return self.sources[channel]
        http = GitHubHTTP(self.tokens.get(channel, ''), timeout=8, channel=channel)
        return ReleaseUpdater(self.role, self.edition, http, self.current, channel=channel)

    def check(self, channel='auto', *, cancelled=None, probe=True):
        if channel not in CHANNELS:
            raise UpdateError('更新通道无效。')
        self.checked = {}
        requested = ('github', 'gitee') if channel == 'auto' else (channel,)
        results = {}
        errors = {}

        def discovery(provider):
            if self.requires_auth and self.sources is None and (not self.tokens.get(provider)):
                raise UpdateTransportError(('GitHub' if provider == 'github' else 'Gitee') + ' 缺少仓库读取授权。')
            source = self.source(provider)
            result = source.check()
            result['channel'] = provider
            return (source, result)
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = {pool.submit(discovery, provider): provider for provider in requested}
            for future in as_completed(jobs):
                provider = jobs[future]
                try:
                    source, result = future.result()
                    self.checked[provider] = source
                    results[provider] = result
                except UpdateTransportError as error:
                    errors[provider] = str(error)
        if cancelled and cancelled():
            raise UpdateCancelled('更新已取消，原程序未改变。')
        if not results:
            raise UpdateError('\n'.join(errors.values()) or '没有可用的更新通道。')
        available = {key: value for key, value in results.items() if value.get('available')}
        if not available:
            return {'available': False, 'current': self.current, 'latest': self.current, 'repository': self.repo, 'channel': channel, 'channelErrors': errors, 'measurements': {}}
        newest = max((version_tuple(value['latest']) for value in available.values()))
        eligible = {key: value for key, value in available.items() if version_tuple(value['latest']) == newest}
        first = next(iter(eligible.values()))['package']
        if any((value['package'] != first for value in eligible.values())):
            raise UpdateError('不同通道的同版本安装包不一致，拒绝自动更新。')
        measurements = {}
        if probe and channel == 'auto' and (len(eligible) > 1):
            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs = {pool.submit(measure, self.checked[key].http, value['assetUrl'], cancelled=cancelled): key for key, value in eligible.items()}
                for future in as_completed(jobs):
                    measurements[jobs[future]] = future.result()
        winner = max(eligible, key=lambda key: (measurements.get(key, {}).get('bytesPerSecond', 0), key == 'github'))
        result = dict(eligible[winner], automatic=channel == 'auto', measurements=measurements, channelErrors=errors, alternatives=[value for key, value in eligible.items() if key != winner])
        return result

    def prepare(self, release, progress=None, *, cancelled=None, status=None):
        choices = [release] + (release.get('alternatives', []) if release.get('automatic') else [])
        failure = None
        for choice in choices:
            provider = choice.get('channel', 'github')
            source = self.checked.get(provider)
            if source is None:
                raise UpdateError('请先重新检查更新。')
            if choice['package'] != release['package']:
                raise UpdateError('不同通道的同版本安装包不一致，拒绝自动更新。')
            if cancelled and cancelled():
                raise UpdateCancelled('更新已取消，原程序未改变。')
            if status:
                status('channel:' + provider)
            try:
                return source.prepare(choice, progress, cancelled=cancelled, status=status)
            except UpdateTransportError as error:
                failure = error
        raise failure or UpdateError('没有可用的更新通道。')
