"""Inspect our compiled Python literals and bundled metadata, not just source."""
import argparse
import json
from pathlib import Path
import re
import sys
import types
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.privacy_guard import check
from release_tools.release_matrix import inspect_bundle
HOME = re.compile('(?:/home/|/Users/|[A-Z]:[/\\\\]+Users[/\\\\]+)([A-Za-z0-9_.-]+)')
ALLOWED = {'relay', 'relay-test', 'relay-other', 'test', 'test-user', 'example', 'example-user', 'user'}
CODE_PREFIXES = ('desktop_client', 'desktop_server', 'relay_bridge_bundle', 'crb.')
CORE = {'relay', 'agent', 'ipc', 'question_ui', 'native_history', 'live_view_cache', 'software_updates', 'native_updates', 'codex_updates', 'harness_agent', 'harness_client', 'dsh_desktop_agent', 'dsh_desktop_client', 'mac_native_runtime', 'native_features', 'read_progress', 'file_access', 'windows_pty', 'client_pairing', 'panel_restart', 'upstream_monitor'}

def code_values(code):
    if isinstance(code, types.CodeType):
        yield (code.co_filename, 'code-filename')
        for value in code.co_consts:
            yield from code_values(value)
    elif isinstance(code, str):
        yield (code, 'literal')
    elif isinstance(code, bytes):
        try:
            yield (code.decode('utf-8'), 'literal')
        except UnicodeError:
            pass
    elif isinstance(code, (tuple, list, frozenset)):
        for value in code:
            yield from code_values(value)

def audit(package, role, edition, version):
    from PyInstaller.archive.readers import CArchiveReader
    package = Path(package)
    product = 'RelayClient' if role == 'client' else 'RelayServer'
    executable = package / 'Contents/MacOS' / product if sys.platform == 'darwin' else package / (product + ('.exe' if sys.platform == 'win32' else ''))
    identity = inspect_bundle(executable, edition, version)
    archive = CArchiveReader(str(executable)).open_embedded_archive('PYZ.pyz')
    findings = []
    modules, strings = (0, 0)
    for name in archive.toc:
        if not (name.startswith(CODE_PREFIXES) or name in CORE or name.startswith('test_')):
            continue
        modules += 1
        source_name = name.replace('.', '/') + '.py'
        for value, kind in code_values(archive.extract(name)):
            strings += 1
            if kind == 'code-filename':
                if any((m.group(1).lower() not in ALLOWED for m in HOME.finditer(value))):
                    findings.append({'file': source_name, 'category': 'personal-build-filename'})
                continue
            for row in check(source_name, value.encode('utf-8')):
                findings.append({'file': source_name, 'category': row['category']})
    metadata = 0
    for file in package.rglob('*'):
        if file.is_file() and file.name in {'manifest.json', 'relay-edition.json'}:
            metadata += 1
            findings += check(file.relative_to(package).as_posix(), file.read_bytes())
    if role == 'server':
        resources = package / ('Contents/Frameworks' if sys.platform == 'darwin' else '_internal')
        for file in (resources / 'web').rglob('*'):
            if file.is_file() and 'vendor' not in file.relative_to(resources / 'web').parts:
                findings += check('web/' + file.relative_to(resources / 'web').as_posix(), file.read_bytes())
    unique = {json.dumps(row, sort_keys=True) for row in findings}
    return {'passed': not unique, 'role': role, 'edition': edition, 'version': version, 'compiledModulesReviewed': modules, 'compiledStringsReviewed': strings, 'metadataFilesReviewed': metadata, 'compiledIdentity': identity, 'findings': [json.loads(row) for row in sorted(unique)]}
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('package', type=Path)
    parser.add_argument('--role', required=True)
    parser.add_argument('--edition', required=True)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    value = audit(args.package, args.role, args.edition, args.version)
    print(json.dumps(value))
    sys.exit(0 if value['passed'] else 1)
