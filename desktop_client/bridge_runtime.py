"""The EXE's no-GUI worker mode; no system Python or external .py launcher."""
from __future__ import annotations
import ctypes
import importlib
import io
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import psutil
from desktop_client import VERSION
from desktop_client.edition import edition
from desktop_client.connections import ConnectionStore, atomic_json, client_root
from desktop_client.backend import redact
from desktop_client.reconnect import ConnectionRetry, RetryDeferred, EXHAUSTED_EXIT_CODE

class WorkerLease:
    """Same-user instance guard. Harness shares the legacy launcher's mutex."""

    def __init__(self, key, host_id):
        self.file = None
        self.handle = None
        if sys.platform != 'win32':
            import fcntl
            root = client_root() / 'runtime'
            root.mkdir(parents=True, exist_ok=True, mode=448)
            path = root / (key + '.lock')
            fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 384)
            self.file = os.fdopen(fd, 'a')
            try:
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.acquired = True
            except BlockingIOError:
                self.acquired = False
            return
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        self.kernel.CreateMutexW.restype = ctypes.c_void_p
        self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        name = ('Local\\CodexRelayHarness-' if key == 'deepseek' else 'Local\\CodexRelayClient-') + host_id
        self.handle = self.kernel.CreateMutexW(None, False, name)
        self.acquired = bool(self.handle) and ctypes.get_last_error() != 183

    def close(self):
        if self.file:
            self.file.close()
            self.file = None
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

class LogStream:

    def __init__(self, logger):
        self.logger = logger
        self.local = threading.local()

    def write(self, value):
        if getattr(self.local, 'logging', False):
            return len(value)
        text = getattr(self.local, 'text', '') + value
        while '\n' in text:
            line, text = text.split('\n', 1)
            if line.strip():
                self.local.logging = True
                try:
                    self.logger.info(redact(line.strip()))
                finally:
                    self.local.logging = False
        self.local.text = text[-8000:]
        return len(value)

    def flush(self):
        pass

def cached_byte_count(value):
    """Discard malformed auxiliary counters without blocking the real bridge."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return 0
    try:
        return max(0, int(value))
    except (ValueError, OverflowError):
        return 0

class SyncMeter:
    """Observe only existing sync requests. Never issues additional requests."""

    def __init__(self, key, config, root=None, logger=None):
        self.key = key
        self.root = Path(root or client_root())
        self.path = self.root / 'runtime' / f'{key}.json'
        self.sync_url = config['relay_url'].rstrip('/') + '/agent/sync'
        self.lock = threading.RLock()
        self.agent = None
        self.local_access = None
        self.client_control = None
        self.logger = logger if logger is not None else logging.getLogger('relay-worker-' + key)
        self.publish_failures = 0
        self.retry = ConnectionRetry()
        self.last_publish_warning = None
        prior = {}
        try:
            prior = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        if not isinstance(prior, dict):
            prior = {}
        self.state = {'version': VERSION, 'key': key, 'pid': os.getpid(), 'created': psutil.Process().create_time(), 'started_at': time.time(), 'updated_at': time.time(), 'last_sync_at': None, 'last_error': '正在连接', 'sent_bytes': cached_byte_count(prior.get('sent_bytes', 0)), 'received_bytes': cached_byte_count(prior.get('received_bytes', 0)), 'successful_syncs': 0, 'failed_syncs': 0, 'desktop_connected': False, 'phase': 'starting', 'latency_ms': None, 'catalog_count': 0, 'models_count': 0}

    def success(self, sent, received, elapsed):
        with self.lock:
            self.state.update(last_sync_at=time.time(), last_error='', phase='running', latency_ms=round(elapsed * 1000))
            self.state['sent_bytes'] += sent
            self.state['received_bytes'] += received
            self.state['successful_syncs'] += 1

    def failure(self, exc):
        with self.lock:
            self.state['last_error'] = f'HTTP {exc.code}' if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__
            self.state['failed_syncs'] += 1

    def snapshot(self):
        with self.lock:
            result = dict(self.state)
        result['updated_at'] = time.time()
        if self.agent:
            agent = self.agent
            result['desktop_connected'] = bool(agent.ipc and agent.ipc.connected) if self.key == 'codex' else bool(agent.connected)
            result['catalog_count'] = len(agent.catalog)
            result['models_count'] = len(agent.models)
            result['commands_inflight'] = len(agent.inflight)
            if self.key == 'codex':
                pass
        result.update(self.retry.snapshot())
        if result['retry_exhausted']:
            result['phase'] = 'disconnected'
        elif result['retry_attempts'] and result['phase'] not in {'stopped', 'error'}:
            result['phase'] = 'reconnecting'
        if result['phase'] in {'stopped', 'error', 'disconnected'}:
            result['desktop_connected'] = False
        return result

    def publish(self):
        value = self.snapshot()
        try:
            atomic_json(self.path, value)
        except OSError as exc:
            self.publish_failures += 1
            now = time.monotonic()
            if self.last_publish_warning is None or now - self.last_publish_warning >= 60:
                self.logger.warning('本地运行状态暂未写入（%s，winerror=%s）；桥接继续运行，下轮重试', type(exc).__name__, getattr(exc, 'winerror', None))
                self.last_publish_warning = now
            return False
        if self.publish_failures:
            self.logger.info('本地运行状态写入已恢复；跳过 %d 次状态写入', self.publish_failures)
            self.publish_failures = 0
            self.last_publish_warning = None
        return True

    def install(self):
        original = urllib.request.OpenerDirector.open
        meter = self

        def tracked_open(opener, fullurl, data=None, timeout=socket_timeout_default):
            url = fullurl.full_url if isinstance(fullurl, urllib.request.Request) else str(fullurl)
            if url != meter.sync_url:
                return original(opener, fullurl, data, timeout)
            if isinstance(fullurl, urllib.request.Request):
                if meter.client_control:
                    fullurl = meter.client_control.decorate(fullurl, meter.local_access)
                elif meter.local_access:
                    fullurl = meter.local_access.decorate(fullurl)
            sent = len(fullurl.data or b'') if isinstance(fullurl, urllib.request.Request) else len(data or b'')
            if not meter.retry.begin('relay'):
                raise RetryDeferred('Waiting for the next connection attempt')
            started = time.monotonic()
            try:
                with original(opener, fullurl, data, timeout) as response:
                    raw = response.read()
                    value = json.loads(raw)
                    if not isinstance(value, dict) or not isinstance(value.get('commands', []), list):
                        raise ValueError('Invalid relay response')
                meter.success(sent, len(raw), time.monotonic() - started)
                meter.retry.success('relay')
                if meter.client_control:
                    meter.client_control.acknowledge(fullurl)
                return io.BytesIO(raw)
            except Exception as exc:
                meter.failure(exc)
                meter.retry.failure('relay')
                raise
        urllib.request.OpenerDirector.open = tracked_open
        return original
import socket
socket_timeout_default = socket._GLOBAL_DEFAULT_TIMEOUT
_launches_sanitized = False

def sanitize_child_launches():
    global _launches_sanitized
    if not getattr(sys, 'frozen', False):
        return
    if _launches_sanitized:
        return
    _launches_sanitized = True
    if sys.platform != 'win32':
        original = subprocess.Popen
        package_root = Path(sys._MEIPASS).resolve()

        class PosixPopen(original):

            def __init__(self, *args, **kwargs):
                env = dict(kwargs.get('env') or os.environ)
                for name in ('LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH'):
                    if name + '_ORIG' in env:
                        env[name] = env[name + '_ORIG']
                    elif name in env:
                        paths = [p for p in env[name].split(os.pathsep) if p and (not Path(p).resolve().is_relative_to(package_root))]
                        if paths:
                            env[name] = os.pathsep.join(paths)
                        else:
                            env.pop(name, None)
                for name in list(env):
                    if name.startswith('_PYI_'):
                        env.pop(name, None)
                kwargs['env'] = env
                super().__init__(*args, **kwargs)
        subprocess.Popen = PosixPopen
        return
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    lock = threading.RLock()
    original = subprocess.Popen
    package_root = str(Path(sys._MEIPASS).resolve()).casefold()

    class CleanPopen(original):

        def __init__(self, *args, **kwargs):
            env = dict(kwargs.get('env') or os.environ)
            env['PATH'] = os.pathsep.join((p for p in env.get('PATH', '').split(os.pathsep) if not str(Path(p).resolve()).casefold().startswith(package_root)))
            for key in list(env):
                if key.startswith('_PYI_'):
                    env.pop(key, None)
            kwargs['env'] = env
            argv = kwargs.get('args', args[0] if args else None)
            binary = Path(argv[0]).name.lower() if isinstance(argv, (list, tuple)) and argv else ''
            if binary in {'pwsh.exe', 'powershell.exe', 'cmd.exe', 'ssh.exe', 'python.exe', 'pythonw.exe'}:
                from crb.platform.process_launch import hidden_launch_options
                hidden = hidden_launch_options()
                kwargs['creationflags'] = kwargs.get('creationflags', 0) | hidden['creationflags']
                kwargs.setdefault('startupinfo', hidden['startupinfo'])
            with lock:
                kernel.SetDllDirectoryW(None)
                try:
                    super().__init__(*args, **kwargs)
                finally:
                    kernel.SetDllDirectoryW(str(sys._MEIPASS))
    subprocess.Popen = CleanPopen

def load_core(key):
    name = {'codex': 'agent', 'deepseek': 'harness_agent', 'claude': 'claude_agent'}[key]
    if not getattr(sys, 'frozen', False):
        module = {'codex': 'crb.codex.agent', 'deepseek': 'crb.deepseek.harness_agent', 'claude': 'crb.claude.agent'}[key]
        return importlib.import_module(module)
    return importlib.import_module('relay_bridge_bundle.' + name)

def stop_core(agent, key):
    if key in {'deepseek', 'claude'}:
        agent.stop.set()
        agent.client.close()
    else:
        agent.close_all_terminals()
        ipc = getattr(agent, 'ipc', None)
        if ipc:
            stream = getattr(ipc, 'sock', None)
            if stream:
                stream.close()

def cleanup_worker_helpers():
    """Only the bridge's short-lived CLI/adapter helpers, never desktop Apps."""
    try:
        children = psutil.Process().children()
    except psutil.Error:
        return
    for child in children:
        try:
            args = child.cmdline()
            exe = Path(child.exe())
            stdio = 'app-server' in args[1:] and '--stdio' in args[1:]
            if stdio or False:
                child.terminate()
                try:
                    child.wait(timeout=2)
                except psutil.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=2)
        except (psutil.Error, OSError):
            pass

def run_worker(key):
    from desktop_client.startup_diagnostics import boot_mark, boot_span
    boot_mark('worker.begin')
    if not getattr(sys, 'frozen', False):
        raise RuntimeError('完整桥接需要从独立 Relay Client 启动，不使用外部 Python 运行。')
    from desktop_client.lifecycle import worker_owner, worker_should_stop, watch_parent_pipe
    owner = worker_owner()
    boot_mark('worker.owner-checked')
    parent_closed = threading.Event()
    threading.Thread(target=watch_parent_pipe, args=(parent_closed,), daemon=True, name='client-lifetime').start()
    store = ConnectionStore()
    cfg = store.import_existing(key)
    boot_mark('worker.config-read')
    from desktop_client.backend import bridge_specs, matching_processes, process_rows
    processes, _ = matching_processes(bridge_specs()[key], process_rows())
    boot_mark('worker.process-snapshot')
    if any((p['pid'] != os.getpid() for p in processes)):
        return 0
    lease = WorkerLease(key, cfg['host_id'])
    if not lease.acquired:
        lease.close()
        return 0
    boot_mark('worker.mutex-acquired')
    root = client_root()
    runtime = root / 'runtime'
    runtime.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger('relay-worker-' + key)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = RotatingFileHandler(runtime / f'{key}.log', maxBytes=512 * 1024, backupCount=2, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    logger.addHandler(handler)
    sys.stdout = sys.stderr = LogStream(logger)
    boot_mark('worker.log-ready')
    meter = SyncMeter(key, cfg, logger=logger)
    boot_mark('worker.meter-ready')
    from desktop_client.remote_control import RemoteClientControl
    meter.client_control = RemoteClientControl(owner)
    meter.state.update(owner_pid=owner['pid'], owner_created=owner['created'])
    meter.publish()
    boot_mark('worker.state-published')
    errors = []
    finished = threading.Event()

    def core_loop():
        try:
            with boot_span('core.import'):
                core = load_core(key)
            sanitize_child_launches()
            if key == 'codex':
                cfg = dict(meter_config)
                bundled = Path(sys._MEIPASS) / 'relay_bridge_bundle'
                if sys.platform == 'darwin':
                    mac_runtime = importlib.import_module('relay_bridge_bundle.mac_native_runtime')
                    sys.modules['mac_native_runtime'] = mac_runtime
                    sys.modules['crb.platform.mac_native_runtime'] = mac_runtime
                with boot_span('core.construct'):
                    agent = core.HostAgent(cfg)
            elif key == 'claude':
                agent = core.ClaudeAgent(meter_config)
            else:
                client = None
                from desktop_client.platform_support import harness_payload
                payload = harness_payload(harness_home=meter_config.get('harness_home'), preferred=meter_config.get('native_desktop_payload'))
                if payload:
                    modern = importlib.import_module('relay_bridge_bundle.modern_desktop_client')

                    def desktop_port():
                        current = harness_payload(harness_home=meter_config['harness_home'], preferred=payload) or payload
                        return modern.discover_modern_desktop_port(meter_config['harness_home'], current)
                    client = modern.ModernDesktopHarnessClient(meter_config['harness_home'], payload, port_resolver=desktop_port)
                agent = core.HarnessAgent(meter_config, client=client)
            if meter_config.get('local_access', True):
                from desktop_client.local_access import LocalAccess
                meter.local_access = LocalAccess(agent, meter_config['host_id'], load_core('codex'))
            meter.agent = agent
            agent.connection_retry = meter.retry
            meter.client_control.bind(agent)
            boot_mark('core.run.begin')
            agent.run()
        except Exception as exc:
            errors.append(exc)
            meter.failure(exc)
            logger.exception('桥接运行错误')
        finally:
            finished.set()
    meter_config = cfg
    original_open = meter.install()
    worker = threading.Thread(target=core_loop, daemon=True, name='relay-core-' + key)
    worker.start()
    stopping = False
    exhausted = False
    try:
        while not finished.wait(1):
            meter.publish()
            if meter.retry.exhausted:
                stopping = exhausted = True
                break
            from desktop_client.lifecycle import send_client_action
            try:
                meter.client_control.dispatch_if_ready(send_client_action)
            except Exception as exc:
                logger.warning('客户端重启未被 UI 接受（%s）；原 App 未停止', type(exc).__name__)
            stop_file = runtime / f'{key}.stop.json'
            if parent_closed.is_set() or worker_should_stop(owner, stop_file, os.getpid(), meter.state['created']):
                stopping = True
                break
        exhausted = exhausted or meter.retry.exhausted
        stopping = stopping or exhausted
        if stopping and meter.agent:
            if meter.local_access:
                meter.local_access.close()
            stop_core(meter.agent, key)
            worker.join(3)
        with meter.lock:
            meter.state.update(phase='disconnected' if exhausted else 'stopped' if stopping else 'error', desktop_connected=False)
        meter.publish()
        if exhausted:
            return EXHAUSTED_EXIT_CODE
        return 0 if stopping else 1
    finally:
        if meter.local_access:
            meter.local_access.close()
        cleanup_worker_helpers()
        urllib.request.OpenerDirector.open = original_open
        lease.close()

def package_probe():
    result = {'version': VERSION, 'edition': edition(), 'frozen': bool(getattr(sys, 'frozen', False)), 'executable': sys.executable, 'cores': {}, 'dependencies': {}}
    for key in ('codex', 'deepseek'):
        module = load_core(key)
        result['cores'][key] = module.__file__
    if sys.platform.startswith('linux'):
        modern = importlib.import_module('relay_bridge_bundle.modern_desktop_client')
        result['cores']['deepseek_rc2'] = modern.__file__
    dependencies = ['yaml', 'websocket', 'sqlite3', 'psutil']
    if sys.platform == 'win32':
        dependencies += ['pythoncom', 'win32com.client']
    for name in dependencies:
        module = importlib.import_module(name)
        result['dependencies'][name] = getattr(module, '__file__', 'built-in')
    result['no_external_bridge_imports'] = all((str(Path(sys._MEIPASS).resolve()).casefold() in str(p).casefold() for p in result['cores'].values())) if result['frozen'] else False
    result['question_contract'] = question_contract_probe(load_core('codex'))
    result['edition_contract'] = edition_contract_probe(load_core('codex'))
    result['review_contract'] = review_contract_probe(load_core('deepseek'))
    result['passed'] = all(result['question_contract'].values()) and result['no_external_bridge_imports'] and all(result['edition_contract'].values()) and all(result['review_contract'].values())
    return result

def review_contract_probe(harness_core):
    """Verify actual bundled revisions; never perform an App restart or send."""
    import tempfile
    from unittest.mock import Mock
    from desktop_client.remote_control import RemoteClientControl
    checks = {}
    session = harness_core.SessionView('probe')
    session.events[1] = {'seq': 1, 'type': 'turn/start', 'time': 1000, 'data': {'turn': 1}}
    session.events[2] = {'seq': 2, 'type': 'turn/end', 'time': 1200, 'data': {'turn': 1, 'reason': {'kind': 'forked'}}}
    public = session.public({})
    checks['deepseek_fork_not_failed'] = public['executionState']['phase'] == 'forked' and (not any((i.get('noticeKind') == 'error' for i in public['items'])))
    with tempfile.TemporaryDirectory(prefix='relay-review-probe-') as directory:
        agent = harness_core.HarnessAgent({'state_dir': directory, 'host_id': 'probe', 'physical_host_id': 'probe', 'label': 'probe'}, client=Mock())
        agent.states['probe'] = session
        agent.dirty.add('probe')
        for event, phase in [('approval/request', 'waiting_approval'), ('user-questions/request', 'waiting_input')]:
            agent.approvals = {'request': {'agentId': 'probe', 'event': event, 'request': {'questions': []}}}
            checks['deepseek_' + phase] = agent.payload()['views']['probe']['executionState']['phase'] == phase
        control = RemoteClientControl({'pid': 1, 'created': 1})
        control.bind(agent)
        receipt = agent.execute({'action': 'restart_client', 'id': '11111111-1111-4111-8111-111111111111'})
        checks['client_restart_deferred'] = receipt['clientRestart'] and (not control.dispatch_if_ready(Mock()))
    return checks

def edition_contract_probe(core):
    return {'edition_identity': edition() == 'standard', 'codex_updater_kept': callable(core.software_updates.check) and callable(core.software_updates.install)}

def question_contract_probe(core):
    """Test the actually bundled answer path without connecting to any App."""
    import tempfile
    from unittest.mock import Mock
    if sys.platform == 'darwin' and getattr(sys, 'frozen', False):
        sys.modules['mac_native_runtime'] = importlib.import_module('relay_bridge_bundle.mac_native_runtime')
    checks = {}
    execution = {'id': 'probe-state', 'threadRuntimeStatus': {'type': 'active', 'activeFlags': []}, 'turns': [{'turnId': 'probe-turn', 'status': 'inProgress', 'items': [{'id': 'probe-user', 'type': 'userMessage', 'text': 'probe'}]}]}
    public = core.view_state(execution, 1, 'probe-owner')
    checks['execution_waiting_not_fake_thinking'] = public['executionState']['phase'] == 'waiting_output' and (not public['executionState']['hasOutput'])
    execution['threadRuntimeStatus']['activeFlags'] = ['waitingOnUserInput']
    checks['execution_native_wait_flags'] = core.view_state(execution, 2, 'probe-owner')['executionState']['phase'] == 'waiting_input'
    execution['threadRuntimeStatus']['type'] = 'idle'
    execution['threadRuntimeStatus']['activeFlags'] = []
    execution['turns'][0]['status'] = 'interrupted'
    checks['execution_native_stop'] = core.view_state(execution, 3, 'probe-owner')['executionState']['phase'] == 'interrupted'
    capacity = {'id': 'capacity-chat', 'threadRuntimeStatus': {'type': 'idle'}, 'turns': [{'turnId': 'capacity-turn', 'status': 'failed', 'error': {'codexErrorInfo': 'serverOverloaded', 'message': 'Selected model is at capacity.'}, 'items': [{'id': 'user', 'type': 'userMessage', 'text': 'Task'}]}]}
    public = core.view_state(capacity, 1, 'probe-owner')
    checks['capacity_native_continue'] = public['canContinue'] and public['executionState']['phase'] == 'capacity_wait'
    checks['capacity_no_fake_countdown'] = public['capacityRetry']['automatic'] is None and (not public['capacityRetry']['countdownAvailable'])
    observed = {'status': 'capacity-observed', 'turnId': 'capacity-turn', 'automatic': True, 'retryAtMs': 1791880000000}
    checks['capacity_exact_original_deadline'] = core.view_state(capacity, 2, 'probe-owner', capacity_observation=observed)['capacityRetry']['retryAtMs'] == observed['retryAtMs']
    with tempfile.TemporaryDirectory(prefix='relay-question-probe-') as directory:
        agent = core.HostAgent({'state_dir': directory, 'codex_home': str(Path(directory) / 'codex')})
        agent.ipc = Mock(connected=True)
        agent.ipc.request.side_effect = lambda method, params, **kw: {'handledByClientId': 'probe-owner'} if method == 'thread-owner-discovery' else {'method': method, 'result': {'result': {'turnId': 'probe-turn'}}}
        question = {'id': '中文-question', 'type': 'agentMessage', 'questions': [{'title': '选方案', 'options': ['A', 'B']}, {'title': '补充说明'}]}
        state = {'id': 'probe-chat', 'threadRuntimeStatus': {'type': 'active'}, 'turns': [{'turnId': 'probe-turn', 'items': [question]}]}
        agent.states['probe-chat'] = {'owner': 'probe-owner', 'state': state}
        for status, method in [('active', 'thread-follower-steer-turn'), ('idle', 'thread-follower-start-turn')]:
            state['threadRuntimeStatus']['type'] = status
            agent.execute({'id': 'probe-reply', 'thread': 'probe-chat', 'action': 'answer_choice', 'text': json.dumps({'itemId': question['id'], 'answers': ['B', '自定义\n第二行']})})
            call = agent.ipc.request.call_args
            params = call.args[1]
            inputs = params['input'] if status == 'active' else params['turnStart']['request']['input']
            replies = core.async_question_replies(inputs[0]['text'])
            checks[status + '_native_reply'] = call.args[0] == method and [r['questionItemId'] for r in replies] == ['["request_user_input_async","中文-question",0]', '["request_user_input_async","中文-question",1]'] and ([r['answer'] for r in replies] == ['B', '自定义\n第二行'])
            public = core.normalize_item({'id': 'reply', 'type': 'steeringUserMessage', 'input': inputs}, 'reply')
            checks['readable_answer_no_internal_tags'] = 'send_user_message_question_reply' not in public['text'] and public['questionReplyTo'] == [r['questionItemId'] for r in replies]
    return checks
