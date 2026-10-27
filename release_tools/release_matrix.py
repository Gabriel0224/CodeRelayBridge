"""Persistent, enforced edition policy shared by native packaging checks."""
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
LABELS = {'standard': '普通版', 'universal': '通用版'}
PLANS = [('client', 'RelayClient', 'standard'), ('server', 'RelayServer', 'standard')]
OPTIONAL_MODULES = set()

def policy():
    value = json.loads((ROOT / 'release_policy.json').read_text(encoding='utf-8'))
    return value

def package_name(product, version, edition, extension):
    return f'{product}-{version}-{edition.capitalize()}-{extension}'

def validate_installer_formats(records):
    formats = policy().get('installerFormats', {})
    for item in records:
        if formats and (not item['file'].endswith('.' + formats[item['platform']])):
            raise RuntimeError('Release must contain native installation formats, not archive substitutes')

def bridge_hashes(manifest, edition):
    if manifest['edition'] != edition or not manifest['function_bodies_unchanged']:
        raise RuntimeError('Wrong edition or modified shared bridge logic in native build')
    names = set(manifest['modules'])
    return {name: details.get('common_origin_sha256', details['source_sha256']) for name, details in manifest['modules'].items() if name not in OPTIONAL_MODULES}

def inspect_bundle(executable, edition, version):
    """Inspect actual compiled payload, not just its external metadata files."""
    from PyInstaller.archive.readers import CArchiveReader
    archive = CArchiveReader(str(executable))
    pyz = archive.open_embedded_archive('PYZ.pyz')
    identity = pyz.extract('desktop_client')
    if version not in identity.co_consts:
        raise RuntimeError('Native executable version differs from the release version')
    import tempfile
    import types
    backend = pyz.extract('desktop_client.backend')
    preference_class = next((c for c in backend.co_consts if isinstance(c, types.CodeType) and c.co_name == 'Preferences'))
    load = next((c for c in preference_class.co_consts if isinstance(c, types.CodeType) and c.co_name == 'load'))
    with tempfile.TemporaryDirectory(prefix='relay-default-address-audit-') as directory:
        value = types.FunctionType(load, {'json': json})(types.SimpleNamespace(path=Path(directory) / 'not-created.json'))
    if value.get('panel_url') != '':
        raise RuntimeError('Published program contains a preconfigured default server address')
    names = set(pyz.toc)
    local_tests = [name for name in names if name.startswith(('tests.', 'desktop_client.test_', 'desktop_server.test_')) or name.rsplit('.', 1)[-1].startswith('test_')]
    if local_tests:
        raise RuntimeError('Native package contains development test modules')
    optional = {'relay_bridge_bundle.software_updates'} & names
    if optional:
        raise RuntimeError('Ordinary/universal executable unexpectedly includes local optional provider implementation')
    return {'compiledVersionMatches': True, 'compiledEditionPayloadMatches': True, 'developmentTestsExcluded': True, 'freshDefaultAddressEmpty': True, 'optionalImplementationModules': sorted(optional)}

def selected_platforms(platforms=None):
    supported = policy()['platforms']
    if platforms is None:
        return supported
    if not isinstance(platforms, (list, tuple)) or not platforms or any((platform not in supported for platform in platforms)) or (len(set(platforms)) != len(platforms)):
        raise RuntimeError('Select a nonempty, unique set of supported release platforms')
    return [platform for platform in supported if platform in platforms]

def release_records(root, platforms=None):
    """Read only explicitly selected native verification reports."""
    names = {'Windows-x64': 'windows', 'Linux-x64': 'mint', 'macOS-arm64': 'mac'}
    records = []
    for platform in selected_platforms(platforms):
        report = json.loads((Path(root) / ('verification-' + names[platform] + '.json')).read_text(encoding='utf-8'))
        rows = report['packages']
        if not rows or any((row['platform'] != platform for row in rows)):
            raise RuntimeError('Verification report does not match its selected platform')
        records.extend(rows)
    return records

def validate_release(records, version, platforms=None):
    platforms = selected_platforms(platforms)
    validate_installer_formats(records)
    expected = {(platform, role, edition) for platform in platforms for role, _, edition in PLANS}
    actual = {(item['platform'], item['role'], item['edition']) for item in records}
    if actual != expected or len(records) != len(expected):
        raise RuntimeError(f'Incomplete release matrix: missing={sorted(expected - actual)}, unexpected={sorted(actual - expected)}')
    for item in records:
        if item['version'] != version:
            raise RuntimeError('All selected editions must share the same release version')
    for platform in platforms:
        clients = [item for item in records if item['platform'] == platform and item['role'] == 'client']
        hashes = [item['bridgeSourceHashes'] for item in clients]
        if not hashes[0] or any((value != hashes[0] for value in hashes[1:])):
            raise RuntimeError(f'{platform}: non-optional provider bridge sources differ between editions')
    return {'completeMatrix': True, 'platforms': platforms, 'clientsPerPlatform': sum((role == 'client' for role, _, _ in PLANS)), 'serversPerPlatform': sum((role == 'server' for role, _, _ in PLANS))}
