"""Read-only upstream release and local pin check; optional notice dedup state."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from crb.paths import resource_root
ROOT = resource_root()
from crb.paths import upstreams_file
MANIFEST = upstreams_file()
STATE = ROOT / '.upstream-monitor-state.json'

def _local_path(relative: str) -> Path:
    path = (ROOT / relative).resolve()
    path.relative_to(ROOT.resolve())
    return path

def fetch_latest(source: dict) -> tuple[str, str]:
    if source['type'] == 'github_release':
        repository = source['repository']
        url = f'https://api.github.com/repos/{repository}/releases/latest'
    elif source['type'] == 'npm':
        package = source['package']
        url = f"https://registry.npmjs.org/{quote(package, safe='@')}/latest"
    else:
        raise ValueError(f"unknown source type: {source['type']}")
    request = Request(url, headers={'User-Agent': 'demo-codex-panel-upstream-monitor/1'})
    with urlopen(request, timeout=20) as response:
        payload = json.load(response)
    if source['type'] == 'github_release':
        return (payload['tag_name'], payload['html_url'])
    version = payload['version']
    return (version, f'https://www.npmjs.com/package/{package}/v/{version}')

def local_drift(component: dict) -> list[str]:
    problems = []
    for pin in component.get('pins', []):
        try:
            actual = _local_path(pin['path']).read_text(encoding='utf-8').strip()
        except (OSError, ValueError) as error:
            problems.append(f"{pin['path']}: {error}")
            continue
        if actual != pin['content']:
            problems.append(f"{pin['path']}: fixed revision changed")
    for asset in component.get('assets', []):
        try:
            actual = hashlib.sha256(_local_path(asset['path']).read_bytes()).hexdigest()
        except (OSError, ValueError) as error:
            problems.append(f"{asset['path']}: {error}")
            continue
        if actual != asset['sha256']:
            problems.append(f"{asset['path']}: SHA-256 changed")
    return problems

def load_state(path: Path=STATE) -> dict:
    if not path.exists():
        return {'notified': {}}
    state = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(state, dict) or not isinstance(state.get('notified'), dict):
        raise ValueError(f'invalid monitor state: {path}')
    return state

def check(manifest: dict, state: dict, fetcher=fetch_latest) -> dict:
    results = []
    for component in manifest['components']:
        drift = local_drift(component)
        entry = {'id': component['id'], 'name': component['name'], 'installed': component['installed'], 'local_drift': drift, 'latest': None, 'release_url': None, 'update_available': False, 'new_notice': False, 'fetch_error': None}
        try:
            latest, release_url = fetcher(component['source'])
            entry['latest'] = latest
            entry['release_url'] = release_url
            entry['update_available'] = latest != component['installed']
            entry['new_notice'] = entry['update_available'] and state['notified'].get(component['id']) != latest
        except (OSError, ValueError, KeyError, TimeoutError) as error:
            entry['fetch_error'] = str(error)
        results.append(entry)
    return {'checked_at': datetime.now(timezone.utc).isoformat(), 'components': results}

def record_notices(report: dict, state: dict, path: Path=STATE) -> None:
    for entry in report['components']:
        if entry['new_notice'] and (not entry['local_drift']):
            state['notified'][entry['id']] = entry['latest']
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='machine-readable report')
    parser.add_argument('--record-notified', action='store_true', help='record newly reported versions to avoid repeat notices')
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    if manifest.get('schema') != 1:
        parser.error('unsupported upstream manifest schema')
    state = load_state()
    report = check(manifest, state)
    if args.record_notified:
        record_notices(report, state)
    if args.json:
        print(json.dumps(report, ensure_ascii=True, indent=2))
    else:
        for entry in report['components']:
            status = 'UPDATE' if entry['update_available'] else 'CURRENT'
            if entry['fetch_error']:
                status = 'FETCH ERROR'
            if entry['local_drift']:
                status = 'LOCAL DRIFT'
            print(f"{status}: {entry['name']} {entry['installed']} → {entry['latest'] or '?'}")
            for problem in entry['local_drift']:
                print(f'  {problem}')
            if entry['fetch_error']:
                print(f"  {entry['fetch_error']}")
            if entry['new_notice']:
                print(f"  NEW: {entry['release_url']}")
    return 2 if any((e['local_drift'] or e['fetch_error'] for e in report['components'])) else 0
if __name__ == '__main__':
    raise SystemExit(main())
