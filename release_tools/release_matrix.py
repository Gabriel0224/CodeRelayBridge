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

def validate_release(records, version):
    validate_installer_formats(records)
    expected = {(platform, role, 'standard') for platform in policy()['platforms'] for role in ('client', 'server')}
    actual = {(r['platform'], r['role'], r['edition']) for r in records}
    if actual != expected or len(records) != len(expected) or any((r['version'] != version for r in records)):
        raise RuntimeError('Incomplete Standard release matrix')
    return {'completeMatrix': True, 'clientsPerPlatform': 1, 'serversPerPlatform': 1}
