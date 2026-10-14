"""UI-owned, small control heartbeat while an App data bridge is disconnected.

No App IPC, chat reads, shell commands, separate listener or resident process.
The authenticated server can request only a fixed local App/bridge operation.
"""
import json
import threading
import time
import urllib.request
import uuid
from desktop_client import VERSION
from desktop_client.connections import atomic_json
from desktop_client.reconnect import ConnectionRetry
ACTIONS = frozenset({'start_app', 'start_bridge', 'restart_client'})

class ClientControlLink:

    def __init__(self, session, launcher=None, opener=None):
        self.session = session
        self.launcher = launcher
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.retry = {}
        self.receipts = {}
        self.lock = threading.Lock()

    def reset(self, key):
        self.retry.pop(key, None)

    def _journal(self, key):
        path = self.session.store.root / 'runtime' / ('control-' + key + '.json')
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            value = {}
        if not isinstance(value, dict) or len(value) > 256:
            raise ValueError('Invalid client control journal')
        return (path, value)

    def execute(self, key, command):
        cid = str(uuid.UUID(str(command['id'])))
        action = command.get('action')
        if action not in ACTIONS or command.get('thread') or command.get('text') not in ('', '{}'):
            return {'ok': False, 'operationId': cid, 'error': 'Unsupported client control operation'}
        path, journal = self._journal(key)
        if cid in journal:
            old = journal[cid]
            if old.get('action') != action:
                raise ValueError('Client control identity conflict')
            return old.get('result') or {'ok': False, 'unknown': True, 'operationId': cid, 'error': 'Client control result unknown; operation was not repeated'}
        journal[cid] = {'action': action, 'result': None}
        if len(journal) > 256:
            raise RuntimeError('Client control journal is full; no previous intent was discarded')
        atomic_json(path, journal)
        try:
            if self.session.closing:
                raise RuntimeError('Client is closing')
            if action == 'start_app':
                if self.launcher is None:
                    from desktop_client.native_apps import DesktopAppLauncher
                    self.launcher = DesktopAppLauncher()
                from desktop_client.backend import Preferences
                linked = key == 'codex' and Preferences(self.session.store.root).load().get('codex_question_link', False)
                self.launcher.launch(key, question_link=True) if linked else self.launcher.launch(key)
                self.session.start(self.session.specs[key], remember=True)
            elif action == 'start_bridge':
                self.session.start(self.session.specs[key], remember=True)
            result = {'ok': True, 'accepted': True, 'operationId': cid, 'clientRestart': action == 'restart_client'}
        except Exception as error:
            result = {'ok': False, 'operationId': cid, 'error': 'Client operation failed: ' + type(error).__name__}
        journal[cid]['result'] = result
        atomic_json(path, journal)
        return result

    def sync(self):
        if self.session.closing or not self.lock.acquire(blocking=False):
            return
        try:
            for key, entry in self.session.state()['applications'].items():
                if self.session.closing or not self.session.store.available(key):
                    continue
                child = self.session.children.get(key)
                alive = child is not None and child.poll() is None
                if alive and (not self.receipts.get(key)):
                    continue
                budget = self.retry.setdefault(key, ConnectionRetry())
                if not budget.begin('relay'):
                    continue
                try:
                    cfg = self.session.store.load(key)
                    endpoint = cfg['relay_url'].rstrip('/') + '/agent/sync'
                    headers = {'Authorization': 'Bearer ' + cfg['agent_token']}
                    probe = urllib.request.Request(endpoint, headers=headers)
                    with self.opener.open(probe, timeout=5) as response:
                        support = json.loads(response.read(4097))
                    if not isinstance(support, dict) or support.get('protocol') != 1 or support.get('controlHeartbeat') is not True:
                        raise ValueError('Server does not support client control heartbeats')
                    results = dict(self.receipts.get(key, {}))
                    payload = {'host': cfg['host_id'], 'label': cfg['label'], 'controlHeartbeat': True, 'clientControl': {'protocol': 1, 'version': VERSION, 'controlOnly': not alive, 'launchApp': True, 'startBridge': True, 'restartClient': True, 'retryExhausted': bool(entry.get('retry_exhausted'))}, 'results': results}
                    request = urllib.request.Request(endpoint, data=json.dumps(payload, separators=(',', ':')).encode(), headers={**headers, 'Content-Type': 'application/json'})
                    with self.opener.open(request, timeout=5) as response:
                        reply = json.loads(response.read(64 * 1024 + 1))
                    if not isinstance(reply, dict) or reply.get('protocol') != 1 or (not isinstance(reply.get('commands'), list)):
                        raise ValueError('Invalid client control response')
                    budget.success('relay')
                    pending = self.receipts.setdefault(key, {})
                    for cid in reply.get('acknowledged', []):
                        result = pending.pop(cid, None)
                        if result and result.get('ok') and result.get('clientRestart'):
                            self.session.remote_restart_ready = True
                    for command in reply['commands']:
                        if command.get('id') not in pending:
                            pending[command['id']] = self.execute(key, command)
                except Exception:
                    budget.failure('relay')
        finally:
            self.lock.release()
