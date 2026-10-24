"""Small private-tailnet relay. No model runtime and no Codex credentials here."""
import argparse
import atexit
import base64
from contextlib import contextmanager, closing
import gzip
import hashlib
import hmac
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import io
import ipaddress
import mimetypes
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import secrets
import socket
import sqlite3
import struct
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
import uuid
import zlib
if os.name != 'nt':
    import fcntl
    import pty
    import termios
from crb.files.file_access import SshFileManager
from crb.paths import web_root
from desktop_client import PRODUCT_NAME, VERSION as PROGRAM_VERSION
from crb.server.panel_restart import RestartManager
from crb.common.client_pairing import ClientPairing, verify_panel_login
from crb.common.feedback import issue_form_url
from crb.server.persistence import relay_database
HOST_ONLINE_TTL = 45
RESPONSE_GZIP_MIN_BYTES = 4 * 1024
SSH_TERMINAL_BUFFER_LIMIT = 2 * 1024 * 1024
SSH_TERMINAL_READ_LIMIT = 128 * 1024
MAX_SSH_TERMINALS = 24

def page_reload_nonce(params):
    values = params.get('_crb_reload', [])
    value = values[0] if len(values) == 1 else ''
    return value if re.fullmatch('[a-zA-Z0-9_-]{1,64}', value) else ''

def page_reload_html(data, nonce):
    """Only an explicit recovery navigation bypasses each local asset cache."""
    if not nonce:
        return data
    return re.sub(b'((?:src|href)="/lite/[^"?]+)(")', lambda match: match[1] + b'?_crb_reload=' + nonce.encode('ascii') + match[2], data)

def response_content_coding(accept_encoding, content_type='application/json'):
    """Negotiate standard HTTP compression without rewriting historical data.

    Prefer the zlib-wrapped deflate coding for JSON at equal quality. Some web
    scanners misclassify legitimate command-history JSON inside a gzip archive.
    Gzip-only consumers keep their existing wire format. Never force a coding
    the client has refused, and do not turn JSON into an opaque encoded envelope.
    """
    qualities = {}
    for entry in accept_encoding.split(','):
        parts = [part.strip().lower() for part in entry.split(';')]
        coding = parts[0]
        if not coding:
            continue
        quality = 1.0
        for parameter in parts[1:]:
            if parameter.startswith('q='):
                value = parameter[2:]
                if not re.fullmatch('(?:0(?:\\.\\d{0,3})?|1(?:\\.0{0,3})?)', value):
                    quality = 0.0
                    break
                quality = min(quality, float(value))
        qualities[coding] = min(quality, qualities.get(coding, 1.0))
    mime = content_type.split(';', 1)[0].strip().lower()
    candidates = ('deflate', 'gzip') if mime == 'application/json' or mime.endswith('+json') else ('gzip',)
    selected, best = ('', 0.0)
    for coding in candidates:
        quality = qualities.get(coding, qualities.get('*', 0.0))
        if quality > best:
            selected, best = (coding, quality)
    return selected

def compress_response_body(body, coding):
    """Use RFC 9110 deflate (a zlib wrapper), not a raw DEFLATE stream."""
    if coding == 'deflate':
        return zlib.compress(body, level=5)
    if coding == 'gzip':
        return gzip.compress(body, compresslevel=5, mtime=0)
    if coding:
        raise ValueError('Unsupported response content coding')
    return body

def allow_late_native_receipt(command_id, thread_id, action, previous, incoming):
    """An authenticated host may correct UNKNOWN, never an explicit rejection.

    Task failure is separate from message acceptance. Require the exact native
    message identity and original conversation, not text or active status.
    """
    if action not in ('send', 'steer', 'answer_choice', 'queue_send_now'):
        return False
    if not isinstance(previous, dict) or previous.get('unknown') is not True:
        return False
    if not isinstance(incoming, dict) or incoming.get('ok') is not True:
        return False
    if incoming.get('confirmedBy') != 'native-stream' or incoming.get('threadId') != thread_id:
        return False
    receipt = incoming.get('nativeReceipt')
    native_id = previous.get('nativeMessageId') if action == 'queue_send_now' else command_id
    return isinstance(native_id, str) and 0 < len(native_id) <= 200 and isinstance(receipt, dict) and (receipt.get('clientUserMessageId') == native_id) and all((isinstance(receipt.get(key), str) and 0 < len(receipt[key]) <= 200 for key in ('itemId', 'turnId')))
PUBLIC_SENSITIVE_KEYS = {'token', 'tokens', 'access_token', 'refresh_token', 'id_token', 'api_key', 'apikey', 'openai_api_key', 'password', 'secret', 'credentials', 'credential', 'agent_private_key', 'raw_data', 'email', 'account_id', 'current_account_id', 'user_id', 'organization_id'}
PUBLIC_SENSITIVE_KEY_FORMS = {key.replace('_', '') for key in PUBLIC_SENSITIVE_KEYS}
PUBLIC_SECRET_TEXT = re.compile('(?i)(?:\\b(?:sk|at)-[A-Za-z0-9._-]{12,}\\b|\\beyJ[A-Za-z0-9_-]{12,}\\.[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}\\b|\\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}\\b)')

class RelayHTTPServer(ThreadingHTTPServer):

    def __init__(self, server_address, RequestHandlerClass, bind_and_activate=True):
        try:
            ipv6 = ipaddress.ip_address(server_address[0]).version == 6
        except (ValueError, TypeError):
            ipv6 = False
        self.address_family = socket.AF_INET6 if ipv6 else socket.AF_INET
        super().__init__(server_address, RequestHandlerClass, bind_and_activate)

    def server_bind(self):
        from socketserver import TCPServer
        TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = self.server_address[1]
    'Absorb short agent/browser connection bursts without dropping SYNs.'
    request_queue_size = 128
    daemon_threads = True

def validate_public_data(value, path='optional provider'):
    """Reject secrets at the relay boundary; target-only credentials stay target-only."""
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).replace('-', '_').lower()
            collapsed = normalized.replace('_', '')
            if normalized in PUBLIC_SENSITIVE_KEYS or collapsed in PUBLIC_SENSITIVE_KEY_FORMS or normalized.endswith('_secret') or collapsed.endswith('secret'):
                raise ValueError(f'{path} 包含禁止传输的敏感字段')
            validate_public_data(item, path + '.' + str(key))
    elif isinstance(value, list):
        for item in value:
            validate_public_data(item, path)
    elif isinstance(value, str) and PUBLIC_SECRET_TEXT.search(value):
        raise ValueError(f'{path} 包含疑似凭据')

class SshTerminalSession:
    """One real SSH connection backed by a local PTY on the relay host."""

    def __init__(self, session_id, host_id, config, cwd, cols, rows, ssh_binary):
        self.id = session_id
        self.host_id = host_id
        self.config = config
        self.cwd = str(cwd or '')
        self.cols = cols
        self.rows = rows
        self.shell = str(config.get('shell') or 'SSH')
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.buffer = bytearray()
        self.base_offset = 0
        self.total_offset = 0
        self.created_at = time.time()
        self.ordinal = 0
        self.last_used = time.monotonic()
        self.closed = False
        self.exit_code = None
        self.master_fd = None
        self.process = None
        self.command = self._build_command(ssh_binary)
        self._start()

    def _build_command(self, ssh_binary):
        hostname = str(self.config.get('hostname') or '').strip()
        user = str(self.config.get('user') or '').strip()
        if not hostname or not user:
            raise ValueError('SSH 目标缺少 hostname 或 user')
        port = min(max(int(self.config.get('port', 22)), 1), 65535)
        command = [str(ssh_binary), '-F', os.devnull, '-tt', '-p', str(port), '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3', '-o', 'LogLevel=ERROR']
        identity = str(self.config.get('identity_file') or '').strip()
        if identity:
            identity_path = Path(identity).expanduser().resolve()
            if not identity_path.is_file():
                raise RuntimeError('服务器 上没有找到该电脑的 SSH 私钥')
            command.extend(['-i', str(identity_path)])
        known_hosts = str(self.config.get('known_hosts_file') or '').strip()
        if known_hosts:
            known_hosts_path = Path(known_hosts).expanduser().resolve()
            if not known_hosts_path.is_file():
                raise RuntimeError('服务器 上没有找到 SSH 主机指纹文件')
            command.extend(['-o', f'UserKnownHostsFile={known_hosts_path}', '-o', 'StrictHostKeyChecking=yes'])
        command.append(f'{user}@{hostname}')
        kind = str(self.config.get('kind') or 'posix').lower()
        if kind == 'windows':
            remote = 'pwsh.exe -NoLogo -NoProfile -WindowStyle Hidden -NoExit'
            if self.cwd:
                escaped = self.cwd.replace("'", "''").replace('\r', '').replace('\n', '')
                bootstrap = "if (Test-Path -LiteralPath '" + escaped + "' -PathType Container) { Set-Location -LiteralPath '" + escaped + "' }"
                encoded = base64.b64encode(bootstrap.encode('utf-16-le')).decode('ascii')
                remote += ' -EncodedCommand ' + encoded
            command.append(remote)
        elif self.cwd:
            safe_cwd = shlex.quote(self.cwd.replace('\r', '').replace('\n', ''))
            command.append(f'cd -- {safe_cwd} 2>/dev/null || cd; exec "${{SHELL:-/bin/sh}}" -l')
        return command

    def _start(self):
        if os.name == 'nt':
            from crb.terminal.windows_pty import WindowsPtyProcess
            self.process = WindowsPtyProcess(self.command, self.rows, self.cols)
            threading.Thread(target=self._reader, daemon=True, name=f'relay-ssh-{self.id[:8]}').start()
            return
        master_fd, slave_fd = pty.openpty()
        self.master_fd = master_fd
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack('HHHH', self.rows, self.cols, 0, 0))
        env = dict(os.environ)
        env.setdefault('TERM', 'xterm-256color')
        env.setdefault('COLORTERM', 'truecolor')
        try:
            self.process = subprocess.Popen(self.command, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, env=env, close_fds=True, start_new_session=True)
        finally:
            os.close(slave_fd)
        threading.Thread(target=self._reader, daemon=True, name=f'relay-ssh-{self.id[:8]}').start()

    def _reader(self):
        try:
            while True:
                try:
                    chunk = self.process.read() if os.name == 'nt' else os.read(self.master_fd, 65536)
                except (OSError, EOFError):
                    break
                if not chunk:
                    break
                with self.changed:
                    self.buffer.extend(chunk)
                    self.total_offset += len(chunk)
                    if len(self.buffer) > SSH_TERMINAL_BUFFER_LIMIT:
                        drop = len(self.buffer) - SSH_TERMINAL_BUFFER_LIMIT
                        del self.buffer[:drop]
                        self.base_offset += drop
                    self.changed.notify_all()
        finally:
            code = self.process.poll()
            if code is None:
                try:
                    code = self.process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    code = None
            with self.changed:
                self.exit_code = code
                self.changed.notify_all()

    def read(self, offset, wait_ms=0):
        wait_seconds = min(max(int(wait_ms), 0), 2000) / 1000
        deadline = time.monotonic() + wait_seconds
        with self.changed:
            self.last_used = time.monotonic()
            while offset >= self.total_offset and self.process.poll() is None and (not self.closed) and (time.monotonic() < deadline):
                self.changed.wait(deadline - time.monotonic())
            truncated = offset < self.base_offset
            start = max(offset, self.base_offset)
            relative = min(max(start - self.base_offset, 0), len(self.buffer))
            chunk = bytes(self.buffer[relative:relative + SSH_TERMINAL_READ_LIMIT])
            return {'data': base64.b64encode(chunk).decode(), 'offset': start + len(chunk), 'truncated': truncated, 'baseOffset': self.base_offset, 'totalOffset': self.total_offset, 'exited': self.process.poll() is not None, 'exitCode': self.process.poll(), 'shell': self.shell, 'cwd': self.cwd, 'transport': 'ssh'}

    def write(self, data):
        if not data:
            return 0
        with self.lock:
            if self.closed or self.process.poll() is not None:
                raise RuntimeError('SSH 终端已经结束')
            self.last_used = time.monotonic()
        written = 0
        if os.name == 'nt':
            return self.process.write(data)
        while written < len(data):
            written += os.write(self.master_fd, data[written:])
        return written

    def resize(self, cols, rows):
        with self.lock:
            self.cols, self.rows = (cols, rows)
            self.last_used = time.monotonic()
            if os.name == 'nt':
                self.process.resize(rows, cols)
                return
            if self.master_fd is not None:
                fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))

    def close(self):
        with self.changed:
            if self.closed:
                return
            self.closed = True
            self.changed.notify_all()
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate() if os.name == 'nt' else os.killpg(self.process.pid, signal.SIGHUP)
                self.process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self.process.kill()
                except OSError:
                    pass
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass

class SshTerminalManager:

    def __init__(self, config):
        self.hosts = config.get('ssh_hosts', {})
        self.ssh_binary = config.get('ssh_binary') or shutil.which('ssh') or 'ssh'
        self.sessions = {}
        self.lock = threading.RLock()

    def host_info(self, host_id):
        config = self.hosts.get(host_id)
        if not config:
            return {'supported': False, 'transport': 'ssh'}
        return {'supported': True, 'transport': 'ssh', 'shell': str(config.get('shell') or 'SSH'), 'kind': str(config.get('kind') or 'posix')}

    def reap(self):
        now = time.monotonic()
        with self.lock:
            expired = [session_id for session_id, session in self.sessions.items() if now - session.last_used > (3600 if session.process.poll() is not None else 12 * 3600)]
            sessions = [self.sessions.pop(session_id) for session_id in expired]
        for session in sessions:
            session.close()

    def session(self, session_id, host_id=''):
        try:
            uuid.UUID(session_id)
        except (ValueError, TypeError) as exc:
            raise ValueError('SSH 终端会话标识无效') from exc
        with self.lock:
            session = self.sessions.get(session_id)
        if not session:
            raise ValueError('SSH 终端已关闭或 服务器 服务已重启')
        if host_id and session.host_id != host_id:
            raise ValueError('SSH 终端不属于所选电脑')
        return session

    def execute(self, data):
        self.reap()
        action = str(data.get('action') or '')
        host_id = str(data.get('host') or '')
        payload = data.get('payload', {})
        if not isinstance(payload, dict):
            raise ValueError('SSH 终端参数格式错误')
        if action == 'list':
            with self.lock:
                sessions = sorted(self.sessions.values(), key=lambda item: (item.created_at, item.id))
                return {'ok': True, 'sessions': [{'sessionId': item.id, 'host': item.host_id, 'shell': item.shell, 'cwd': item.cwd, 'ordinal': item.ordinal, 'createdAt': item.created_at, 'exited': item.process.poll() is not None, 'cols': item.cols, 'rows': item.rows} for item in sessions]}
        if action == 'start':
            config = self.hosts.get(host_id)
            if not config:
                raise ValueError('该电脑尚未配置 SSH 终端')
            with self.lock:
                active = sum((session.process.poll() is None for session in self.sessions.values()))
            if active >= MAX_SSH_TERMINALS:
                raise RuntimeError(f'最多同时保留 {MAX_SSH_TERMINALS} 个 SSH 终端')
            cols = min(max(int(payload.get('cols', 100)), 20), 500)
            rows = min(max(int(payload.get('rows', 30)), 5), 200)
            cwd = str(payload.get('cwd') or '')[:4096]
            session_id = str(uuid.uuid4())
            session = SshTerminalSession(session_id, host_id, config, cwd, cols, rows, self.ssh_binary)
            with self.lock:
                session.ordinal = 1 + max((item.ordinal for item in self.sessions.values() if item.host_id == host_id), default=0)
                self.sessions[session_id] = session
            return {'ok': True, 'sessionId': session_id, 'shell': session.shell, 'cwd': session.cwd, 'transport': 'ssh', 'offset': 0, 'confirmedAt': time.time()}
        session_id = str(payload.get('sessionId') or '')
        session = self.session(session_id, host_id)
        if action == 'read':
            return {'ok': True, 'sessionId': session.id, **session.read(max(int(payload.get('offset', 0)), 0), payload.get('waitMs', 0))}
        if action == 'write':
            encoded = str(payload.get('data') or '')
            if len(encoded) > 96 * 1024:
                raise ValueError('单次 SSH 终端输入过长')
            try:
                raw = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError) as exc:
                raise ValueError('SSH 终端输入格式错误') from exc
            if len(raw) > 64 * 1024:
                raise ValueError('单次 SSH 终端输入过长')
            return {'ok': True, 'sessionId': session.id, 'written': session.write(raw), 'transport': 'ssh', 'confirmedAt': time.time()}
        if action == 'resize':
            cols = min(max(int(payload.get('cols', session.cols)), 20), 500)
            rows = min(max(int(payload.get('rows', session.rows)), 5), 200)
            session.resize(cols, rows)
            return {'ok': True, 'sessionId': session.id, 'cols': cols, 'rows': rows, 'transport': 'ssh', 'confirmedAt': time.time()}
        if action == 'close':
            with self.lock:
                self.sessions.pop(session.id, None)
            session.close()
            return {'ok': True, 'sessionId': session.id, 'closed': True, 'transport': 'ssh', 'confirmedAt': time.time()}
        raise ValueError('不支持的 SSH 终端操作')

    def close_all(self):
        with self.lock:
            sessions = list(self.sessions.values())
            self.sessions.clear()
        for session in sessions:
            session.close()

class Relay:

    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.version = 0
        self.generation = str(uuid.uuid4())
        self.hosts = {}
        self.catalog_versions = {}
        self.removed_host_events = []
        self.secure_commands = {}
        self.lite_views = {}
        self.live_output_demands = {}
        self.failures = {}
        self.setup_failures = {}
        self.ssh_terminals = SshTerminalManager(cfg)
        self.ssh_files = SshFileManager(cfg)
        self.dbpath = relay_database(cfg['state_dir'])
        self.panel_restart = RestartManager(cfg.get('_config_path') if sys.platform.startswith('linux') and (not cfg.get('_native_manager')) else None, cfg['state_dir'])
        self.dbpath.parent.mkdir(parents=True, exist_ok=True, mode=448)
        self.client_pairing = ClientPairing(self.dbpath, cfg['session_secret'], cfg['agents'])
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, host TEXT, thread TEXT, action TEXT, text TEXT, created REAL, result TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS traffic_sessions (id INTEGER PRIMARY KEY, started REAL NOT NULL, ended REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS traffic_minutes (session_id INTEGER NOT NULL, minute INTEGER NOT NULL, upload_bytes INTEGER NOT NULL DEFAULT 0, download_bytes INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(session_id, minute))')
            db.execute('CREATE TABLE IF NOT EXISTS ui_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS workspace_threads (host TEXT NOT NULL, thread TEXT NOT NULL, title TEXT NOT NULL, host_label TEXT NOT NULL, created REAL NOT NULL, sort_order INTEGER NOT NULL, PRIMARY KEY(host,thread))')
            if 'sort_order' not in {row[1] for row in db.execute('PRAGMA table_info(workspace_threads)')}:
                db.execute('ALTER TABLE workspace_threads ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0')
                old_order = db.execute('SELECT host,thread FROM workspace_threads ORDER BY created,rowid').fetchall()
                db.executemany('UPDATE workspace_threads SET sort_order=? WHERE host=? AND thread=?', [(index, host, thread) for index, (host, thread) in enumerate(old_order)])
            self.workspace_threads = self.read_workspace_threads(db)
            saved_title = db.execute("SELECT value FROM ui_settings WHERE key='title'").fetchone()
            self.ui_title = saved_title[0] if saved_title and saved_title[0] != 'Codex Relay' else PRODUCT_NAME
            if not db.execute('SELECT 1 FROM traffic_sessions WHERE ended IS NULL').fetchone():
                db.execute('INSERT INTO traffic_sessions(started) VALUES (?)', (time.time(),))
            db.execute("UPDATE commands SET result=? WHERE result IS NULL AND action!='watch'", (json.dumps({'ok': False, 'unknown': True, 'error': '网页服务已重启，请核对原对话后再决定是否发送'}),))
            db.execute("DELETE FROM commands WHERE action='watch'")
        atexit.register(self.ssh_terminals.close_all)

    @staticmethod
    def lite_item_key(item):
        return f"{item.get('turnId', '')}/{item.get('id', '')}"

    def live_output_demand(self, host, tid=None, wanted=None):
        """Ephemeral foreground interest, independent of command/history storage."""
        now = time.monotonic()
        with self.lock:
            demands = self.live_output_demands
            for key, expiry in list(demands.items()):
                state = self.hosts.get(key[0], {})
                view = state.get('views', {}).get(key[1], {})
                if expiry <= now or not state.get('capabilities', {}).get('nativeLiveOutput') or (not state.get('desktopConnected')) or (time.time() - state.get('seen', 0) >= HOST_ONLINE_TTL) or (not view.get('owned')) or (view.get('status') != 'active') or (not view.get('executionState', {}).get('activeCommandIds')):
                    demands.pop(key, None)
            if tid is not None and wanted is not None:
                key = (host, tid)
                demands.pop(key, None)
                state = self.hosts.get(host, {})
                view = state.get('views', {}).get(tid, {})
                if wanted and state.get('capabilities', {}).get('nativeLiveOutput') is True and state.get('desktopConnected') and (time.time() - state.get('seen', 0) < HOST_ONLINE_TTL) and view.get('owned') and (view.get('source') == 'desktop-live') and (view.get('status') == 'active') and view.get('executionState', {}).get('activeCommandIds'):
                    demands[key] = now + 8
            keys = [key for key in demands if key[0] == host]
            for key in keys[:-4]:
                demands.pop(key, None)
            while len(demands) > 128:
                demands.pop(next(iter(demands)))
            return [{'threadId': key[1], 'ttl': min(8, expiry - now)} for key, expiry in demands.items() if key[0] == host]

    def update_lite_view(self, host, tid, view):
        """Record a stable view and the smallest safe patch from its predecessor."""
        if not isinstance(view, dict):
            return
        clean = {key: value for key, value in view.items() if key != 'updated'}
        records = self.lite_views.setdefault(host, {})
        previous = records.get(tid)
        if previous is None:
            records[tid] = {'revision': 1, 'view': clean, 'patches': []}
            return
        old = previous['view']
        if old == clean:
            return
        old_items = {self.lite_item_key(item): item for item in old.get('items', []) if isinstance(item, dict)}
        new_items = {self.lite_item_key(item): item for item in clean.get('items', []) if isinstance(item, dict)}
        upserts, updates = ([], [])
        for key, item in new_items.items():
            before = old_items.get(key)
            if before is None:
                upserts.append({'key': key, 'value': item})
                continue
            if before == item:
                continue
            set_fields, append_fields, unset_fields = ({}, {}, [])
            for field, value in item.items():
                old_value = before.get(field)
                if old_value == value:
                    continue
                if isinstance(value, str) and isinstance(old_value, str) and old_value and value.startswith(old_value):
                    append_fields[field] = value[len(old_value):]
                else:
                    set_fields[field] = value
            unset_fields = [field for field in before if field not in item]
            updates.append({'key': key, 'set': set_fields, 'append': append_fields, 'unset': unset_fields})
        old_order = [self.lite_item_key(item) for item in old.get('items', []) if isinstance(item, dict)]
        new_order = [self.lite_item_key(item) for item in clean.get('items', []) if isinstance(item, dict)]
        revision = previous['revision'] + 1
        patch = {'revision': revision, 'meta': {key: value for key, value in clean.items() if key != 'items'}, 'upserts': upserts, 'updates': updates, 'removed': [key for key in old_items if key not in new_items]}
        if old_order != new_order:
            patch['order'] = new_order
        previous['revision'] = revision
        previous['view'] = clean
        previous['patches'] = (previous['patches'] + [patch])[-120:]

    def lite_thread(self, host, tid, since=0):
        record = self.lite_views.get(host, {}).get(tid)
        if record is None:
            view = self.hosts.get(host, {}).get('views', {}).get(tid)
            if not isinstance(view, dict):
                return {'mode': 'full', 'revision': 0, 'thread': None}
            self.update_lite_view(host, tid, view)
            record = self.lite_views[host][tid]
        revision = record['revision']
        if since == revision:
            return {'mode': 'unchanged', 'revision': revision}
        patches = [patch for patch in record['patches'] if patch['revision'] > since]
        if since > 0 and patches and (patches[0]['revision'] == since + 1) and (patches[-1]['revision'] == revision):
            return {'mode': 'patch', 'revision': revision, 'patches': patches}
        return {'mode': 'full', 'revision': revision, 'thread': record['view']}

    def preload_thread(self, host, tid, since=0, generation='', accepts_gzip=False, content_coding=''):
        """Read only an already-cached view, with a hard transfer-size limit.

        Never queue watch/read commands or fetch Desktop/files on a cache miss.
        Large or poorly compressible snapshots wait for a real foreground click.
        """
        h = self.hosts.get(host, {})
        available = tid in self.lite_views.get(host, {}) and any((item.get('id') == tid for item in h.get('catalog', [])))
        if not available:
            result = {'mode': 'unavailable', 'generation': self.generation}
        else:
            if generation and generation != self.generation:
                since = 0
            result = self.lite_thread(host, tid, since)
            result.update({'generation': self.generation, 'online': bool(time.time() - h.get('seen', 0) < HOST_ONLINE_TTL and h.get('desktopConnected', False))})
        body = json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()
        wire = compress_response_body(body, content_coding or ('gzip' if accepts_gzip else ''))
        if len(body) > 512 * 1024 or len(wire) > 64 * 1024:
            body = json.dumps({'mode': 'deferred', 'generation': self.generation}).encode()
        return body

    @staticmethod
    def stable_lite_value(value):
        """Remove poll timestamps that change without changing anything visible."""
        if isinstance(value, dict):
            return {key: Relay.stable_lite_value(item) for key, item in value.items() if key not in ('lastCheckedAt', 'last_checked_at')}
        if isinstance(value, list):
            return [Relay.stable_lite_value(item) for item in value]
        return value

    def public_hosts(self):
        hosts = []
        for hid, host in self.hosts.items():
            threads = []
            for thread in host['catalog']:
                view = host['views'].get(thread['id'], {})
                owned = bool(view.get('owned'))
                threads.append({**thread, 'status': view.get('status') or thread.get('status', 'notLoaded') if owned else thread.get('status', 'notLoaded'), 'pendingRequests': view.get('pendingRequests', 0) if owned else 0, 'unread': thread.get('unread') if thread.get('unread') is not None else view.get('unread'), 'owned': owned})
            result = {'id': hid, 'label': host['label'], 'applicationId': host.get('applicationId', 'codex'), 'physicalHostId': host.get('physicalHostId', hid), 'capabilities': host.get('capabilities', {}), 'online': time.time() - host['seen'] < HOST_ONLINE_TTL, 'desktopConnected': host['desktopConnected'], 'terminal': host.get('terminal', {}) if host.get('terminal', {}).get('transport') == 'client' else self.ssh_terminals.host_info(host.get('physicalHostId', hid)), 'projects': host.get('projects', []), 'models': host.get('models', []), 'threads': threads}
            hosts.append(result)
        return hosts

    @staticmethod
    def catalog_patch(before, after):
        if [host['id'] for host in before] != [host['id'] for host in after]:
            return None
        patches = []
        for previous, current in zip(before, after):
            old_threads = previous.get('threads', [])
            new_threads = current.get('threads', [])
            if [item['id'] for item in old_threads] != [item['id'] for item in new_threads]:
                return None
            changed = [item for old, item in zip(old_threads, new_threads) if old != item]
            set_fields = {key: value for key, value in current.items() if key != 'threads' and previous.get(key) != value}
            if changed or set_fields:
                patches.append({'id': current['id'], 'set': set_fields, 'threads': changed})
        return patches

    @contextmanager
    def db(self):
        with closing(sqlite3.connect(self.dbpath, timeout=15)) as db:
            with db:
                yield db

    def set_ui_title(self, value):
        if not isinstance(value, str):
            raise ValueError('请输入工作台名称')
        title = value.strip()
        if not 1 <= len(title) <= 40 or not title.isprintable():
            raise ValueError('工作台名称应为 1–40 个可显示字符')
        with self.lock, self.db() as db:
            db.execute("INSERT INTO ui_settings(key,value) VALUES ('title',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (title,))
            self.ui_title = title
        self.signal()
        return title

    @staticmethod
    def read_workspace_threads(db):
        return [{'host': host, 'thread': thread, 'title': title, 'hostLabel': label} for host, thread, title, label in db.execute('SELECT host,thread,title,host_label FROM workspace_threads ORDER BY sort_order,created,rowid')]

    def update_workspace_thread(self, request):
        action = request.get('action')
        host_id, thread_id = (request.get('host'), request.get('thread'))
        if action not in ('add', 'remove', 'move'):
            raise ValueError('不支持的工作区操作')
        if any((not isinstance(value, str) or not 1 <= len(value) <= 200 or (not value.isprintable()) for value in (host_id, thread_id))):
            raise ValueError('工作区对话标识无效')
        with self.lock, self.db() as db:
            before = self.workspace_threads
            if action == 'add':
                host = self.hosts.get(host_id)
                thread = next((item for item in (host or {}).get('catalog', []) if item.get('id') == thread_id), None)
                fork_command_id = request.get('forkCommandId')
                if fork_command_id is not None:
                    if not isinstance(fork_command_id, str):
                        raise ValueError('分支确认标识无效')
                    uuid.UUID(fork_command_id)
                    row = db.execute('SELECT host,action,result FROM commands WHERE id=?', (fork_command_id,)).fetchone()
                    confirmed = json.loads(row[2]) if row and row[2] else {}
                    if not row or row[0] != host_id or row[1] != 'fork_thread' or (not isinstance(confirmed, dict)) or (confirmed.get('ok') is not True) or (confirmed.get('threadId') != thread_id):
                        raise ValueError('分支尚未确认，无法加入工作区')
                    if thread is None:
                        thread = {'id': thread_id, 'title': confirmed.get('title') or '新分支对话'}
                if not host or not thread:
                    raise ValueError('没有找到要加入工作区的对话')
                exists = db.execute('SELECT 1 FROM workspace_threads WHERE host=? AND thread=?', (host_id, thread_id)).fetchone()
                if not exists and len(before) >= 500:
                    raise ValueError('工作区最多保留 500 条对话链接')
                next_order = db.execute('SELECT COALESCE(MAX(sort_order),-1)+1 FROM workspace_threads').fetchone()[0]
                db.execute('INSERT INTO workspace_threads(host,thread,title,host_label,created,sort_order) VALUES (?,?,?,?,?,?) ON CONFLICT(host,thread) DO UPDATE SET title=excluded.title,host_label=excluded.host_label', (host_id, thread_id, str(thread.get('title') or '未命名对话')[:500], str(host.get('label') or host_id)[:200], time.time(), next_order))
            elif action == 'remove':
                db.execute('DELETE FROM workspace_threads WHERE host=? AND thread=?', (host_id, thread_id))
            else:
                target = (request.get('targetHost'), request.get('targetThread'))
                after = request.get('after', False)
                if any((not isinstance(value, str) or not 1 <= len(value) <= 200 or (not value.isprintable()) for value in target)) or not isinstance(after, bool):
                    raise ValueError('工作区排序目标无效')
                order = [(item['host'], item['thread']) for item in self.read_workspace_threads(db)]
                source = (host_id, thread_id)
                if source not in order or target not in order:
                    raise ValueError('工作区对话已移除，请刷新列表后重试')
                if source != target:
                    order.remove(source)
                    order.insert(order.index(target) + int(after), source)
                    db.executemany('UPDATE workspace_threads SET sort_order=? WHERE host=? AND thread=?', [(index, host, thread) for index, (host, thread) in enumerate(order)])
            self.workspace_threads = self.read_workspace_threads(db)
            changed = before != self.workspace_threads
            result = list(self.workspace_threads)
        if changed:
            self.signal()
        return result

    def record_traffic(self, upload_bytes=0, download_bytes=0):
        """Count public-edge application payload, not agent sync or local clients."""
        upload_bytes, download_bytes = (max(0, int(upload_bytes)), max(0, int(download_bytes)))
        if not upload_bytes and (not download_bytes):
            return
        with self.lock, self.db() as db:
            sid = db.execute('SELECT id FROM traffic_sessions WHERE ended IS NULL ORDER BY id DESC LIMIT 1').fetchone()[0]
            db.execute('INSERT INTO traffic_minutes(session_id,minute,upload_bytes,download_bytes) VALUES (?,?,?,?) ON CONFLICT(session_id,minute) DO UPDATE SET upload_bytes=upload_bytes+excluded.upload_bytes, download_bytes=download_bytes+excluded.download_bytes', (sid, int(time.time() // 60) * 60, upload_bytes, download_bytes))

    def traffic_report(self, since, until):
        now = time.time()
        since, until = (float(since), float(until))
        if not 0 <= since <= until <= now + 3600 or until - since > 366 * 86400:
            raise ValueError('查询时间范围无效，最多查询一年')
        with self.lock, self.db() as db:
            sid, started = db.execute('SELECT id,started FROM traffic_sessions WHERE ended IS NULL ORDER BY id DESC LIMIT 1').fetchone()
            current = db.execute('SELECT COALESCE(SUM(upload_bytes),0), COALESCE(SUM(download_bytes),0) FROM traffic_minutes WHERE session_id=?', (sid,)).fetchone()
            rows = db.execute("SELECT date(minute,'unixepoch','localtime'),SUM(upload_bytes),SUM(download_bytes) FROM traffic_minutes WHERE minute>=? AND minute<? GROUP BY 1 ORDER BY 1", (int(since // 60) * 60, (int(until // 60) + 1) * 60)).fetchall()
            first = db.execute('SELECT MIN(started) FROM traffic_sessions').fetchone()[0]
        return {'scope': 'public-edge-payload', 'firstRecordedAt': first, 'current': {'startedAt': started, 'uploadBytes': current[0], 'downloadBytes': current[1]}, 'range': {'from': since, 'to': until, 'uploadBytes': sum((r[1] for r in rows)), 'downloadBytes': sum((r[2] for r in rows))}, 'days': [{'date': r[0], 'uploadBytes': r[1], 'downloadBytes': r[2]} for r in rows]}

    def reset_traffic_current(self):
        now = time.time()
        with self.lock, self.db() as db:
            db.execute('UPDATE traffic_sessions SET ended=? WHERE ended IS NULL', (now,))
            db.execute('INSERT INTO traffic_sessions(started) VALUES (?)', (now,))
        return {'ok': True, 'startedAt': now}

    def signal(self, removed_hosts=()):
        with self.changed:
            self.version += 1
            if removed_hosts:
                self.removed_host_events.append((self.version, tuple(removed_hosts)))
                self.removed_host_events = self.removed_host_events[-64:]
            self.changed.notify_all()

    def cookie(self, lifetime=7 * 86400):
        data = f'{int(time.time()) + lifetime}.{secrets.token_hex(12)}'
        sig = hmac.new(self.cfg['session_secret'].encode(), data.encode(), hashlib.sha256).hexdigest()
        return data + '.' + sig

    def authenticated(self, headers):
        edge_token = self.cfg.get('edge_token', '')
        supplied_edge = headers.get('X-Codex-Edge-Token', '')
        if edge_token and supplied_edge and hmac.compare_digest(supplied_edge, edge_token):
            return True
        try:
            cookie = SimpleCookie(headers.get('Cookie', ''))['relay_session'].value
            expires, nonce, sig = cookie.split('.')
            actual = hmac.new(self.cfg['session_secret'].encode(), f'{expires}.{nonce}'.encode(), hashlib.sha256).hexdigest()
            return int(expires) > time.time() and hmac.compare_digest(sig, actual)
        except (KeyError, ValueError):
            return False

    def queue(self, host, tid, action, text='', cid=None):
        if host not in self.cfg['agents'] and (not self.client_pairing.contains(host)):
            raise ValueError('未知设备')
        with self.lock:
            h = self.hosts.get(host)
            if not h or time.time() - h['seen'] > HOST_ONLINE_TTL:
                raise ValueError('目标设备当前离线')
            capabilities = h.get('capabilities', {})
            if 'actions' in capabilities and action not in capabilities['actions']:
                raise ValueError('该功能尚未接入目标应用的原生接口')
            thread_actions = {'watch', 'mark_read', 'send', 'steer', 'archive', 'unarchive', 'move_project', 'update_settings', 'fetch_asset', 'edit_user_turn', 'edit_last_user_turn', 'update_permissions', 'load_older', 'attach_thread', 'rename_thread', 'fork_thread', 'upload_asset_chunk', 'attach_local_file', 'queue_edit', 'queue_delete', 'queue_send_now', 'queue_reorder', 'queue_resume', 'resolve_request', 'answer_choice', 'question_control', 'native_read', 'native_action'}
            if action in thread_actions and (not any((row['id'] == tid for row in h['catalog']))):
                raise ValueError('没有找到该对话')
        cid = cid or str(uuid.uuid4())
        uuid.UUID(cid)
        with self.db() as db:
            old = db.execute('SELECT host,thread,action,text FROM commands WHERE id=?', (cid,)).fetchone()
            if old:
                if old != (host, tid, action, text):
                    raise ValueError('请求标识已被另一条消息使用')
            else:
                db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?,NULL)', (cid, host, tid, action, text, time.time()))
        return cid

    def queue_secure_update(self, host, package, password):
        if package not in ('codex',):
            raise ValueError('不支持的更新目标')
        if not isinstance(password, str) or len(password) > 256:
            raise ValueError('授权密码格式错误')
        if host not in self.cfg['agents'] and (not self.client_pairing.contains(host)):
            raise ValueError('未知设备')
        with self.lock:
            state = self.hosts.get(host)
            if not state or time.time() - state['seen'] > HOST_ONLINE_TTL:
                raise ValueError('目标设备当前离线')
            if any((job['host'] == host and job['result'] is None for job in self.secure_commands.values())):
                raise ValueError('此设备已有更新正在进行')
            now = time.time()
            self.secure_commands = {key: job for key, job in self.secure_commands.items() if now - job['created'] < 3600}
            cid = str(uuid.uuid4())
            self.secure_commands[cid] = {'host': host, 'created': now, 'delivered': False, 'text': json.dumps({'package': package, 'sudoPassword': password}), 'result': None}
            return cid

    def client_access_host(self, host):
        with self.lock:
            if (host in self.cfg['agents'] or self.client_pairing.contains(host)) and self.hosts.get(host, {}).get('terminal', {}).get('transport') == 'client':
                return host
            return next((hid for hid, state in self.hosts.items() if state.get('physicalHostId') == host and (hid in self.cfg['agents'] or self.client_pairing.contains(hid)) and (state.get('terminal', {}).get('transport') == 'client')), None)

    def client_access(self, host, action, request):
        target = self.client_access_host(host)
        if not target:
            raise ValueError('未找到已配对的客户端直连通道')
        cid = self.queue(target, '', action, json.dumps(request, ensure_ascii=False))
        deadline = time.monotonic() + (310 if request.get('action') == 'copy' else 125 if request.get('action') == 'delete' else 60)
        while time.monotonic() < deadline:
            with self.db() as db:
                row = db.execute('SELECT result FROM commands WHERE id=? AND host=?', (cid, target)).fetchone()
            if row and row[0] is not None:
                result = json.loads(row[0])
                if not result.get('ok'):
                    raise ValueError(result.get('error') or '客户端未完成操作')
                return result
            time.sleep(0.1)
        raise ValueError('客户端操作确认超时；请核对目标状态，不会自动重发')

    def terminal_request(self, request):
        if request.get('action') == 'list':
            result = self.ssh_terminals.execute(request)
            with self.lock:
                for hid, host in self.hosts.items():
                    if time.time() - host.get('seen', 0) < HOST_ONLINE_TTL and host.get('terminal', {}).get('transport') == 'client':
                        result['sessions'].extend(host['terminal'].get('sessions', []))
            return result
        host = str(request.get('host') or '')
        sid = (request.get('payload') or {}).get('sessionId')
        with self.ssh_terminals.lock:
            ssh_session = sid in self.ssh_terminals.sessions
        ssh_start = request.get('action') == 'start' and host in self.ssh_terminals.hosts
        if self.client_access_host(host) and (not (ssh_session or ssh_start)):
            result = self.client_access(host, 'client_terminal', request)
            with self.lock:
                target = self.client_access_host(host)
                terminal = self.hosts.get(target, {}).get('terminal', {})
                if request.get('action') == 'close':
                    sid = request.get('payload', {}).get('sessionId')
                    terminal['sessions'] = [s for s in terminal.get('sessions', []) if s.get('sessionId') != sid]
            return result
        return self.ssh_terminals.execute(request)

def handler_factory(app):

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_):
            pass

        def public_edge_request(self):
            expected = app.cfg.get('edge_token', '')
            supplied = self.headers.get('X-Codex-Edge-Token', '')
            return bool(expected and supplied and hmac.compare_digest(expected, supplied))

        def count_public_traffic(self, download_bytes=0, count_request=False):
            if not self.public_edge_request():
                return
            try:
                upload = max(0, int(self.headers.get('Content-Length', '0'))) if count_request else 0
                app.record_traffic(upload, download_bytes)
            except (TypeError, ValueError):
                pass

        def response(self, status, data, content_type='application/json; charset=utf-8', extra=None, cache_control='no-store', compress=False):
            if self.command == 'POST' and (not getattr(self, '_post_body_handled', False)):
                self.discard_post_body()
                extra = {**(extra or {}), 'Connection': 'close'}
            body = json.dumps(data, ensure_ascii=False).encode() if not isinstance(data, bytes) else data
            encoding = ''
            compressible = content_type.startswith('application/json') or content_type.split(';', 1)[0].endswith('+json') or content_type.startswith('application/javascript') or content_type.startswith('text/')
            preferred = response_content_coding(self.headers.get('Accept-Encoding', ''), content_type)
            threshold = 512 if compress else RESPONSE_GZIP_MIN_BYTES
            if status != 304 and compressible and (len(body) >= threshold) and preferred:
                body = compress_response_body(body, preferred)
                encoding = preferred
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', cache_control)
            if encoding:
                self.send_header('Content-Encoding', encoding)
            if compressible:
                self.send_header('Vary', 'Accept-Encoding')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data: blob: https:; font-src 'self' data:; frame-src 'self' blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)
            self.count_public_traffic(len(body) if self.command != 'HEAD' else 0, count_request=True)

        def lite_asset(self, data, content_type, force_reload=False):
            etag = '"' + hashlib.sha256(data).hexdigest()[:24] + '"'
            headers = {'ETag': etag}
            cache_control = 'no-store' if force_reload else 'public, max-age=0, must-revalidate'
            if not force_reload and self.headers.get('If-None-Match') == etag:
                return self.response(304, b'', content_type, headers, cache_control=cache_control)
            return self.response(200, data, content_type, headers, cache_control=cache_control, compress=True)

        def body(self):
            if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
                raise ValueError('请求体长度格式不受支持')
            size = int(self.headers.get('Content-Length', '0'))
            if size < 0 or size > 32 * 1024 * 1024:
                raise ValueError('请求内容过大')
            raw = self.rfile.read(size)
            self._post_body_handled = True
            if len(raw) != size:
                self.close_connection = True
                raise ValueError('请求内容不完整')
            encoding = self.headers.get('Content-Encoding', '').strip().lower()
            if encoding:
                if encoding != 'gzip':
                    raise ValueError('不支持的请求压缩格式')
                with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                    raw = stream.read(64 * 1024 * 1024 + 1)
                if len(raw) > 64 * 1024 * 1024:
                    raise ValueError('解压后的请求内容过大')
            return json.loads(raw)

        def check_origin(self):
            origin = self.headers.get('Origin')
            configured = urlsplit(app.cfg['public_url'])
            allowed = {configured.scheme + '://' + configured.netloc}
            if app.cfg.get('client_login_mode') == 'local':
                address = ipaddress.ip_address(self.connection.getsockname()[0])
                host = '[' + str(address) + ']' if address.version == 6 else str(address)
                authority = host + ':' + str(self.server.server_port)
                if self.headers.get('Host') == authority:
                    allowed.add('http://' + authority)
                local = 'localhost:' + str(self.server.server_port)
                if address.is_loopback and self.headers.get('Host') == local:
                    allowed.add('http://' + local)
            if origin not in allowed:
                self.reject_post(403, {'error': 'Origin 不匹配'})
                return False
            return True

        def discard_post_body(self):
            self.close_connection = True
            if getattr(self, '_post_body_handled', False):
                return
            self._post_body_handled = True
            try:
                size = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                size = -1
            if self.command == 'POST' and (not self.headers.get('Transfer-Encoding')) and (0 < size <= 64 * 1024):
                previous = self.connection.gettimeout()
                try:
                    self.connection.settimeout(2)
                    self.rfile.read(size)
                except (OSError, TimeoutError):
                    pass
                finally:
                    self.connection.settimeout(previous)

        def reject_post(self, status, payload):
            self.discard_post_body()
            return self.response(status, payload, extra={'Connection': 'close'})

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            try:
                url = urlsplit(self.path)
                path, params = (url.path, parse_qs(url.query))
                reload_nonce = page_reload_nonce(params)
                if path in ('/', '/lite', '/lite/'):
                    html = (web_root() / 'index.html').read_bytes()
                    return self.lite_asset(page_reload_html(html, reload_nonce), 'text/html; charset=utf-8', force_reload=bool(reload_nonce))
                if path in ('/lite/app.js', '/lite/question-deck.js', '/lite/requests.js', '/lite/reload.js', '/lite/style.css', '/lite/i18n.js', '/lite/i18n-catalog.js'):
                    name = path.rsplit('/', 1)[-1]
                    asset = web_root() / name
                    if not asset.is_file():
                        return self.response(404, {'error': '没有找到此资源'})
                    mime = {'app.js': 'application/javascript', 'requests.js': 'application/javascript', 'style.css': 'text/css'}.get(name, 'application/javascript')
                    return self.lite_asset(asset.read_bytes(), mime + '; charset=utf-8', force_reload=bool(reload_nonce))
                if path.startswith('/lite/vendor/'):
                    vendor = (web_root() / path.removeprefix('/lite/')).resolve()
                    root = (web_root() / 'vendor').resolve()
                    if not vendor.is_relative_to(root) or not vendor.is_file():
                        return self.response(404, {'error': '没有找到此资源'})
                    mime = mimetypes.guess_type(vendor.name)[0] or 'application/octet-stream'
                    return self.lite_asset(vendor.read_bytes(), mime, force_reload=bool(reload_nonce))
                if path in ('/app.js', '/requests.js', '/style.css') or path.startswith('/vendor/'):
                    return self.response(308, b'', 'text/plain; charset=utf-8', extra={'Location': '/lite' + path})
                if path == '/healthz':
                    return self.response(200, {'ok': True, 'service': 'codex-desktop-relay'})
                if path == '/client/setup':
                    return self.response(200, {'service': 'codex-desktop-relay', 'protocol': 1, 'origin': app.cfg['public_url']})
                if path == '/agent/sync':
                    host, _ = self.agent_identity()
                    if host is None:
                        return self.response(401, {'error': 'Invalid agent credential'})
                    return self.response(200, {'protocol': 1, 'controlHeartbeat': True})
                if path == '/api/ui/title':
                    with app.lock:
                        title = app.ui_title
                    return self.response(200, {'title': title, 'appVersion': PROGRAM_VERSION, 'feedbackUrl': issue_form_url()})
                if not app.authenticated(self.headers):
                    return self.response(401, {'error': '请先登录'})
                if path == '/api/panel/status':
                    with app.ssh_terminals.lock:
                        terminals = sum((s.process.poll() is None for s in app.ssh_terminals.sessions.values()))
                    with app.lock:
                        host_ids = list(app.hosts)
                    return self.response(200, {**app.panel_restart.status(), 'generation': app.generation, 'processId': os.getpid(), 'activeTerminals': terminals, 'hostIds': host_ids})
                if path == '/api/events':
                    return self.response(204, b'', 'text/plain; charset=utf-8')
                if path == '/api/lite/events':
                    return self.events(min_interval=1.0)
                if path in ('/api/catalog', '/api/thread'):
                    return self.response(410, {'error': '旧版接口已停用，请刷新工作台'})
                if path == '/api/lite/catalog':
                    with app.lock:
                        hosts = app.public_hosts()
                        title = app.ui_title
                        workspace = list(app.workspace_threads)
                        encoded = json.dumps({'hosts': hosts, 'uiTitle': title, 'appVersion': PROGRAM_VERSION, 'workspaceThreads': workspace}, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
                        version = hashlib.sha256(encoded).hexdigest()[:20]
                        known = params.get('version', [''])[0]
                        previous = app.catalog_versions.get(known)
                        if version not in app.catalog_versions:
                            app.catalog_versions[version] = (title, json.loads(json.dumps(hosts)), workspace)
                            if len(app.catalog_versions) > 96:
                                del app.catalog_versions[next(iter(app.catalog_versions))]
                        patch = app.catalog_patch(previous[1], hosts) if previous and previous[0] == title else None
                    response = {'version': version, 'uiTitle': title, 'hosts': None if known == version else hosts}
                    if known != version and patch is not None and (params.get('patch', [''])[0] == '1'):
                        candidate = {'baseVersion': known, 'hostPatches': patch}
                        if len(json.dumps(candidate, ensure_ascii=False).encode()) < len(encoded) * 0.8:
                            response = {'version': version, 'uiTitle': title, 'hosts': None, **candidate}
                    response['appVersion'] = PROGRAM_VERSION
                    response['feedbackUrl'] = issue_form_url()
                    response['workspaceThreads'] = None if previous and previous[2] == workspace else workspace
                    response['generation'] = app.generation
                    response['browserScope'] = hmac.new(app.cfg['session_secret'].encode(), ('relay-outbox-v1:' + str(app.cfg.get('username', 'relay'))).encode(), hashlib.sha256).hexdigest()
                    return self.response(200, response, compress=True)
                if path == '/api/traffic':
                    now = time.time()
                    since = params.get('from', [str(now - 86400)])[0]
                    until = params.get('to', [str(now)])[0]
                    return self.response(200, app.traffic_report(since, until), compress=True)
                if path in ('/api/lite/thread', '/api/lite/preload'):
                    host, tid = (params.get('host', [''])[0], params.get('id', [''])[0])
                    try:
                        since = max(0, int(params.get('since', ['0'])[0]))
                    except ValueError:
                        raise ValueError('无效的增量版本')
                    with app.lock:
                        generation = params.get('generation', [''])[0]
                        if path == '/api/lite/preload':
                            coding = response_content_coding(self.headers.get('Accept-Encoding', ''))
                            body = app.preload_thread(host, tid, since, generation, content_coding=coding)
                            return self.response(200, body, compress=True)
                        if generation and generation != app.generation:
                            since = 0
                        h = app.hosts.get(host, {})
                        result = app.lite_thread(host, tid, since)
                        result['generation'] = app.generation
                        result['online'] = bool(time.time() - h.get('seen', 0) < HOST_ONLINE_TTL and h.get('desktopConnected', False))
                        demand = params.get('liveOutput', [''])[0]
                        if self.command == 'GET' and demand in ('0', '1'):
                            app.live_output_demand(host, tid, demand == '1')
                    return self.response(200, result, compress=True)
                if path == '/api/command':
                    cid = params.get('id', [''])[0]
                    with app.db() as db:
                        row = db.execute('SELECT result,created FROM commands WHERE id=?', (cid,)).fetchone()
                    if not row:
                        return self.response(404, {'error': '没有找到该指令'})
                    return self.response(200, {'result': json.loads(row[0]) if row[0] else None, 'created': row[1]})
                if path == '/api/secure-update':
                    cid = params.get('id', [''])[0]
                    with app.lock:
                        job = app.secure_commands.get(cid)
                        if not job:
                            return self.response(404, {'error': '更新请求已结束或超时'})
                        result = job['result']
                        created = job['created']
                        if result is not None:
                            del app.secure_commands[cid]
                    return self.response(200, {'result': result, 'created': created})
                return self.response(404, {'error': '没有找到此页面'})
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except Exception as exc:
                self.response(400, {'error': str(exc)})

        def do_POST(self):
            self._post_body_handled = False
            try:
                if self.path == '/agent/sync':
                    return self.agent_sync()
                if not self.check_origin():
                    return
                if self.path == '/api/login':
                    ip = self.client_address[0]
                    with app.lock:
                        failures = [t for t in app.failures.get(ip, []) if time.time() - t < 300]
                        if len(failures) >= 12:
                            return self.reject_post(429, {'error': '尝试次数过多，请稍后再试'})
                    password = self.body().get('password', '')
                    actual = hashlib.sha256(password.encode()).hexdigest()
                    if not hmac.compare_digest(actual, app.cfg['password_sha256']):
                        with app.lock:
                            app.failures[ip] = failures + [time.time()]
                        return self.response(401, {'error': '访问密码不正确'})
                    return self.response(200, {'ok': True}, extra={'Set-Cookie': 'relay_session=' + app.cookie() + '; Path=/; HttpOnly; SameSite=Strict; Max-Age=604800'})
                if self.path == '/client/login':
                    ip = self.client_address[0]
                    with app.lock:
                        attempts = [t for t in app.setup_failures.get(ip, []) if time.time() - t < 300]
                        if len(attempts) >= 12:
                            return self.reject_post(429, {'error': '配对登录尝试过于频繁，请稍后再试'})
                        app.setup_failures[ip] = attempts + [time.time()]
                    request = self.body()
                    config = Path(app.cfg.get('_config_path', '')).parent / 'client-pairing-edge.json'
                    if app.cfg.get('client_login_mode') == 'local':
                        username, password = (request.get('username'), request.get('password'))
                        valid = isinstance(username, str) and isinstance(password, str) and (len(password) <= 256) and hmac.compare_digest(username.encode(), str(app.cfg.get('username', 'relay')).encode()) and hmac.compare_digest(hashlib.sha256(password.encode()).hexdigest(), app.cfg['password_sha256'])
                    else:
                        valid = verify_panel_login(config, request.get('username'), request.get('password'))
                    if not valid:
                        return self.response(401, {'error': '面板账号或密码不正确'})
                    return self.response(200, {'ok': True}, extra={'Set-Cookie': 'relay_session=' + app.cookie(600) + '; Path=/; HttpOnly; SameSite=Strict; Max-Age=600'})
                if self.path == '/client/forget' and self.headers.get('Authorization'):
                    supplied = self.headers['Authorization']
                    matches = [hid for hid, token in app.cfg['agents'].items() if hmac.compare_digest(supplied, 'Bearer ' + token)]
                    if len(matches) > 1:
                        return self.response(409, {'error': '旧凭证被多台设备共用，未撤销；请先拆分凭证或仅断开本机'})
                    legacy = None
                    if matches:
                        host = matches[0]
                        legacy = {'host_id': host, 'applicationId': app.cfg.get('application_nodes', {}).get(host, {}).get('applicationId', 'codex')}
                    request = self.body()
                    result = app.client_pairing.revoke(supplied, legacy, request.get('hostId'))
                    if not result:
                        return self.response(401, {'error': '无法确认这台设备的配对凭证'})
                    if not result['alreadyRevoked']:
                        host = result['hostId']
                        with app.lock:
                            app.hosts.pop(host, None)
                            app.lite_views.pop(host, None)
                            app.catalog_versions.clear()
                            for job in app.secure_commands.values():
                                if job['host'] == host and job['result'] is None:
                                    job.update(result={'ok': False, 'error': '设备配对已解除'}, text='')
                            with app.db() as db:
                                db.execute('UPDATE commands SET result=? WHERE host=? AND result IS NULL', (json.dumps({'ok': False, 'error': '设备配对已解除'}), host))
                        app.signal(removed_hosts=[host])
                    return self.response(200, {'ok': True, **result})
                if not app.authenticated(self.headers):
                    return self.reject_post(401, {'error': '请先登录'})
                if self.path == '/api/client/enroll':
                    return self.response(200, app.client_pairing.enroll(self.body()))
                if self.path == '/client/forget':
                    hosts = app.client_pairing.forget(self.body().get('installationId'))
                    with app.lock:
                        for host in hosts:
                            app.hosts.pop(host, None)
                            app.lite_views.pop(host, None)
                        app.catalog_versions.clear()
                    app.signal(removed_hosts=hosts)
                    return self.response(200, {'ok': True, 'removed': len(hosts)})
                if self.path == '/api/panel/restart':
                    request = self.body()
                    return self.response(202, app.panel_restart.request(request.get('id'), request.get('mode')))
                if self.path == '/api/ui/title':
                    return self.response(200, {'title': app.set_ui_title(self.body().get('title'))})
                if self.path == '/api/ui/workspace':
                    return self.response(200, {'workspaceThreads': app.update_workspace_thread(self.body())})
                if self.path == '/api/terminal':
                    request = self.body()
                    result = app.terminal_request(request)
                    if request.get('action') in ('start', 'close'):
                        app.signal()
                    return self.response(200, result)
                if self.path == '/api/files':
                    request = self.body()
                    return self.response(200, app.client_access(request.get('host', ''), 'client_files', request) if app.client_access_host(request.get('host', '')) else app.ssh_files.execute(request))
                if self.path == '/api/logout':
                    return self.response(200, {'ok': True}, extra={'Set-Cookie': 'relay_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0'})
                if self.path == '/api/traffic/reset':
                    return self.response(200, app.reset_traffic_current())
                if self.path == '/api/secure-update':
                    data = self.body()
                    cid = app.queue_secure_update(data.get('host'), data.get('package'), data.get('sudoPassword', ''))
                    return self.response(200, {'id': cid})
                if self.path == '/api/command':
                    data = self.body()
                    action = data.get('action')
                    valid_actions = {'watch', 'mark_read', 'send', 'steer', 'stop_turn', 'continue_turn', 'edit_last_user_turn', 'edit_user_turn', 'archive', 'unarchive', 'project_create', 'project_rename', 'project_delete', 'move_project', 'update_settings', 'update_permissions', 'new_thread', 'fetch_asset', 'load_older', 'attach_thread', 'rename_thread', 'fork_thread', 'upload_asset_chunk', 'attach_local_file', 'file_read_text', 'file_write_text', 'software_check', 'queue_edit', 'queue_delete', 'queue_send_now', 'queue_reorder', 'queue_resume', 'resolve_request', 'answer_choice', 'question_control', 'native_read', 'native_action', 'restart_client', 'start_app', 'start_bridge'}
                    if action not in valid_actions:
                        raise ValueError('不支持的操作')
                    if action in ('send', 'steer'):
                        text = data.get('text', '')
                        attachments = data.get('attachments', [])
                        if not isinstance(text, str) or len(text) > 32000:
                            raise ValueError('消息过长')
                        if not isinstance(attachments, list):
                            raise ValueError('附件引用格式错误')
                        clean_attachments = []
                        for attachment in attachments:
                            if not isinstance(attachment, dict):
                                raise ValueError('附件引用格式错误')
                            upload_id = str(attachment.get('uploadId', ''))
                            uuid.UUID(upload_id)
                            clean_attachments.append({'uploadId': upload_id, 'name': str(attachment.get('name', ''))[:180], 'mime': str(attachment.get('mime', ''))[:200], 'size': int(attachment.get('size', 0))})
                        if not text.strip() and (not clean_attachments):
                            raise ValueError('请输入消息或添加附件')
                        message = json.dumps({'relayMessageVersion': 1, 'text': text, 'attachments': clean_attachments}, ensure_ascii=False, separators=(',', ':')) if clean_attachments else text
                    elif action in ('restart_client', 'start_app', 'start_bridge'):
                        if data.get('thread') or data.get('text') or data.get('payload'):
                            raise ValueError('客户端管理操作不接受对话或命令参数')
                        message = ''
                    elif action == 'watch':
                        message = ''
                    else:
                        payload = data.get('payload', {})
                        if not isinstance(payload, dict):
                            raise ValueError('指令参数格式错误')
                        message = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
                        limit = 1200000 if action == 'file_write_text' else 900000 if action == 'upload_asset_chunk' else 600000 if action == 'edit_user_turn' else 40000 if action in ('native_action', 'new_thread', 'queue_edit', 'queue_resume', 'resolve_request', 'answer_choice', 'edit_last_user_turn') else 16000
                        if len(message) > limit:
                            raise ValueError('指令参数过长')
                    cid = app.queue(data['host'], data.get('thread', ''), action, message, data.get('id'))
                    return self.response(200, {'id': cid})
                return self.response(404, {'error': '没有找到此接口'})
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except Exception as exc:
                self.response(400, {'error': str(exc)})

        def agent_identity(self):
            supplied = self.headers.get('Authorization', '')
            if app.client_pairing.is_revoked(supplied):
                return (None, None)
            host = next((hid for hid, token in app.cfg['agents'].items() if hmac.compare_digest(supplied, 'Bearer ' + token)), None)
            enrolled = app.client_pairing.authenticate(supplied) if host is None else None
            if enrolled:
                host = enrolled['host_id']
            return (host, enrolled)

        def agent_sync(self):
            supplied = self.headers.get('Authorization', '')
            host, enrolled = self.agent_identity()
            if host is None:
                return self.response(401, {'error': 'Invalid agent credential'})
            data = self.body()
            if data.get('host') != host:
                return self.response(403, {'error': 'Agent host mismatch'})
            if data.get('controlHeartbeat') is True:
                return self.client_control_sync(host, data, enrolled, supplied)
            changed = False
            with app.lock:
                if app.client_pairing.is_revoked(supplied):
                    return self.response(401, {'error': 'Agent credential was revoked'})
                state = app.hosts.setdefault(host, {'views': {}})
                incoming = {'label': data['label'], 'catalog': data['catalog'], 'applicationId': enrolled['applicationId'] if enrolled else app.cfg.get('application_nodes', {}).get(host, {}).get('applicationId', 'codex'), 'physicalHostId': enrolled['physicalHostId'] if enrolled else app.cfg.get('application_nodes', {}).get(host, {}).get('physicalHostId', host), 'capabilities': data.get('capabilities', {}), 'projects': data.get('projects', []), 'models': data.get('models', []), 'terminal': data.get('terminal', {'supported': False}), 'desktopConnected': data.get('desktopConnected', False)}
                changed = any((state.get(key) != value for key, value in incoming.items()))
                changed = any((state.get(key) != value for key, value in incoming.items()))
                state.update(incoming)
                state['seen'] = time.time()
                for tid, view in data.get('views', {}).items():
                    previous = state['views'].get(tid)
                    comparable = lambda value: {key: item for key, item in value.items() if key != 'updated'} if isinstance(value, dict) else value
                    if comparable(previous) != comparable(view):
                        changed = True
                    app.update_lite_view(host, tid, view)
                    state['views'][tid] = view
                for cid, result in data.get('results', {}).items():
                    job = app.secure_commands.get(cid)
                    if job and job['host'] == host and (job['result'] is None):
                        try:
                            validate_public_data(result, '更新结果')
                        except ValueError:
                            result = {'ok': False, 'error': '更新结果触发凭据保护，已丢弃'}
                        job['result'] = result
                        job['text'] = ''
                        changed = True
                secure_rows = []
                now = time.time()
                for cid, job in list(app.secure_commands.items()):
                    if now - job['created'] >= 3600:
                        del app.secure_commands[cid]
                    elif job['host'] == host and (not job['delivered']):
                        secure_rows.append({'id': cid, 'thread': '', 'action': 'secure_software_update', 'text': job['text']})
                        job['text'] = ''
                        job['delivered'] = True
            with app.db() as db:
                for cid, result in data.get('results', {}).items():
                    action_row = db.execute('SELECT action,thread,result FROM commands WHERE id=? AND host=?', (cid, host)).fetchone()
                    cursor = db.execute("UPDATE commands SET result=?,text='' WHERE id=? AND host=? AND result IS NULL", (json.dumps(result), cid, host))
                    changed = changed or cursor.rowcount > 0
                    if action_row and action_row[2]:
                        previous = json.loads(action_row[2])
                        if allow_late_native_receipt(cid, action_row[1], action_row[0], previous, result):
                            cursor = db.execute("UPDATE commands SET result=?,text='' WHERE id=? AND host=? AND result=?", (json.dumps(result), cid, host, action_row[2]))
                            changed = changed or cursor.rowcount > 0
                expired = json.dumps({'ok': False, 'unknown': True, 'error': '指令确认超时，请核对原对话；不会自动重发'})
                now = time.time()
                cursor = db.execute("UPDATE commands SET result=? WHERE host=? AND result IS NULL AND ((action='optional provider_action' AND created<?) OR (action IN ('client_files','client_terminal') AND created<?) OR (action NOT IN ('optional provider_action','client_files','client_terminal') AND created<?))", (expired, host, now - 150, now - 360, now - 90))
                changed = changed or cursor.rowcount > 0
                rows = db.execute("SELECT id,thread,action,text FROM commands WHERE host=? AND result IS NULL AND action NOT IN ('start_app','start_bridge') ORDER BY created LIMIT 16", (host,)).fetchall()
                db.execute("DELETE FROM commands WHERE result IS NOT NULL AND ((action='fetch_asset' AND created<?) OR (action LIKE 'terminal_%' AND created<?) OR created<?)", (now - 3600, now - 600, now - 86400))
            if changed:
                app.signal()
            response = {'generation': app.generation, 'commands': [dict(zip(('id', 'thread', 'action', 'text'), row)) for row in rows] + secure_rows}
            if state.get('capabilities', {}).get('nativeLiveOutput') is True:
                response['liveOutputDemand'] = app.live_output_demand(host)
            return self.response(200, response)

        def client_control_sync(self, host, data, enrolled, supplied):
            control = data.get('clientControl')
            if not isinstance(control, dict) or control.get('protocol') != 1 or (not isinstance(control.get('controlOnly'), bool)) or (not isinstance(data.get('label'), str)) or (len(data['label']) > 200) or (not isinstance(data.get('results', {}), dict)) or (len(data.get('results', {})) > 128):
                return self.response(400, {'error': 'Invalid client control heartbeat'})
            changed = False
            with app.lock:
                if app.client_pairing.is_revoked(supplied):
                    return self.response(401, {'error': 'Agent credential was revoked'})
                state = app.hosts.setdefault(host, {'views': {}, 'catalog': [], 'projects': [], 'models': []})
                if control['controlOnly']:
                    incoming = {'label': data['label'], 'desktopConnected': False, 'applicationId': enrolled['applicationId'] if enrolled else app.cfg.get('application_nodes', {}).get(host, {}).get('applicationId', 'codex'), 'physicalHostId': enrolled['physicalHostId'] if enrolled else app.cfg.get('application_nodes', {}).get(host, {}).get('physicalHostId', host)}
                    control = {key: control[key] for key in ('protocol', 'version', 'controlOnly', 'launchApp', 'startBridge', 'restartClient', 'retryExhausted') if key in control}
                    caps = dict(state.get('capabilities', {}), clientControl=control, actions=['start_app', 'start_bridge', 'restart_client'], launchApp=True)
                    incoming['capabilities'] = caps
                    changed = any((state.get(key) != value for key, value in incoming.items()))
                    state.update(incoming)
                    for tid, before in list(state['views'].items()):
                        if before.get('owned'):
                            value = dict(before, owned=False)
                            state['views'][tid] = value
                            app.update_lite_view(host, tid, value)
                            changed = True
                    state['seen'] = time.time()
            acknowledged = []
            with app.db() as db:
                db.execute("UPDATE commands SET result=? WHERE host=? AND result IS NULL AND action IN ('start_app','start_bridge','restart_client') AND created<?", (json.dumps({'ok': False, 'unknown': True, 'error': '客户端管理操作确认超时；不会自动重发'}), host, time.time() - 90))
                for cid, result in data.get('results', {}).items():
                    if not isinstance(result, dict) or result.get('operationId') != cid or (not isinstance(result.get('ok'), bool)):
                        continue
                    row = db.execute("SELECT action,result FROM commands WHERE id=? AND host=? AND thread='' AND action IN ('start_app','start_bridge','restart_client')", (cid, host)).fetchone()
                    if row:
                        clean = {key: result[key] for key in ('ok', 'accepted', 'operationId', 'clientRestart', 'unknown') if key in result}
                        if not clean['ok']:
                            clean['error'] = '客户端未确认管理操作；不会自动重发'
                        cursor = db.execute("UPDATE commands SET result=?,text='' WHERE id=? AND host=? AND result IS NULL", (json.dumps(clean), cid, host))
                        changed |= cursor.rowcount > 0
                        acknowledged.append(cid)
                rows = db.execute("SELECT id,thread,action,text FROM commands WHERE host=? AND thread='' AND result IS NULL AND action IN ('start_app','start_bridge','restart_client') AND created>? ORDER BY created LIMIT 16", (host, time.time() - 90)).fetchall()
            if changed:
                app.signal()
            return self.response(200, {'protocol': 1, 'acknowledged': acknowledged, 'commands': [dict(zip(('id', 'thread', 'action', 'text'), row)) for row in rows]})

        def events(self, min_interval=0):
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Accel-Buffering', 'no')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True
            version = -1
            last_emit = 0.0
            first_emit = True
            end = time.monotonic() + 300
            while time.monotonic() < end:
                removed = []
                with app.changed:
                    if version == app.version:
                        app.changed.wait(15)
                    if version != app.version and min_interval:
                        remaining = last_emit + min_interval - time.monotonic()
                        while remaining > 0:
                            app.changed.wait(remaining)
                            remaining = last_emit + min_interval - time.monotonic()
                    changed = version != app.version
                    if changed:
                        removed = sorted({host for number, hosts in app.removed_host_events if number > version for host in hosts if host not in app.hosts})
                        version = app.version
                payload = f'data: {version}\n\n' if changed else ': keepalive\n\n'
                if removed:
                    payload = 'event: hosts-removed\ndata: ' + json.dumps({'hosts': removed}) + '\n\n' + payload
                self.wfile.write(payload.encode())
                self.wfile.flush()
                self.count_public_traffic(len(payload.encode()), count_request=first_emit)
                first_emit = False
                if changed:
                    last_emit = time.monotonic()
    return Handler
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    os.umask(63)
    cfg = json.loads(Path(args.config).read_text())
    cfg['_config_path'] = str(Path(args.config).resolve())
    relay = Relay(cfg)
    server = RelayHTTPServer((cfg['bind'], cfg['port']), handler_factory(relay))
    print('Codex Desktop Relay listening on', cfg['public_url'], flush=True)
    server.serve_forever()
