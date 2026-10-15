"""Offline source/staged/history privacy gate. Reports locations, never values."""
import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import subprocess
ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PARTS = {'.ssh', 'artifacts', 'runtime', 'connections', 'logs', 'uploads', 'downloads', 'screenshots', 'profiles', 'node_modules', 'tests', '.venv', '.client-venv', '__pycache__', 'build', 'dist', 'target', 'releases'}
FORBIDDEN_NAMES = {'.npmrc', 'relay.json', 'agent.json', 'crb.server.relay.json', 'crb.codex.agent.json', 'auth.json', 'preferences.json', 'manager.json', 'client-session.json', 'device.json', 'web-access.txt'}
FORBIDDEN_SUFFIXES = {'.sqlite', '.sqlite3', '.db', '.jsonl', '.log', '.bak', '.tmp', '.key', '.pem', '.p12', '.pfx', '.exe', '.dll', '.zip', '.dmg', '.png', '.jpg', '.jpeg', '.gif', '.tiff', '.pdf', '.docx'}
IP = re.compile(b'(?<![\\w.])(?:\\d{1,3}\\.){3}\\d{1,3}(?![\\w.])')
SECRET = re.compile(b'sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----')
HOME = re.compile(b'(?:/home/|/Users/|[A-Z]:[/\\\\]+Users[/\\\\]+)([A-Za-z0-9_.-]+)')
LIVE_URL = re.compile(b'api\\.day\\.app/[A-Za-z0-9_-]+|[A-Za-z0-9-]+\\.tail[a-zA-Z0-9]+\\.ts\\.net')
DOC_NETS = tuple((ipaddress.ip_network(n) for n in ('192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24')))
FIXTURE_HOMES = {b'relay', b'relay-test', b'relay-other', b'test', b'test-user', b'example', b'example-user', b'user'}
FIXTURE_IPS = {'100.64.0.2', '100.64.0.3', '1.1.1.1', '8.8.8.8', '8.8.4.4'}
APPROVED_DOCUMENT_IMAGES = {'docs/images/crb-empty-desktop.png': '1e67a2f4e9c4ae4f6461ffd3cdbf289c5b7bb678c45492362a933fb54559ac9f'}

def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)

def inputs(mode):
    if mode == 'staged':
        names = git('diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z').split(b'\x00')
        return [(p.decode('utf-8'), git('show', ':' + p.decode('utf-8'))) for p in names if p]
    if mode == 'history':
        names = git('ls-tree', '-r', '--name-only', '-z', 'HEAD').split(b'\x00')
        return [(p.decode('utf-8'), git('show', 'HEAD:' + p.decode('utf-8'))) for p in names if p]
    return [(p.relative_to(ROOT).as_posix(), p.read_bytes()) for p in ROOT.rglob('*') if p.is_file() and '.git' not in p.relative_to(ROOT).parts and (not any((x in FORBIDDEN_PARTS for x in p.relative_to(ROOT).parts)))]

def check(name, data):
    path = Path(name)
    errors = []

    def fail(kind, line=0):
        errors.append({'file': name, 'line': line, 'category': kind})
    if name in APPROVED_DOCUMENT_IMAGES:
        if hashlib.sha256(data).hexdigest() != APPROVED_DOCUMENT_IMAGES[name]:
            fail('unreviewed-document-image')
        return errors
    if path.is_absolute() or '..' in path.parts or any((x in FORBIDDEN_PARTS for x in path.parts)):
        fail('forbidden-directory')
    if path.name in FORBIDDEN_NAMES or path.suffix in FORBIDDEN_SUFFIXES or '.bak' in path.name:
        fail('forbidden-file-type')
    if path.name.startswith('test_') or path.name == 'browser_test_runtime.js':
        fail('local-test-must-not-be-published')
    for number, line in enumerate(data.splitlines(), 1):
        for match in SECRET.finditer(line):
            if name in ('test_relay.py', 'tools/privacy_guard.py') and match[0] == b'sk-test-not-a-real-api-key':
                continue
            fail('credential-shaped-value', number)
        if LIVE_URL.search(line):
            fail('personal-service-endpoint', number)
        for match in HOME.finditer(line):
            if match[1].lower() not in FIXTURE_HOMES:
                fail('non-example-home-path', number)
        for match in IP.finditer(line):
            try:
                address = ipaddress.ip_address(match[0].decode())
            except ValueError:
                continue
            value = str(address)
            if address.is_loopback or address.is_unspecified or address.is_multicast or (value == '255.255.255.255'):
                continue
            if any((address in net for net in DOC_NETS)) or value in FIXTURE_IPS:
                continue
            if value == '100.64.0.0' and (b'/10' in line or b'100.64.0.0/255.192.0.0' in line):
                continue
            if value == '255.192.0.0' and (b'100.64.0.0/255.192.0.0' in line or name == 'tools/privacy_guard.py'):
                continue
            fail('non-example-ip-literal', number)
    return errors

def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--staged', action='store_true')
    group.add_argument('--history', action='store_true')
    args = parser.parse_args()
    mode = 'staged' if args.staged else 'history' if args.history else 'directory'
    files = inputs(mode)
    issues = [row for name, data in files for row in check(name, data)]
    content = dict(files)
    expected = json.loads(content.get('tools/vendor-hashes.json', (ROOT / 'tools/vendor-hashes.json').read_bytes()))
    for name, sha in expected.items():
        data = content.get(name)
        if data is None and mode == 'staged':
            try:
                data = git('show', 'HEAD:' + name)
            except subprocess.CalledProcessError:
                pass
        if data is not None and hashlib.sha256(data).hexdigest() != sha:
            issues.append({'file': name, 'line': 0, 'category': 'vendor-content-changed-review-required'})
    print(json.dumps({'passed': not issues, 'scope': mode, 'files': len(files), 'findings': issues}, ensure_ascii=True, indent=2))
    return 1 if issues else 0
if __name__ == '__main__':
    raise SystemExit(main())
