"""Authenticated relay commands control the UI-owned client, not model Apps.

The restart is dispatched only AFTER the server has acknowledged its receipt.
There is no new listener, timer, credential or standalone background service.
"""
import gzip
import json
import os
import threading
import types
import uuid
from desktop_client import VERSION

class RemoteClientControl:

    def __init__(self, owner):
        self.owner = dict(owner)
        self.lock = threading.Lock()
        self.pending = None
        self.ready = False
        self.dispatched = False

    def bind(self, agent):
        original = agent.execute
        control = self

        def execute(instance, command):
            if command.get('action') != 'restart_client':
                return original(command)
            cid = str(uuid.UUID(str(command.get('id'))))
            if command.get('thread') or command.get('text'):
                raise ValueError('客户端重启不接受对话或命令参数')
            with control.lock:
                if control.pending and control.pending != cid:
                    raise RuntimeError('客户端重启已经提交，请等待重新上线')
                control.pending = cid
            return {'ok': True, 'accepted': True, 'clientRestart': True, 'operationId': cid, 'oldOwnerPID': control.owner['pid']}
        agent.execute = types.MethodType(execute, agent)

    def decorate(self, request, local_access=None):
        compressed = request.get_header('Content-encoding') == 'gzip'
        data = json.loads(gzip.decompress(request.data) if compressed else request.data)
        if local_access:
            local_access.decorate_payload(data)
        capabilities = data.setdefault('capabilities', {})
        capabilities['clientControl'] = {'protocol': 1, 'restartClient': True, 'ownerPID': self.owner['pid'], 'ownerCreated': self.owner['created'], 'workerPID': os.getpid(), 'version': VERSION}
        if isinstance(capabilities.get('actions'), list):
            capabilities['actions'] = sorted(set(capabilities['actions']) | {'restart_client'})
        with self.lock:
            cid = self.pending
        result = data.get('results', {}).get(cid)
        request.relay_restart_ack = cid if isinstance(result, dict) and result.get('ok') and result.get('clientRestart') and (result.get('operationId') == cid) else None
        encoded = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()
        request.data = gzip.compress(encoded, compresslevel=5, mtime=0) if compressed else encoded
        return request

    def acknowledge(self, request):
        with self.lock:
            if self.pending and getattr(request, 'relay_restart_ack', None) == self.pending:
                self.ready = True

    def dispatch_if_ready(self, dispatch):
        with self.lock:
            if not self.ready or self.dispatched:
                return False
            self.dispatched = True
        dispatch('restart')
        return True
