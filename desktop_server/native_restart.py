"""Native-package web restart with a helper outside the server service lifetime."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from crb.server.panel_restart import MODES, PENDING, read_record, write_record, restart_agent, restart_application_agent

class NativeRestartManager:

    def __init__(self, manager):
        self.manager = manager
        self.state_dir = manager.config()['state_dir']
        self.lock = threading.Lock()

    def status(self):
        tasks, spec = (self.manager.tasks(), self.manager.spec())
        enabled = self.manager.legacy_service() or tasks.exists(spec)
        if enabled and (not self.manager.legacy_service()):
            tasks.inspect(spec)
        return {'enabled': enabled, 'restart': read_record(self.state_dir)}

    def request(self, request_id, mode):
        request_id = str(uuid.UUID(str(request_id)))
        if mode not in MODES:
            raise ValueError('未知重启方式')
        if not self.status()['enabled']:
            raise RuntimeError('服务未安装，不能通过网页重启')
        with self.lock:
            old = read_record(self.state_dir)
            if old.get('requestId') == request_id:
                return {'ok': True, 'accepted': True, 'requestId': request_id, 'restart': old}
            if old.get('phase') in PENDING and time.time() - old.get('requestedAt', 0) < 300:
                raise RuntimeError('面板重启正在进行，请等待')
            record = {'requestId': request_id, 'mode': mode, 'phase': 'queued', 'requestedAt': time.time(), 'oldPID': os.getpid(), 'agents': []}
            write_record(self.state_dir, record)
            args = [sys.executable, '--restart-helper', request_id, mode]
            try:
                if sys.platform.startswith('linux'):
                    subprocess.run(['systemd-run', '--user', '--collect', '--quiet', '--unit=relay-restart-' + request_id, *args], check=True, timeout=10, capture_output=True)
                elif sys.platform == 'darwin':
                    subprocess.run(['launchctl', 'submit', '-l', 'com.codexrelay.restart.' + request_id, '--', *args], check=True, timeout=10, capture_output=True)
                else:
                    subprocess.Popen(args, creationflags=subprocess.CREATE_NO_WINDOW, env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1'), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
            except Exception:
                write_record(self.state_dir, dict(record, phase='failed', error='无法启动独立重启程序'))
                raise RuntimeError('无法启动独立重启程序') from None
            return {'ok': True, 'accepted': True, 'requestId': request_id, 'restart': record}

def restart_clients(manager, cfg):
    """Discover paired clients, deduplicate Apps on the same physical PC."""
    hosts = manager.request('/api/lite/catalog').get('hosts', [])
    groups = {}
    for host in hosts:
        if not isinstance(host, dict) or not host.get('id'):
            continue
        physical = host.get('physicalHostId') or host['id']
        groups.setdefault(physical, []).append(host)

    def restart_group(physical, nodes):
        online = [node for node in nodes if node.get('online')]
        if not online:
            return {'host': physical, 'status': 'skipped', 'reason': '设备离线，未发送重启'}
        node = next((node for node in online if node.get('capabilities', {}).get('clientControl', {}).get('restartClient')), None)
        if node is None:
            if physical in ('win', 'mint', 'mac', 'local') and (physical in cfg.get('agents', {}) or physical in cfg.get('ssh_hosts', {})):
                result = restart_agent(physical, cfg)
                if result.get('clientRestart'):
                    return result
                extra = [restart_application_agent(n['id'], cfg) for n in online if n.get('applicationId') == 'deepseek']
                if any((row['status'] != 'restarted' for row in extra)):
                    return {'host': physical, 'status': 'failed', 'error': '部分旧桥接未确认重启'}
                return result
            return {'host': physical, 'status': 'unsupported', 'error': '旧客户端尚未提供 UI 重启能力，请先更新该客户端'}
        control = node['capabilities']['clientControl']
        cid = str(uuid.uuid4())
        try:
            manager.request('/api/command', {'id': cid, 'host': node['id'], 'thread': '', 'action': 'restart_client', 'text': ''})
            deadline = time.monotonic() + 55
            accepted = False
            while time.monotonic() < deadline:
                if not accepted:
                    result = manager.request('/api/command?id=' + cid).get('result')
                    if result:
                        if not result.get('ok') or not result.get('clientRestart') or result.get('operationId') != cid:
                            raise RuntimeError('客户端未接受重启')
                        accepted = True
                if accepted:
                    current = next((h for h in manager.request('/api/lite/catalog').get('hosts', []) if h.get('id') == node['id'] and h.get('online')), None)
                    new = (current or {}).get('capabilities', {}).get('clientControl', {})
                    identity = (new.get('ownerPID'), new.get('ownerCreated'))
                    if new.get('protocol') == 1 and all(identity) and (identity != (control.get('ownerPID'), control.get('ownerCreated'))):
                        return {'host': physical, 'status': 'restarted', 'clientRestart': True, 'oldPID': control.get('ownerPID'), 'newPID': new['ownerPID']}
                time.sleep(0.75)
            raise RuntimeError('客户端重启后未重新上线')
        except Exception as exc:
            return {'host': physical, 'status': 'failed', 'error': '客户端重启未完成或未重新上线', 'errorType': type(exc).__name__}
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(groups)))) as pool:
        jobs = {pool.submit(restart_group, physical, nodes): physical for physical, nodes in groups.items()}
        results = []
        for job in as_completed(jobs):
            try:
                results.append(job.result())
            except Exception as exc:
                results.append({'host': jobs[job], 'status': 'failed', 'errorType': type(exc).__name__, 'error': '客户端重启未完成'})
        return sorted(results, key=lambda row: row['host'])

def helper(request_id, mode):
    from desktop_server.backend import ServerManager
    from desktop_client.bridge_runtime import sanitize_child_launches
    sanitize_child_launches()
    request_id = str(uuid.UUID(request_id))
    if mode not in MODES:
        raise ValueError('未知重启方式')
    manager = ServerManager()
    cfg = manager.config()
    state = cfg['state_dir']
    record = read_record(state)
    if record.get('requestId') != request_id or record.get('mode') != mode or record.get('phase') != 'queued':
        return 1
    time.sleep(2)

    def phase(name, **fields):
        record.update(phase=name, **fields)
        write_record(state, record)
    try:
        if mode == 'all':
            phase('agents')
            record['agents'] = restart_clients(manager, cfg)
            write_record(state, record)
        phase('stopping')
        manager.control('restart')
        phase('checking')
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                new = manager.request('/api/panel/status')['processId']
                if new and new != record['oldPID']:
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError('新服务健康检查失败')
        phase('partial' if any((a['status'] not in ('restarted', 'skipped') for a in record['agents'])) else 'completed', newPID=new, completedAt=time.time())
        return 0
    except Exception as exc:
        phase('failed', error='重启未完成，请查看服务端日志', errorType=type(exc).__name__)
        return 1
