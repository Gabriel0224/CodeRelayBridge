"""Read-only deployed capability probe. Never starts turns or changes settings."""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
from crb.codex.agent import HostAgent
from crb.codex.native_features import NativeFeatures

@contextmanager
def isolated_probe_config(config):
    with tempfile.TemporaryDirectory(prefix='codex-relay-readonly-probe-') as state:
        pass

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding='utf-8'))
    with isolated_probe_config(config) as isolated:
        agent = HostAgent(isolated)
        agent.read_catalog()
        assert agent.catalog
        tid = agent.catalog[0]['id']
        bridge = NativeFeatures(agent)
        result = {'host': agent.cfg['host_id'], 'readOnly': True, 'checks': {}}
        for operation in ('permissions', 'tasks', 'tools', 'metadata'):
            try:
                data = bridge.execute('native_read', tid, {'operation': operation})
                result['checks'][operation] = {key: len(data[key]) for key in ('profiles', 'tasks', 'skills', 'plugins', 'servers') if key in data}
                if operation == 'metadata':
                    result['checks'][operation] = {'nativeTokenCount': data['metadata']['total'] is not None}
            except Exception as exc:
                result['checks'][operation] = {'error': str(exc)[:500]}
        print(json.dumps(result, ensure_ascii=False))
        assert not any(('error' in value for value in result['checks'].values()))
if __name__ == '__main__':
    main()
