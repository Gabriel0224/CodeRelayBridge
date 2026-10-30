"""Outbound-only host agent for an already running Codex Desktop."""
import argparse
import atexit
import base64
import copy
from datetime import datetime
import gzip
import hashlib
import html
from contextlib import contextmanager, closing
import json
import math
import mimetypes
import os
from pathlib import Path
import plistlib
import queue
import re
import shlex
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import uuid
from urllib.parse import quote, unquote, unquote_to_bytes, urlsplit
import urllib.request
if os.name != 'nt':
    import fcntl
    import pty
    import struct
    import termios
from crb.codex.ipc import AppToolsUnavailable, DesktopAppTools, MacNativeAppTools, DesktopIPC, apply_patches, desktop_app_tool_paths
import crb.updates.native_updates as software_updates
from crb.codex.read_progress import ReadProgress
from crb.codex.native_features import NativeFeatures, public_metadata
from crb.codex.question_ui import CodexQuestionUI
from crb.codex.native_history import NativeHistoryReader
from crb.codex.conversation import create_configured
import crb.codex.sidebar_ui as native_conversation_ui
from crb.codex.projects import desktop_project_id, local_projects, path_key, project_paths, project_id_map, registered_backend_project, thread_project_memberships
from crb.common.live_view_cache import LiveTurnCache
MAX_ITEM_TEXT = 500000
MAX_TOOL_DETAIL = 200000
MAX_ASSET_SIZE = 256 * 1024 * 1024
MAX_ASSET_CHUNK = 2 * 1024 * 1024
MAX_UPLOAD_CHUNK = 512 * 1024
MAX_EDITOR_BYTES = 512 * 1024
UPLOAD_DISK_RESERVE = 64 * 1024 * 1024
MAX_VIEW_ITEMS_BYTES = 256 * 1024
MAX_TERMINALS = 12
TERMINAL_BUFFER_LIMIT = 2 * 1024 * 1024
MODEL_SOURCE_CHECK_INTERVAL = 2
MODEL_REFRESH_FALLBACK_INTERVAL = 6 * 60 * 60
MODEL_REFRESH_RETRY_MIN = 30
MODEL_REFRESH_RETRY_MAX = 15 * 60
TERMINAL_READ_LIMIT = 128 * 1024
MARKDOWN_LINK = re.compile('(?P<image>!)?\\[(?P<label>[^\\]]*)\\]\\((?:<(?P<angle>[^>]+)>|(?P<plain>[^)\\s]+))(?:\\s+[\'\\"][^\'\\"]*[\'\\"])?\\)')
HEARTBEAT_BLOCK = re.compile('<heartbeat(?:\\s[^>]*)?>.*?</heartbeat>', re.IGNORECASE | re.DOTALL)

class TerminalSession:
    """A bounded, reconnectable shell stream owned by the host agent."""

    def __init__(self, session_id, cwd, cols=100, rows=30):
        self.id = session_id
        self.cwd = str(cwd)
        self.cols = cols
        self.rows = rows
        self.lock = threading.RLock()
        self.buffer = bytearray()
        self.base_offset = 0
        self.total_offset = 0
        self.last_used = time.monotonic()
        self.exit_code = None
        self.closed = False
        self.master_fd = None
        self.process = None
        self.input_stream = None
        self.output_stream = None
        self.shell = 'PowerShell' if os.name == 'nt' else 'Shell'
        self._start()

    def _start(self):
        env = dict(os.environ)
        env.setdefault('TERM', 'xterm-256color')
        env.setdefault('COLORTERM', 'truecolor')
        if os.name == 'nt':
            binary = shutil.which('pwsh.exe') or shutil.which('pwsh') or shutil.which('powershell.exe') or shutil.which('powershell')
            if not binary:
                raise RuntimeError('目标 Windows 没有找到 PowerShell')
            self.shell = 'PowerShell'
            creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            self.process = subprocess.Popen([binary, '-NoLogo', '-NoProfile', '-NoExit', '-Command', '-'], cwd=self.cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0, creationflags=creationflags)
            self.input_stream = self.process.stdin
            self.output_stream = self.process.stdout
            prelude = '[Console]::InputEncoding=[Text.UTF8Encoding]::new($false);[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);$OutputEncoding=[Console]::OutputEncoding\r\n'
            self.input_stream.write(prelude.encode('utf-8'))
            self.input_stream.flush()
        else:
            shell = os.environ.get('SHELL', '')
            if not shell or not Path(shell).is_file():
                shell = shutil.which('zsh') or shutil.which('bash') or '/bin/sh'
            self.shell = Path(shell).name
            master_fd, slave_fd = pty.openpty()
            self.master_fd = master_fd
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack('HHHH', self.rows, self.cols, 0, 0))
            try:
                self.process = subprocess.Popen([shell, '-l'], cwd=self.cwd, env=env, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, close_fds=True, start_new_session=True)
            finally:
                os.close(slave_fd)
        threading.Thread(target=self._reader, daemon=True, name=f'relay-terminal-{self.id[:8]}').start()

    def _reader(self):
        try:
            while True:
                try:
                    chunk = self.output_stream.read(65536) if os.name == 'nt' else os.read(self.master_fd, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                with self.lock:
                    self.buffer.extend(chunk)
                    self.total_offset += len(chunk)
                    if len(self.buffer) > TERMINAL_BUFFER_LIMIT:
                        drop = len(self.buffer) - TERMINAL_BUFFER_LIMIT
                        del self.buffer[:drop]
                        self.base_offset += drop
        finally:
            code = self.process.poll()
            if code is None:
                try:
                    code = self.process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    code = None
            with self.lock:
                self.exit_code = code

    def write(self, data):
        if not data:
            return 0
        with self.lock:
            if self.closed or self.process.poll() is not None:
                raise RuntimeError('终端会话已经结束')
            self.last_used = time.monotonic()
        if os.name == 'nt':
            self.input_stream.write(data)
            self.input_stream.flush()
            return len(data)
        written = 0
        while written < len(data):
            written += os.write(self.master_fd, data[written:])
        return written

    def resize(self, cols, rows):
        self.cols, self.rows = (cols, rows)
        self.last_used = time.monotonic()
        if os.name != 'nt' and self.master_fd is not None:
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))

    def read(self, offset):
        with self.lock:
            self.last_used = time.monotonic()
            truncated = offset < self.base_offset
            start = max(offset, self.base_offset)
            relative = min(max(start - self.base_offset, 0), len(self.buffer))
            chunk = bytes(self.buffer[relative:relative + TERMINAL_READ_LIMIT])
            return {'data': base64.b64encode(chunk).decode(), 'offset': start + len(chunk), 'truncated': truncated, 'baseOffset': self.base_offset, 'totalOffset': self.total_offset, 'exited': self.process.poll() is not None, 'exitCode': self.process.poll(), 'shell': self.shell, 'cwd': self.cwd}

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
        if self.process and self.process.poll() is None:
            try:
                if os.name == 'nt':
                    self.process.terminate()
                else:
                    os.killpg(self.process.pid, signal.SIGHUP)
                self.process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self.process.kill()
                except OSError:
                    pass
        for stream in (self.input_stream, self.output_stream):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass

class AppServerPreludeError(RuntimeError):
    """A preparatory app-server request failed before the requested tool ran."""

class AppServer:
    """Official app-server client; optional owned channels reuse read-only calls."""

    def __init__(self, binary, codex_home, cli_args=(), env_overrides=None, binary_resolver=None, reuse_reads=False):
        self.binary = str(binary)
        self.codex_home = str(codex_home)
        self.cli_args = tuple(cli_args)
        if sys.platform == 'darwin' and (not any(('mcp_servers.codex_relay_native_bridge.enabled=' in arg for arg in self.cli_args))):
            self.cli_args += ('-c', 'mcp_servers.codex_relay_native_bridge.enabled=false')
        self.env_overrides = dict(env_overrides or {})
        self.binary_resolver = binary_resolver
        self.reuse_reads = reuse_reads
        self._read_pool = {}
        self._pool_lock = threading.RLock()
        self._pool_closed = False
        if reuse_reads:
            atexit.register(self.close)

    def close(self):
        """End ONLY our read channels; mutation sessions stay separately owned."""
        with self._pool_lock:
            self._pool_closed = True
            channels = list(self._read_pool.values())
            self._read_pool.clear()
        for channel in channels:
            process = channel.get('process')
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
        for channel in channels:
            with channel['lock']:
                context = channel.pop('context', None)
                if context:
                    context.__exit__(None, None, None)

    def _read_identity(self):
        binary = self.binary_resolver() if self.binary_resolver else self.binary
        if not binary:
            raise FileNotFoundError('没有找到当前 Codex 程序')

        def signature(path):
            try:
                value = Path(path).stat()
                return (value.st_dev, value.st_ino, value.st_mtime_ns, value.st_size)
            except OSError:
                return None
        return (str(binary), signature(binary), self.codex_home, self.cli_args, signature(Path(self.codex_home) / 'auth.json'), signature(Path(self.codex_home) / 'config.toml'))

    def _read_request(self, method, params, timeout):
        name = 'history' if method in ('thread/turns/list', 'thread/items/list') else 'metadata'
        with self._pool_lock:
            if self._pool_closed:
                raise ConnectionError('Codex 只读通道已关闭')
            channel = self._read_pool.setdefault(name, {'lock': threading.RLock()})
        with channel['lock']:
            if self._pool_closed:
                raise ConnectionError('Codex 只读通道已关闭')
            identity = self._read_identity()
            if channel.get('identity') != identity:
                context = channel.pop('context', None)
                if context:
                    context.__exit__(None, None, None)
            if 'context' not in channel:
                context = self.session(timeout=timeout, _channel=channel)
                channel['request'] = context.__enter__()
                channel['context'] = context
                channel['identity'] = identity
            try:
                return channel['request'](method, params, timeout_override=timeout)
            except (TimeoutError, ConnectionError, OSError, RuntimeError):
                context = channel.pop('context', None)
                if context:
                    context.__exit__(None, None, None)
                raise

    def request(self, method, params, timeout=30, before=()):
        if self.reuse_reads and (not before) and (method in ('thread/turns/list', 'thread/items/list', 'thread/read', 'thread/list', 'model/list', 'account/read', 'account/rateLimits/read', 'config/read')):
            return self._read_request(method, params, timeout)
        with self.session(timeout=timeout) as request:
            for prior_method, prior_params in before:
                try:
                    request(prior_method, prior_params)
                except RuntimeError as exc:
                    raise AppServerPreludeError(str(exc)) from exc
            return request(method, params)

    @contextmanager
    def session(self, timeout=30, _channel=None):
        """Keep dependent native requests on one connection, without inference."""
        binary = self.binary_resolver() if self.binary_resolver else self.binary
        if not binary:
            raise FileNotFoundError('没有找到当前 Codex 程序')
        self.binary = str(binary)
        env = dict(os.environ)
        env['CODEX_HOME'] = self.codex_home
        env.update(self.env_overrides)
        launch_options = {}
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            launch_options = {'creationflags': subprocess.CREATE_NO_WINDOW, 'startupinfo': startupinfo}
        process = subprocess.Popen([str(binary), 'app-server', *self.cli_args, '--stdio'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8', errors='replace', bufsize=1, env=env, **launch_options)
        if _channel is not None:
            _channel['process'] = process
        messages = queue.Queue()

        def read_messages():
            try:
                for line in process.stdout:
                    try:
                        messages.put(json.loads(line))
                    except (TypeError, ValueError):
                        continue
            except (OSError, ValueError):
                pass
            finally:
                messages.put(None)
        threading.Thread(target=read_messages, daemon=True).start()

        def send(message):
            process.stdin.write(json.dumps(message, ensure_ascii=False, separators=(',', ':')) + '\n')
            process.stdin.flush()

        def receive(request_id, wait, method):
            deadline = time.monotonic() + wait
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f'Codex app-server 请求超时：{method}')
                try:
                    message = messages.get(timeout=remaining)
                except queue.Empty as exc:
                    raise TimeoutError(f'Codex app-server 请求超时：{method}') from exc
                if message is None:
                    raise RuntimeError('Codex app-server 已提前退出')
                if message.get('id') != request_id:
                    continue
                if message.get('error'):
                    error = message['error']
                    raise RuntimeError(error.get('message', str(error)))
                return message.get('result', {})
        try:
            if _channel is not None:
                with self._pool_lock:
                    if self._pool_closed:
                        raise ConnectionError('Codex 只读通道已关闭')
            send({'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'codex-desktop-relay', 'title': 'Codex Desktop Relay', 'version': '0.2'}, 'capabilities': {'experimentalApi': True, 'optOutNotificationMethods': ['thread/started', 'turn/started', 'turn/completed']}}})
            receive(1, min(timeout, 15), 'initialize')
            send({'method': 'initialized', 'params': {}})
            next_id = 2

            def request(method, params, timeout_override=None):
                nonlocal next_id
                request_id = next_id
                next_id += 1
                send({'id': request_id, 'method': method, 'params': params})
                return receive(request_id, timeout if timeout_override is None else timeout_override, method)
            yield request
        finally:
            try:
                process.terminate()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                    process.wait(timeout=2)
                except OSError:
                    pass
            finally:
                for stream in (process.stdin, process.stdout):
                    if stream and hasattr(stream, 'close'):
                        try:
                            stream.close()
                        except OSError:
                            pass

def find_codex_binary(config):
    candidates = [config.get('codex_bin'), '/usr/lib/chatgpt/resources/codex', '/Applications/ChatGPT.app/Contents/Resources/codex', '/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex']
    if os.name == 'nt':
        local = Path(os.environ.get('LOCALAPPDATA', '')) / 'OpenAI/Codex/bin'
        configured = config.get('codex_bin')
        if configured and Path(configured).resolve().is_relative_to(local.resolve()):
            candidates.remove(configured)
        if local.is_dir():
            candidates.extend(sorted(local.glob('*/codex.exe'), key=lambda path: path.stat().st_mtime, reverse=True))
    candidates.append(shutil.which('codex'))
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None

def native_auth_identity(auth_status):
    """Return Desktop's privacy-safe read-state identity from getAuthStatus."""
    if not isinstance(auth_status, dict):
        return None
    method = auth_status.get('authMethod')
    if method not in ('chatgpt', 'chatgptAuthTokens'):
        if method is None and auth_status.get('requiresOpenaiAuth') is not False:
            return None
        return {'kind': 'execution-storage', 'authMode': method or 'none'}
    token = auth_status.get('authToken')
    if not isinstance(token, str):
        return None
    try:
        payload = token.split('.', 2)[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
    except (IndexError, ValueError, TypeError, UnicodeError):
        return None
    nested = claims.get('https://api.openai.com/auth', {})
    if not isinstance(nested, dict):
        nested = {}
    account_id = nested.get('chatgpt_account_id') or claims.get('chatgpt_account_id') or claims.get('account_id')
    user_id = nested.get('user_id') or nested.get('chatgpt_user_id') or claims.get('user_id') or claims.get('chatgpt_user_id')
    if not isinstance(account_id, str) or not isinstance(user_id, str):
        return None
    return {'kind': 'chatgpt', 'accountId': account_id, 'userId': user_id}

def native_identity_key(identity):
    if not isinstance(identity, dict):
        return None
    if identity.get('kind') == 'chatgpt':
        values = ['chatgpt', identity.get('accountId'), identity.get('userId')]
    elif identity.get('kind') == 'execution-storage':
        values = ['execution-storage', identity.get('authMode')]
    else:
        return None
    if any((value is None for value in values)):
        return None
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()

def native_unread_threads(global_state, identity_key):
    """Read current-local-host unread IDs from Desktop's versioned state."""
    if not isinstance(global_state, dict) or not identity_key:
        return None
    read_state = global_state.get('electron-thread-read-state-v1')
    if not isinstance(read_state, dict) or read_state.get('version') != 1:
        return None
    unread_by_identity = read_state.get('unreadByIdentity', {})
    if not isinstance(unread_by_identity, dict):
        return None
    record = unread_by_identity.get(identity_key, {})
    if not isinstance(record, dict):
        return None
    host_key = native_local_host_key(global_state, identity_key)
    if host_key is None:
        return set() if not record else None
    values = record.get(host_key, [])
    if not isinstance(values, list):
        return None
    return {value for value in values if isinstance(value, str)}

def native_local_host_key(global_state, identity_key):
    read_state = global_state.get('electron-thread-read-state-v1', {})
    record = read_state.get('unreadByIdentity', {}).get(identity_key, {})
    if not isinstance(record, dict):
        record = {}
    migration = read_state.get('legacyMigration', {})
    adopted = migration.get('adoptedHostIds', {}) if isinstance(migration, dict) else {}
    host_key = adopted.get('local') if isinstance(adopted, dict) else None
    if not isinstance(host_key, str) or not host_key.startswith('local:'):
        host_key = None
    default_host_key = 'local:' + hashlib.sha256(json.dumps(['local', 'local', None], separators=(',', ':')).encode()).hexdigest()
    if host_key not in record:
        host_key = default_host_key if default_host_key in record else None
    if host_key is None and len(record) == 1:
        candidate = next(iter(record))
        if isinstance(candidate, str) and candidate.startswith('local:'):
            host_key = candidate
    return host_key or default_host_key

def native_read_sources(global_state):
    """Local-machine unread sources across identities, never another SSH host."""
    if not isinstance(global_state, dict):
        return None
    state = global_state.get('electron-thread-read-state-v1')
    if not isinstance(state, dict) or state.get('version') != 1:
        return None
    identities = state.get('unreadByIdentity')
    if not isinstance(identities, dict):
        return None
    sources = {}
    for identity, record in identities.items():
        if not isinstance(identity, str) or not isinstance(record, dict):
            continue
        host = native_local_host_key(global_state, identity)
        values = record.get(host, [])
        if isinstance(values, list):
            sources[f'{identity}/{host}'] = {tid for tid in values if isinstance(tid, str)}
    return sources

def app_tool_json(result):
    """Return the first JSON object carried by a Desktop app-tool result."""
    for item in result.get('contentItems', []):
        if item.get('type') != 'inputText' or not isinstance(item.get('text'), str):
            continue
        try:
            value = json.loads(item['text'])
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return {}

def title_text(*values):
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip().splitlines()[0][:240]
    return '未命名对话'

def path_belongs_to(path, root):
    if not path or not root:
        return False
    try:
        path = os.path.normcase(os.path.abspath(normalize_local_path(path)))
        root = os.path.normcase(os.path.abspath(normalize_local_path(root)))
        return os.path.commonpath([path, root]) == root
    except (OSError, ValueError):
        return False

def normalize_local_path(value):
    """Convert Windows extended-length paths to their normal local form."""
    value = str(value or '')
    if value.startswith('\\\\?\\UNC\\'):
        return '\\\\' + value[8:]
    if value.startswith('\\\\?\\'):
        return value[4:]
    return value

def safe_upload_name(value):
    """Keep the browser-provided display name without allowing path traversal."""
    name = re.split('[\\\\/]', str(value or ''))[-1]
    name = ''.join((char for char in name if ord(char) >= 32 and char not in '<>:"|?*'))
    name = name.strip(' .')[:180]
    return name or '附件'

def text_content(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return '\n'.join(filter(None, (text_content(x) for x in value)))
    if isinstance(value, dict):
        for key in ('text', 'summary_text', 'raw_content', 'content', 'input', 'message', 'output_text', 'input_text'):
            if key in value:
                text = text_content(value[key])
                if text:
                    return text
    return ''

def heartbeat_block(value, whole_message=False):
    """Locate a scheduler envelope without parsing its free-form instructions as XML."""
    text = str(value or '')
    blocks = list(HEARTBEAT_BLOCK.finditer(text))
    if not blocks:
        return None
    block = blocks[-1].group(0)
    if whole_message and text.strip() != block.strip():
        return None
    return block

def heartbeat_field(block, name):
    """Read only scheduler control tags; the instruction body may contain raw XML chars."""
    if not block:
        return None
    match = re.search(f'<{name}(?:\\s[^>]*)?>(.*?)</{name}\\s*>', block, re.I | re.S)
    return html.unescape(match.group(1)).strip() if match else None

def scheduled_user_trigger(value):
    """Identify actual scheduler envelopes, not a user's quoted protocol example."""
    heartbeat = heartbeat_block(value, whole_message=True)
    if heartbeat_field(heartbeat, 'automation_id') is not None and heartbeat_field(heartbeat, 'instructions') is not None:
        return heartbeat
    return None

def desktop_user_text(value):
    """Keep automatic trigger instructions without the internal scheduler tags."""
    text = str(value or '')
    replies = async_question_replies(text)
    if replies:
        return '\n\n'.join((reply['answer'] if len(replies) == 1 else reply['question'] + '\n' + reply['answer'] for reply in replies))
    trigger = scheduled_user_trigger(text)
    if trigger is not None:
        return heartbeat_field(trigger, 'instructions') or '定时监控已触发。'
    return text

def desktop_agent_text(value):
    """Display scheduler message bodies independently of notification policy."""
    text = str(value or '')
    heartbeat = heartbeat_block(text)
    if heartbeat is None:
        return text
    decision = (heartbeat_field(heartbeat, 'decision') or '').upper()
    if decision in ('NOTIFY', 'DONT_NOTIFY'):
        message = heartbeat_field(heartbeat, 'message')
        if message is not None:
            return message
        return HEARTBEAT_BLOCK.sub('', text).strip()
    return text

def scheduled_automation_signature(home):
    """Track created, removed and edited task definitions without reading prompts."""
    try:
        paths = sorted((Path(home) / 'automations').glob('*/automation.toml'))[:500]
        return tuple(((str(path), path.stat().st_mtime_ns, path.stat().st_size) for path in paths))
    except OSError:
        return ()

def scheduled_thread_automations(home):
    """Map local heartbeat definitions to their existing Codex conversation."""
    result = {}
    directory = Path(home) / 'automations'
    try:
        paths = sorted(directory.glob('*/automation.toml'))[:500]
    except OSError:
        return result
    for path in paths:
        try:
            if path.stat().st_size > 128 * 1024:
                continue
            data = tomllib.loads(path.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        definition = data.get('automation', data)
        if not isinstance(definition, dict):
            continue
        kind = str(definition.get('kind') or '').lower()
        if kind and kind != 'heartbeat':
            continue
        target = definition.get('target', {})
        if not isinstance(target, dict):
            target = {}
        thread_id = next((value for value in (definition.get('targetThreadId'), definition.get('target_thread_id'), target.get('threadId'), target.get('thread_id')) if isinstance(value, str) and 1 <= len(value) <= 128), None)
        if not thread_id:
            continue
        status = str(definition.get('status') or 'ACTIVE').upper()
        if status not in ('ACTIVE', 'PAUSED'):
            continue
        result.setdefault(thread_id, []).append({'name': str(definition.get('name') or '定时任务')[:120], 'status': status})
    return result

def _local_reference(value):
    value = str(value or '').strip()
    return value.startswith('file:') or value.startswith('/') or bool(re.match('^[A-Za-z]:[\\\\/]', value))

def _content_attachments(value, asset_factory):
    if not asset_factory:
        return []
    entries = value if isinstance(value, list) else [value]
    attachments = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        content_type = str(entry.get('type', '')).lower()
        if 'image' in content_type:
            source = entry.get('path') or entry.get('image_url') or entry.get('url') or entry.get('file_path')
            if source:
                attachments.append(asset_factory(source, 'image', entry.get('name')))
        elif 'file' in content_type or 'attachment' in content_type or content_type == 'mention':
            source = entry.get('path') or entry.get('file_path') or entry.get('url') or entry.get('file_url')
            if source:
                attachments.append(asset_factory(source, 'file', entry.get('name')))
    return [attachment for attachment in attachments if attachment]

def _markdown_attachments(text, asset_factory):
    if not text or not asset_factory:
        return []
    attachments = []
    for match in MARKDOWN_LINK.finditer(text):
        source = match.group('angle') or match.group('plain') or ''
        if not _local_reference(source):
            continue
        kind = 'image' if match.group('image') else 'file'
        attachment = asset_factory(source, kind, match.group('label') or None)
        if attachment:
            attachments.append(attachment)
    return attachments

def _dedupe_attachments(attachments):
    result = []
    seen = set()
    for attachment in attachments:
        if not attachment:
            continue
        key = attachment.get('id') or (attachment.get('name'), attachment.get('url'))
        if key in seen:
            continue
        seen.add(key)
        result.append(attachment)
    return result

def compact_public_items(items):
    """Drop empty events and repeated cumulative public reasoning summaries."""
    result = []
    reasoning_lines = {}
    for item in items:
        if item.get('type') == 'reasoning':
            seen = reasoning_lines.setdefault(item.get('turnId'), set())
            fresh = []
            for line in str(item.get('text', '')).splitlines():
                clean = line.strip()
                if clean and clean not in seen:
                    seen.add(clean)
                    fresh.append(line)
            if not fresh:
                continue
            item = {**item, 'text': '\n'.join(fresh)}
        if not item.get('text') and (not item.get('detail')) and (not item.get('attachments')) and (not item.get('questions')):
            continue
        result.append(item)
    return result

def bounded_recent_items(items, budget=MAX_VIEW_ITEMS_BYTES):
    """Keep complete recent items and the latest user prompt within the wire budget."""

    def encoded_size(item, comma=False):
        return len(json.dumps(item, ensure_ascii=False, separators=(',', ':'), default=str).encode()) + (1 if comma else 0)
    selected = []
    used = 2
    for item in reversed(items):
        size = encoded_size(item, bool(selected))
        if selected and used + size > budget:
            break
        selected.append(item)
        used += size
    latest_user = next((item for item in reversed(items) if item.get('type') in ('userMessage', 'steeringUserMessage')), None)
    if latest_user is not None and latest_user not in selected:
        anchor_size = encoded_size(latest_user, bool(selected))
        if anchor_size <= budget - 2:
            while selected and used + anchor_size > budget:
                removed = selected.pop()
                used -= encoded_size(removed, bool(selected))
                used = 2 + sum((encoded_size(value, index > 0) for index, value in enumerate(selected)))
                anchor_size = encoded_size(latest_user, bool(selected))
            selected.append(latest_user)
    selected.reverse()
    return selected

def async_question_replies(text):
    """Recognize the exact Desktop envelope; leave quoted/malformed text alone."""
    start, end = ('<send_user_message_question_reply>', '</send_user_message_question_reply>')
    text = str(text or '').strip()
    if not text.startswith(start) or not text.endswith(end):
        return []
    try:
        replies = json.loads(text[len(start):-len(end)])
    except (ValueError, TypeError):
        return []
    if isinstance(replies, dict):
        replies = [replies]
    if not isinstance(replies, list) or not 1 <= len(replies) <= 8 or any((not isinstance(reply, dict) or any((not isinstance(reply.get(field), str) for field in ('questionItemId', 'question', 'answer'))) for reply in replies)):
        return []
    return replies

def async_message_questions(item):
    """Keep only the user-visible choices carried by an async agent message."""
    if item.get('type') not in ('AgentMessage', 'agentMessage', 'AssistantMessage', 'assistantMessage'):
        return []
    raw = item.get('questions')
    if not isinstance(raw, list):
        return []
    questions = []
    for index, question in enumerate(raw[:8]):
        if not isinstance(question, dict):
            continue
        title = question.get('title') or question.get('question')
        if not isinstance(title, str) or not title.strip():
            continue
        options = question.get('options')
        choices = []
        if isinstance(options, list):
            for option in options[:20]:
                label = option if isinstance(option, str) else option.get('label') if isinstance(option, dict) else None
                if isinstance(label, str) and label.strip():
                    choices.append(label.strip()[:4000])
        questions.append({'title': title.strip()[:4000], 'options': choices, 'questionItemId': json.dumps(['request_user_input_async', str(item.get('id', '')), index], ensure_ascii=False, separators=(',', ':'))})
    return questions

def timestamp_ms(value):
    """Accept native Unix seconds/milliseconds and timezone-qualified ISO times."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        if isinstance(value, str) and (not re.fullmatch('\\d+(?:\\.\\d+)?', value)):
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                return None
            number = parsed.timestamp() * 1000
        else:
            number = float(value)
            if number < 100000000000:
                number *= 1000
        return int(number) if 946684800000 <= number < 4102444800000 else None
    except (ValueError, TypeError, OverflowError):
        return None

def set_item_timestamp(public, item=None, turn=None, timing=None, recorded_at=None):
    """Prefer per-item native timing, never the time we fetched the snapshot."""
    item, turn, timing = (item or {}, turn or {}, timing or {})
    for source in (item, timing):
        for key in ('timestamp', 'startedAtMs', 'started_at_ms', 'createdAtMs', 'created_at_ms', 'createdAt', 'created_at', 'startedAt', 'started_at', 'completedAtMs', 'completed_at_ms'):
            value = timestamp_ms(source.get(key))
            if value is not None:
                public['timestamp'] = value
                return public
    kind, key = (public.get('type'), str(public.get('id', '')))
    for field in ('aeonAssistantMessageStartedAtMsById', 'agentMessageStartedAtMsById', 'commandExecutionStartedAtMsById', 'itemStartedAtMsById', 'agentMessageCompletedAtMsById'):
        mapping = turn.get(field) or {}
        value = timestamp_ms(mapping.get(key)) if isinstance(mapping, dict) else None
        if value is not None:
            public['timestamp'] = value
            return public
    value = timestamp_ms(public.get('scheduledAt'))
    if value is not None:
        public['timestamp'] = value
        return public
    value = timestamp_ms(recorded_at)
    if value is not None:
        public.update(timestamp=value, timestampSource='recorded')
        return public
    if kind in ('agentMessage', 'assistantMessage') and public.get('phase') != 'commentary':
        value = timestamp_ms(turn.get('finalAssistantStartedAtMs'))
        if value is not None:
            public['timestamp'] = value
            return public
    fields = ('turnStartedAtMs', 'startedAt', 'started_at')
    if kind not in ('userMessage', 'steeringUserMessage'):
        fields = ('completedAt', 'completed_at', *fields)
    for field in fields:
        value = timestamp_ms(turn.get(field))
        if value is not None:
            public['timestamp'] = value
            if kind != 'userMessage':
                public['timestampSource'] = 'turn'
            return public
    return public

def normalize_item(item, fallback_id, asset_factory=None, *, turn_status=None):
    raw_kind = item.get('type', 'unknown')
    kind = {'UserMessage': 'userMessage', 'SteeringUserMessage': 'steeringUserMessage', 'AgentMessage': 'agentMessage', 'AssistantMessage': 'assistantMessage', 'Reasoning': 'reasoning', 'CommandExecution': 'commandExecution', 'FileChange': 'fileChange', 'Plan': 'plan', 'McpToolCall': 'mcpToolCall', 'ImageView': 'imageView', 'WebSearch': 'webSearch', 'Extension': 'extension', 'ContextCompaction': 'contextCompaction'}.get(raw_kind, raw_kind)
    result = {'id': str(item.get('id', fallback_id)), 'type': kind, 'status': item.get('status'), 'phase': item.get('phase')}
    attachments = []
    if kind in ('userMessage', 'steeringUserMessage'):
        client_id = item.get('clientUserMessageId') or item.get('clientId')
        if isinstance(client_id, str) and client_id:
            result['clientUserMessageId'] = client_id
        content = item.get('content', item.get('input', item.get('text', '')))
        raw_text = text_content(content)
        result['text'] = desktop_user_text(raw_text)
        replies = async_question_replies(raw_text)
        if replies:
            result['questionReplyTo'] = [reply['questionItemId'] for reply in replies]
        trigger = scheduled_user_trigger(raw_text)
        if trigger is not None:
            result['scheduledTrigger'] = True
            scheduled_at = heartbeat_field(trigger, 'current_time_iso')
            if scheduled_at:
                result['scheduledAt'] = scheduled_at
        attachments.extend(_content_attachments(content, asset_factory))
    elif kind in ('agentMessage', 'assistantMessage'):
        content = item.get('content', item.get('text', ''))
        raw_text = text_content(content)
        result['text'] = desktop_agent_text(raw_text)
        decision = (heartbeat_field(heartbeat_block(raw_text), 'decision') or '').upper()
        if decision in ('NOTIFY', 'DONT_NOTIFY'):
            result['notificationDecision'] = decision
        attachments.extend(_content_attachments(content, asset_factory))
        questions = async_message_questions(item)
        if questions:
            result['questions'] = questions
    elif kind == 'reasoning':
        result['text'] = text_content(item.get('summary')) or text_content(item.get('summary_text')) or text_content(item.get('content')) or text_content(item.get('raw_content'))
    elif kind == 'commandExecution':
        command = item.get('command', '')
        result['text'] = '\n'.join(map(str, command)) if isinstance(command, list) else command
        result['detail'] = item.get('aggregatedOutput') or item.get('aggregated_output') or item.get('formatted_output') or item.get('stdout') or ''
        result['exitCode'] = item.get('exitCode', item.get('exit_code'))
    elif kind == 'fileChange':
        changes = item.get('changes', {})
        paths, details = ([], [])
        if isinstance(changes, dict):
            iterable = changes.items()
        else:
            iterable = ((change.get('path', ''), change) for change in changes if isinstance(change, dict))
        for path, change in iterable:
            paths.append(str(path))
            if isinstance(change, dict):
                body = change.get('diff') or change.get('content') or change.get('patch') or ''
                change_type = change.get('type')
                details.append('\n'.join(filter(None, (f'{path} ({change_type})' if change_type else str(path), str(body)))))
            elif change:
                details.append(f'{path}\n{change}')
            if path and asset_factory:
                attachments.append(asset_factory(path, None, Path(str(path)).name))
        result['text'] = '\n'.join(paths)
        result['detail'] = '\n\n'.join(details)
    elif kind == 'plan':
        result['text'] = item.get('text', '')
    elif kind == 'contextCompaction':
        completed = item.get('completed')
        busy = item.get('status') in ('inProgress', 'in_progress', 'running') or completed is False
        if turn_status == 'completed' or completed is True:
            result.update(status='completed', text='上下文已压缩')
        elif busy and turn_status in ('interrupted', 'failed'):
            result.update(status=turn_status, text='上下文压缩已停止')
        elif busy:
            result.update(status='inProgress', text='正在压缩上下文…')
        else:
            result['text'] = '上下文已压缩'
    elif kind == 'imageView':
        result['text'] = '已查看图片'
        if item.get('path') and asset_factory:
            attachments.append(asset_factory(item['path'], 'image', None))
    elif kind in ('mcpToolCall', 'webSearch', 'extension'):
        result['text'] = str(item.get('tool') or item.get('kind') or item.get('query') or item.get('actionName') or kind)
        result['detail'] = json.dumps({k: v for k, v in item.items() if k not in ('id', 'type')}, ensure_ascii=False, indent=2, default=str)
        saved = item.get('savedPath') or item.get('saved_path')
        if saved and asset_factory:
            attachments.append(asset_factory(saved, None, None))
    else:
        result['text'] = str(item.get('tool', item.get('name', kind)))
        result['detail'] = json.dumps({k: v for k, v in item.items() if k not in ('id', 'type')}, ensure_ascii=False, indent=2, default=str)
    result['text'] = str(result.get('text', ''))[:MAX_ITEM_TEXT]
    if 'detail' in result:
        result['detail'] = str(result['detail'])[:MAX_TOOL_DETAIL]
    attachments.extend(_markdown_attachments(result.get('text', ''), asset_factory))
    attachments = _dedupe_attachments(attachments)
    if attachments:
        result['attachments'] = attachments
    return set_item_timestamp(result, item)

def turn_error_notice(turn_id, error, fallback_id):
    """Expose only Codex's user-facing failure message, not raw error metadata."""
    message = error.get('message') if isinstance(error, dict) else error
    message = message.strip()[:2000] if isinstance(message, str) else ''
    message = message or '本轮执行失败，Codex 未提供具体原因。'
    code = error.get('codexErrorInfo', error.get('codex_error_info', '')) if isinstance(error, dict) else ''
    code = re.sub('[^a-z0-9]', '', str(code).lower())
    quota = 'usagelimit' in code or 'ratelimit' in code or 'quota' in code or ('credit' in code) or bool(re.search('usage limit|rate limit|insufficient credits|out of credits|not enough credits|(额度|余额|积分|点数).{0,6}(不足|用尽|上限)', message, re.IGNORECASE))
    capacity = code == 'serveroverloaded' or 'selected model is at capacity' in message.lower()
    return {'id': 'turn-error-' + str(turn_id or fallback_id), 'turnId': turn_id, 'type': 'systemNotice', 'noticeKind': 'quota' if quota else 'capacity' if capacity else 'error', 'text': message}

def state_turns(state):
    history = state.get('turnHistory', {}).get('history', {})
    entities = history.get('entitiesByKey', {})
    keys = []
    for island in history.get('islands', []):
        keys.extend((entry.get('value', entry.get('key')) for entry in island.get('entries', [])))
    turns = [entities[k] for k in dict.fromkeys(keys) if k in entities]
    if not turns:
        turns = state.get('turns', [])
    return sorted(turns, key=lambda t: t.get('turnStartedAtMs') or 0)

def scheduled_completion_notification(state):
    """Only a completed scheduler final answer can silence a completion notice."""
    if state.get('threadRuntimeStatus', {}).get('type') in ('active', 'systemError'):
        return None
    turns = state_turns(state)
    if not turns:
        return None
    turn = turns[-1]
    if turn.get('status') in ('inProgress', 'failed', 'interrupted') or turn.get('error'):
        return None
    for item in reversed(turn.get('items', [])):
        if item.get('type') not in ('AgentMessage', 'agentMessage', 'AssistantMessage', 'assistantMessage'):
            continue
        if item.get('phase') not in (None, 'final_answer'):
            continue
        text = text_content(item.get('content', item.get('text', '')))
        decision = (heartbeat_field(heartbeat_block(text), 'decision') or '').upper()
        if decision in ('NOTIFY', 'DONT_NOTIFY'):
            return decision == 'NOTIFY'
        return None
    return None

def pending_async_questions(state):
    """Resolve exact native question IDs across turns, before view truncation.

    An ordinary, App-accepted NEW turn retires the previous question surface,
    but is not an answer. Queued messages and steers in the same turn do not.
    """
    offered = []
    answered = set()
    ordinary_turn = -1
    for turn_index, turn in enumerate(state_turns(state)):
        for item in turn.get('items', []):
            if not isinstance(item, dict):
                continue
            questions = async_message_questions(item)
            if questions:
                offered.append((turn_index, item, questions))
            if item.get('type') in ('UserMessage', 'userMessage', 'SteeringUserMessage', 'steeringUserMessage'):
                if item.get('type') in ('SteeringUserMessage', 'steeringUserMessage') and item.get('status') != 'accepted':
                    continue
                content = item.get('content', item.get('input', item.get('text', '')))
                replies = async_question_replies(text_content(content))
                answered.update((reply['questionItemId'] for reply in replies))
                if item.get('type') in ('UserMessage', 'userMessage') and content and (not replies):
                    ordinary_turn = turn_index
    return [(item, [question for question in questions if question['questionItemId'] not in answered]) for turn_index, item, questions in offered if turn_index >= ordinary_turn and any((question['questionItemId'] not in answered for question in questions))]

def latest_async_question(state):
    pending = pending_async_questions(state)
    return pending[-1][0] if pending else None

def native_message_receipt(state, command_id, steering=False):
    """Exact App-accepted message identity; never infer delivery from its text."""
    for turn in reversed(state_turns(state)):
        params = turn.get('params') or {}
        for item in reversed(turn.get('items', [])):
            if not isinstance(item, dict):
                continue
            kind = item.get('type')
            if steering:
                if kind not in ('steeringUserMessage', 'SteeringUserMessage') or item.get('status') != 'accepted':
                    continue
            elif kind not in ('userMessage', 'UserMessage'):
                continue
            identity = item.get('clientUserMessageId') or item.get('clientId')
            if not identity and (not steering):
                identity = params.get('clientUserMessageId')
            if identity == command_id or item.get('id') == command_id:
                return {'turnId': turn.get('turnId'), 'itemId': item.get('id'), 'clientUserMessageId': command_id}
    return None

def latest_editable_user_item(state):
    if state.get('threadRuntimeStatus', {}).get('type') == 'active':
        return None
    for turn in reversed(state_turns(state)):
        if turn.get('status') == 'inProgress':
            return None
        user_items = [item for item in turn.get('items', []) if item.get('type') in ('userMessage', 'UserMessage')]
        if user_items:
            item = user_items[-1]
            raw_text = text_content(item.get('content', item.get('input', item.get('text', ''))))
            if scheduled_user_trigger(raw_text) is not None:
                return None
            text = desktop_user_text(raw_text)
            if turn.get('turnId') and item.get('id') and text:
                return {'turnId': str(turn['turnId']), 'itemId': str(item['id'])}
            return None
    return None

def edit_target(state, turn_id, item_id):
    if state.get('threadRuntimeStatus', {}).get('type') == 'active':
        raise RuntimeError('该对话正在运行，请等本轮结束后再编辑')
    turns = state_turns(state)
    if any((t.get('status') == 'inProgress' for t in turns)):
        raise RuntimeError('该对话正在运行，请等本轮结束后再编辑')
    for turn in turns:
        if turn.get('turnId') != turn_id:
            continue
        for item in turn.get('items', []):
            if item.get('id') != item_id or item.get('type') not in ('userMessage', 'UserMessage'):
                continue
            raw = text_content(item.get('content', item.get('input', item.get('text', ''))))
            if scheduled_user_trigger(raw) is None:
                return desktop_user_text(raw)
    raise RuntimeError('这条消息已变化或尚未加载，请刷新后重试')

def edit_history(agent, tid, payload, target):
    message = payload.get('text')
    if not isinstance(message, str) or not 0 < len(message.strip()) <= 32000:
        raise ValueError('重新发送的消息需为 1–32000 字符')
    for key in ('turnId', 'itemId', 'expectedLastTurnId'):
        if not isinstance(payload.get(key), str) or not 0 < len(payload[key]) <= 200:
            raise ValueError('编辑消息标识无效')
    if not isinstance(payload.get('originalText'), str) or len(payload['originalText']) > MAX_ITEM_TEXT:
        raise ValueError('缺少原消息内容，请刷新后重试')
    with agent.lock:
        state = agent.states.get(tid, {}).get('state', {})
    if not any((t.get('turnId') == payload['turnId'] for t in state_turns(state))):
        agent.ipc.request('thread-follower-load-complete-history', {'conversationId': tid}, version=1, target=target, timeout=60)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with agent.lock:
                state = agent.states.get(tid, {}).get('state', {})
            if any((t.get('turnId') == payload['turnId'] for t in state_turns(state))):
                break
            time.sleep(0.05)
    original = edit_target(state, payload['turnId'], payload['itemId'])
    turns = state_turns(state)
    if original != payload['originalText'] or not turns or turns[-1].get('turnId') != payload['expectedLastTurnId']:
        raise RuntimeError('对话内容已变化，请刷新后重试')
    params = {'conversationId': tid, 'turnId': payload['turnId'], 'message': message.strip(), 'allowEarlierMessages': True}
    tier = (state.get('latestThreadSettings') or {}).get('serviceTier')
    if isinstance(tier, str):
        params['serviceTier'] = tier
    try:
        response = agent.ipc.request('thread-follower-edit-last-user-turn', params, version=2, target=target, timeout=60)
    except RuntimeError as exc:
        if str(exc) != 'Only the most recent message can be edited.':
            raise
        result = native_conversation_ui.call({**payload, 'operation': 'edit-history', 'threadId': tid})
        return {'ok': True, 'transport': 'desktop-native-history', **result}
    if response.get('result', {}).get('ok') is not True:
        raise RuntimeError('桌面 App 没有确认编辑重发，请核对原 App；不会自动重试')
    return {'ok': True, 'transport': 'desktop-ipc', 'historyReverted': True, 'messageSent': True, 'confirmedAt': time.time()}

def native_capacity_retry(state, turns=None, observation=None):
    """Failed-capacity continuation, matching the App; never schedule a retry here."""
    turns = state_turns(state) if turns is None else turns
    turn = turns[-1] if turns else {}
    error = turn.get('error') or {}
    code = error.get('codexErrorInfo', error.get('codex_error_info')) if isinstance(error, dict) else None
    message = error.get('message', '') if isinstance(error, dict) else str(error)
    capacity = code in ('serverOverloaded', 'server_overloaded') or (isinstance(message, str) and 'selected model is at capacity' in message.lower())
    runtime = (state.get('threadRuntimeStatus') or {}).get('type')
    if not capacity or turn.get('status') != 'failed' or runtime not in ('idle', 'active', 'systemError'):
        return None
    turn_id = turn.get('turnId') or turn.get('id')
    if not turn_id:
        return None
    pending = any((isinstance(item, dict) and item.get('terminal') is not True for item in state.get('unconfirmedTurnSubmissions', []) or []))
    result = {'turnId': turn_id, 'source': 'native-error', 'automatic': None, 'countdownAvailable': False, 'pending': pending, 'canContinue': not pending and state.get('resumeState') != 'resuming'}
    if isinstance(observation, dict) and observation.get('status') == 'capacity-observed' and (observation.get('turnId') == turn_id):
        automatic = observation.get('automatic') is True
        deadline = observation.get('retryAtMs')
        valid_deadline = isinstance(deadline, (int, float)) and (not isinstance(deadline, bool)) and math.isfinite(deadline)
        result.update(source='original-app-ui', automatic=automatic, countdownAvailable=automatic and valid_deadline, retryAtMs=deadline if automatic and valid_deadline else None, pending=pending, canContinue=result['canContinue'] and (not pending))
    return result

def public_execution_state(state, turns=None, capacity_retry=None):
    """Execution is independent of delivery. Publish native evidence, not fake progress.

    In particular, active does not imply a reasoning item exists. Keep the
    waiting flags even when requests/items have not been hydrated yet.
    """
    turns = state_turns(state) if turns is None else turns
    turn = turns[-1] if turns else {}
    runtime = state.get('threadRuntimeStatus') or {}
    flags = [flag for flag in runtime.get('activeFlags', []) if flag in ('waitingOnApproval', 'waitingOnUserInput')]
    items = turn.get('items') or []
    non_user = [item for item in items if isinstance(item, dict) and item.get('type') not in ('UserMessage', 'userMessage', 'SteeringUserMessage', 'steeringUserMessage', 'HookPrompt', 'hookPrompt', 'steered')]
    pending = [r.get('method') for r in state.get('requests', []) if isinstance(r, dict)]
    in_progress = turn.get('status') in ('inProgress', 'in_progress', 'running')
    active = runtime.get('type') == 'active'
    capacity_retry = capacity_retry or native_capacity_retry(state, turns)
    if capacity_retry:
        phase = 'retrying' if capacity_retry.get('pending') else 'capacity_wait'
    elif runtime.get('type') == 'systemError' or (not active and (turn.get('status') == 'failed' or turn.get('error'))):
        phase = 'failed'
    elif 'waitingOnApproval' in flags or any((r in ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval', 'item/permissions/requestApproval', 'mcpServer/elicitation/request') for r in pending)):
        phase = 'waiting_approval'
    elif 'waitingOnUserInput' in flags or 'item/tool/requestUserInput' in pending:
        phase = 'waiting_input'
    elif state.get('resumeState') == 'resuming':
        phase = 'resuming'
    elif any((isinstance(s, dict) and s.get('terminal') is not True for s in state.get('unconfirmedTurnSubmissions', []) or [])):
        phase = 'preparing'
    elif active:
        current_items = non_user if in_progress else []
        last = current_items[-1] if current_items else {}
        kind = last.get('type', '')
        busy_statuses = ('inProgress', 'in_progress', 'running')
        terminal_statuses = ('completed', 'interrupted', 'failed', 'declined')
        busy = last.get('status') in busy_statuses
        compacting = any((item.get('type') in ('ContextCompaction', 'contextCompaction') and (item.get('status') in busy_statuses or (item.get('completed') is False and item.get('status') not in terminal_statuses)) for item in current_items))
        executing = any((item.get('type') in ('CommandExecution', 'commandExecution', 'FileChange', 'fileChange', 'McpToolCall', 'mcpToolCall', 'WebSearch', 'webSearch', 'dynamicToolCall', 'collabAgentToolCall') and item.get('status') in busy_statuses for item in current_items))
        if compacting:
            phase = 'compacting'
        elif executing or (kind in ('WebSearch', 'webSearch') and last.get('status') is None):
            phase = 'executing'
        elif kind in ('Reasoning', 'reasoning') and last.get('status') not in terminal_statuses:
            phase = 'thinking'
        elif kind in ('AgentMessage', 'agentMessage', 'AssistantMessage', 'assistantMessage'):
            completed_messages = turn.get('agentMessageCompletedAtMsById') or {}
            streaming = completed_messages.get(last.get('id')) is None and last.get('status') not in terminal_statuses and (busy or (last.get('status') is None and bool(last.get('id')) and (last.get('delivery') != 'async')))
            phase = 'responding' if streaming else 'running'
        else:
            phase = 'running' if in_progress and non_user else 'waiting_output'
    elif turn.get('status') == 'interrupted':
        phase = 'interrupted'
    elif turn.get('status') == 'completed':
        phase = 'completed'
    else:
        phase = 'idle' if runtime.get('type') == 'idle' else 'unknown'
    return {'phase': phase, 'turnId': turn.get('turnId') or turn.get('id'), 'turnStatus': turn.get('status'), 'activeFlags': flags, 'activeCommandIds': [item.get('id') for item in non_user if active and in_progress and (item.get('type') in ('CommandExecution', 'commandExecution')) and (item.get('status') in ('inProgress', 'in_progress', 'running')) and item.get('id')][-4:], 'startedAtMs': timestamp_ms(turn.get('turnStartedAtMs')), 'hasOutput': bool(non_user)}

def view_state(state, revision, owner, asset_factory=None, turn_cache=None, capacity_observation=None):
    history = state.get('turnHistory', {}).get('history', {})
    turns = state_turns(state)

    def convert_turn(turn, ti):
        items = []
        for ii, item in enumerate(turn.get('items', [])):
            normalized = normalize_item(item, f'{ti}-{ii}', asset_factory, turn_status=turn.get('status'))
            set_item_timestamp(normalized, item, turn)
            normalized['turnId'] = turn.get('turnId')
            items.append(normalized)
        if turn.get('status') == 'failed' or turn.get('error'):
            items.append(set_item_timestamp(turn_error_notice(turn.get('turnId'), turn.get('error'), ti), turn=turn))
        return compact_public_items(items)
    if turn_cache is not None and all((turn.get('turnId') for turn in turns)):
        items, total_items = turn_cache.project(turns, convert_turn)
    else:
        items = compact_public_items([item for ti, turn in enumerate(turns) for item in convert_turn(turn, ti)])
        total_items = len(items)
    recent_items = bounded_recent_items(items[-500:])
    status = state.get('threadRuntimeStatus', {}).get('type', 'unknown')
    latest_turn = turns[-1] if turns else {}
    capacity_retry = native_capacity_retry(state, turns, capacity_observation)
    editable = latest_editable_user_item(state)
    latest_settings = state.get('latestThreadSettings') or {}
    current_permissions = state.get('currentPermissions') or {}
    active_profile = latest_settings.get('activePermissionProfile') if 'activePermissionProfile' in latest_settings else current_permissions.get('activePermissionProfile')
    profile_id = active_profile.get('id') if isinstance(active_profile, dict) else active_profile
    profile_id = profile_id or latest_settings.get('permissions')
    sandbox_policy = latest_settings.get('sandboxPolicy') or current_permissions.get('sandboxPolicy') or {}
    full_access = profile_id == ':danger-full-access' or sandbox_policy.get('type') == 'dangerFullAccess'
    pending = public_pending_requests(state.get('requests', []), recent_items, state['id'])
    return {'id': state['id'], 'title': state.get('title') or '未命名对话', 'cwd': state.get('cwd', ''), 'model': state.get('latestModel'), 'reasoningEffort': state.get('latestReasoningEffort'), 'serviceTier': latest_settings.get('serviceTier') or 'default', 'fullAccess': full_access, 'nativeMetadata': public_metadata({**state, 'activeTurn': latest_turn}), 'status': status, 'executionState': public_execution_state(state, turns, capacity_retry), 'source': 'desktop-live', 'owned': bool(owner) and status != 'notLoaded', 'canContinue': status == 'idle' and latest_turn.get('status') == 'interrupted' or bool(capacity_retry and capacity_retry['canContinue']), 'capacityRetry': capacity_retry, 'editableUserMessage': editable, 'canEditHistory': bool(owner) and status not in ('active', 'notLoaded'), 'lastTurnId': latest_turn.get('turnId'), 'revision': revision, 'updated': time.time(), 'items': recent_items, 'asyncQuestionState': {'pendingQuestionIds': [question['questionItemId'] for _, questions in pending_async_questions(state) for question in questions]}, 'totalItems': total_items, 'partialHistory': not history.get('isComplete', True) or len(recent_items) < total_items, 'pendingRequests': len(pending), 'requests': pending}
PENDING_REQUEST_METHODS = {'item/commandExecution/requestApproval', 'item/fileChange/requestApproval', 'item/permissions/requestApproval', 'item/tool/requestUserInput', 'mcpServer/elicitation/request', 'item/plan/requestImplementation'}

def public_pending_requests(requests, items, thread_id):
    """Expose prompt context, but never a verification challenge or opaque secrets."""
    if not isinstance(requests, list):
        return []
    by_item = {str(item.get('id')): item for item in items if item.get('id')}
    public = []
    for request in requests[:30]:
        if not isinstance(request, dict):
            continue
        method = request.get('method')
        params = request.get('params') or {}
        request_id = request.get('id')
        if not isinstance(params, dict) or not isinstance(method, str) or request_id is None:
            continue
        if params.get('threadId') not in (None, thread_id):
            continue
        result = {'id': str(request_id), 'method': method, 'turnId': str(params.get('turnId') or ''), 'itemId': str(params.get('itemId') or '')}
        for key in ('reason', 'message', 'cwd', 'grantRoot', 'serverName', 'mode'):
            if isinstance(params.get(key), str):
                result[key] = params[key][:16000]
        item = by_item.get(result['itemId'])
        if item:
            result['item'] = {key: item[key][:30000] if isinstance(item[key], str) else item[key] for key in ('type', 'text', 'detail') if key in item}
        if method == 'item/commandExecution/requestApproval':
            for key in ('command', 'commandActions', 'proposedExecpolicyAmendment', 'networkApprovalContext', 'availableDecisions', 'additionalPermissions'):
                if key in params:
                    result[key] = params[key]
        elif method == 'item/fileChange/requestApproval':
            if 'availableDecisions' in params:
                result['availableDecisions'] = params['availableDecisions']
        elif method == 'item/permissions/requestApproval':
            result['permissions'] = params.get('permissions', {})
            for key in ('environmentId',):
                if key in params:
                    result[key] = params[key]
        elif method == 'item/tool/requestUserInput':
            result['questions'] = params.get('questions', [])
            result['autoResolutionMs'] = params.get('autoResolutionMs')
        elif method == 'mcpServer/elicitation/request':
            if params.get('mode') == 'openai/userVerification':
                result['desktopOnly'] = True
            elif params.get('mode') not in ('form', 'openai/form', 'url'):
                result['desktopOnly'] = True
            else:
                for key in ('requestedSchema', 'url', 'elicitationId'):
                    if key in params:
                        result[key] = params[key]
        elif method == 'item/plan/requestImplementation':
            result['desktopOnly'] = True
            plan = next((item for item in reversed(items) if item.get('type') == 'planImplementation' and str(item.get('turnId') or '') == result['turnId']), None)
            if plan:
                result['plan'] = str(plan.get('detail') or plan.get('text') or '')[:30000]
        else:
            result['desktopOnly'] = True
        if len(json.dumps(result, ensure_ascii=False, default=str)) > 120000:
            result = {'id': result['id'], 'method': method, 'turnId': result['turnId'], 'itemId': result['itemId'], 'desktopOnly': True, 'reason': '请求内容过大，请在桌面 App 中处理'}
        public.append(result)
    return public

def _is_requested_subset(requested, granted):
    if isinstance(requested, dict):
        return isinstance(granted, dict) and all((key in requested and _is_requested_subset(requested[key], value) for key, value in granted.items()))
    if isinstance(requested, list):
        return isinstance(granted, list) and all((any((value == original for original in requested)) for value in granted))
    return type(requested) is type(granted) and requested == granted

def pending_request_reply(method, params, payload):
    """Validate a browser decision against the exact Desktop request it answers."""
    if method not in PENDING_REQUEST_METHODS:
        raise RuntimeError('该类桌面请求暂不支持在面板中处理')
    if method == 'item/plan/requestImplementation':
        raise RuntimeError('计划执行不是确认请求，请在对话输入框中回复计划')
    response = payload.get('response')
    if not isinstance(response, dict):
        raise ValueError('待确认回复格式错误')
    if method in ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval'):
        if set(response) != {'decision'}:
            raise ValueError('确认回复包含未知字段')
        decision = response.get('decision')
        allowed = {'accept', 'acceptForSession', 'decline', 'cancel'}
        if method == 'item/commandExecution/requestApproval':
            allowed.add('acceptWithExecpolicyAmendment')
        decision_name = next(iter(decision)) if isinstance(decision, dict) and len(decision) == 1 else decision
        if not isinstance(decision_name, str) or decision_name not in allowed:
            raise ValueError('不支持的确认选项')
        offered = params.get('availableDecisions')
        if isinstance(offered, list) and offered:
            offered_names = set()
            for option in offered:
                if isinstance(option, str):
                    offered_names.add(option)
                elif isinstance(option, dict):
                    name = option.get('decision') or option.get('type')
                    if isinstance(name, str):
                        offered_names.add(name)
                    else:
                        offered_names.update((str(key) for key in option))
            if offered_names and decision_name not in offered_names:
                raise ValueError('原桌面请求未提供这个确认选项')
        if decision_name == 'acceptWithExecpolicyAmendment':
            proposed = params.get('proposedExecpolicyAmendment')
            amendment = decision['acceptWithExecpolicyAmendment']
            expected = proposed.get('execpolicy_amendment') if isinstance(proposed, dict) else proposed
            if not isinstance(expected, list) or not isinstance(amendment, dict) or amendment.get('execpolicy_amendment') != expected:
                raise ValueError('自定义规则必须与桌面请求提出的规则一致')
        elif not isinstance(decision, str):
            raise ValueError('确认选项格式错误')
        route = 'thread-follower-command-approval-decision' if method == 'item/commandExecution/requestApproval' else 'thread-follower-file-approval-decision'
        return (route, 'decision', decision)
    if method == 'item/permissions/requestApproval':
        requested = params.get('permissions', {})
        granted = response.get('permissions', {})
        if not _is_requested_subset(requested, granted):
            raise ValueError('自定义授权不得超出原请求范围')
        if response.get('scope', 'turn') not in ('turn', 'session'):
            raise ValueError('授权范围只能是本轮或当前会话')
        if set(response) - {'permissions', 'scope'}:
            raise ValueError('授权回复包含未知字段')
        return ('thread-follower-permissions-request-approval-response', 'response', {'permissions': granted, 'scope': response.get('scope', 'turn')})
    if method == 'item/tool/requestUserInput':
        questions = params.get('questions') or []
        question_ids = {str(question.get('id')) for question in questions if isinstance(question, dict) and question.get('id')}
        answers = response.get('answers')
        if not isinstance(answers, dict) or set(answers) - question_ids:
            raise ValueError('自定义答案与原问题不匹配')
        clean = {}
        for question_id, value in answers.items():
            texts = value.get('answers') if isinstance(value, dict) else None
            if not isinstance(texts, list) or not 1 <= len(texts) <= 10 or any((not isinstance(text, str) or not text.strip() or len(text) > 4000 for text in texts)):
                raise ValueError('自定义答案格式错误')
            clean[question_id] = {'answers': [text.strip() for text in texts]}
        if set(response) != {'answers'}:
            raise ValueError('提问回复包含未知字段')
        return ('thread-follower-submit-user-input', 'response', {'answers': clean})
    if params.get('mode') == 'openai/userVerification':
        raise RuntimeError('该身份验证必须在原桌面 App 完成')
    action = response.get('action')
    if action not in ('accept', 'decline', 'cancel'):
        raise ValueError('不支持的表单处理选项')
    if set(response) - {'action', 'content'}:
        raise ValueError('表单回复包含未知字段')
    content = response.get('content')
    mode = params.get('mode')
    if action == 'accept' and mode in ('form', 'openai/form'):
        if not isinstance(content, dict):
            raise ValueError('表单回复需要对象内容')
    elif content is not None:
        raise ValueError('此操作不应携带表单内容')
    if len(json.dumps(content, ensure_ascii=False)) > 16000:
        raise ValueError('表单回复过长')
    return ('thread-follower-submit-mcp-server-elicitation-response', 'response', {'action': action, 'content': content})
from crb.codex.sidebar import native_subagent_predicate, native_thread_metadata

class HostAgent:

    def __init__(self, config):
        self.cfg = config
        self.home = Path(config.get('codex_home', '~/.codex')).expanduser().resolve()
        self.lock = threading.RLock()
        self.states = {}
        self.catalog = []
        self.creation_context_ids = set()
        self.projects = []
        self.models = []
        self.paths = {}
        self.assets = {}
        self.upload_dir = Path(config['state_dir']).expanduser() / 'uploads'
        self.upload_dir.mkdir(parents=True, exist_ok=True, mode=448)
        self.wanted = set()
        self.followed = set()
        self.dirty = set()
        self.results = {}
        self.commands = queue.Queue()
        self.watch_commands = queue.Queue()
        self.read_commands = queue.Queue()
        self.attach_commands = queue.Queue()
        self.read_state_commands = queue.Queue()
        self.software_lock = threading.RLock()
        self.inflight = set()
        self.terminals = {}
        self.terminal_lock = threading.RLock()
        self.terminal_info = {'supported': True, 'shell': 'PowerShell' if os.name == 'nt' else Path(os.environ.get('SHELL') or shutil.which('zsh') or shutil.which('bash') or '/bin/sh').name, 'kind': 'powershell' if os.name == 'nt' else 'pty'}
        self.ipc = None
        self.question_ui = CodexQuestionUI()
        self.capacity_retry_observations = {}
        self.capacity_retry_attempted = {}
        self.capacity_ui_pending = {}
        self.capacity_ui_busy = False
        self.desktop_ipc = config.get('desktop_ipc') or ('\\\\.\\pipe\\codex-ipc' if os.name == 'nt' else str(self.home / 'ipc/ipc.sock'))
        if sys.platform == 'darwin':
            from crb.platform.mac_native_runtime import mac_native_bridge_directory
        self.app_tools = MacNativeAppTools(config.get('native_app_bridge_dir') or mac_native_bridge_directory(self.home)) if sys.platform == 'darwin' else DesktopAppTools(config.get('app_tools_pipe'))
        self.last_catalog = 0
        self.last_automation_poll = 0
        self.automation_signature = scheduled_automation_signature(self.home)
        self.last_models = 0
        self.model_refresh_lock = threading.Lock()
        self.model_source_records = {}
        self.model_source_signature = None
        self.next_model_source_check = 0
        self.next_model_retry = 0
        self.model_refresh_failures = 0
        self.native_identity = None
        self.native_identity_key = None
        self.last_native_identity = 0
        self.native_auth_signature = None
        self.unread_threads = set()
        self.native_read_state_available = False
        self.native_read_signature = None
        self.native_read_identity = None
        self.pending_native_read_events = {}
        self.pending_read_receipts = {}
        self.native_execution_host_key = None
        self.last_native_read_poll = 0
        self.last_sent = {}
        self.live_view_caches = {}
        self.native_live_output_available = False
        self.native_output_demand = {}
        self.native_output_overlays = {}
        self.native_output_wakeup = threading.Event()
        self.native_output_worker = None
        self.native_output_retry = {}
        self.pending_native_deliveries = {}
        self.queue_receipt_dirty = set()
        self.queue_receipt_attempts = {}
        self.receipt_wakeup = threading.Event()
        self.relay_generation = None
        self.last_error = ''
        binary = find_codex_binary(config)
        self.appserver = AppServer(binary, self.home, reuse_reads=True, binary_resolver=lambda: find_codex_binary(self.cfg)) if binary else None
        self.history_reader = NativeHistoryReader(self.home)
        self.mac_native_runtime = None
        if sys.platform == 'darwin' and self.appserver:
            from crb.platform.mac_native_runtime import MacNativeRuntime
            self.mac_native_runtime = MacNativeRuntime(self.app_tools.directory, self.home, lambda: self.appserver)
            self.app_tools.ensure_ready = self.mac_native_runtime.ensure
        self.journal = Path(config['state_dir']).expanduser() / 'commands.sqlite'
        self.journal.parent.mkdir(parents=True, exist_ok=True, mode=448)
        self.read_progress = ReadProgress(self.journal)
        self.queued_followups = self.read_native_queued_followups()
        with self.connect_journal() as db:
            db.execute('CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, result TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS refresh_state (key TEXT PRIMARY KEY, at REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS pending_native_deliveries (id TEXT PRIMARY KEY, details TEXT NOT NULL, created REAL NOT NULL)')
            db.execute('DELETE FROM pending_native_deliveries WHERE created<?', (time.time() - 86400,))
            for cid, details in db.execute('SELECT id,details FROM pending_native_deliveries'):
                try:
                    self.pending_native_deliveries[cid] = json.loads(details)
                except (TypeError, ValueError):
                    continue
            row = db.execute("SELECT at FROM refresh_state WHERE key='quota'").fetchone()
            db.execute('UPDATE commands SET result=? WHERE result IS NULL', (json.dumps({'ok': False, 'unknown': True, 'error': '桥接程序曾在提交期间重启；请核对原对话，不会自动重发'}),))
        self.cleanup_partial_uploads()
        atexit.register(self.close_all_terminals)

    def read_native_queued_followups(self):
        """Read Desktop's durable queue so a newly started relay never overwrites it."""
        path = self.home / '.codex-global-state.json'
        try:
            state = json.loads(path.read_text(encoding='utf-8'))
            queued = state.get('queued-follow-ups', {})
        except (OSError, ValueError, TypeError):
            return {}
        if not isinstance(queued, dict):
            return {}
        result = {}
        for thread_id, messages in queued.items():
            if not isinstance(thread_id, str) or not isinstance(messages, list):
                continue
            clean = [message for message in messages if isinstance(message, dict)]
            if clean:
                result[thread_id] = clean
        return result

    def refresh_native_identity(self, force=False):
        now = time.monotonic()
        try:
            stat = (self.home / 'auth.json').stat()
            signature = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            signature = None
        changed = signature != self.native_auth_signature
        self.native_auth_signature = signature
        if not force and (not changed) and (now - self.last_native_identity < 30):
            return self.native_identity_key
        self.last_native_identity = now
        if not self.appserver:
            return self.native_identity_key
        try:
            status = self.appserver.request('getAuthStatus', {'includeToken': True, 'refreshToken': False}, timeout=15)
            identity = native_auth_identity(status)
            key = native_identity_key(identity)
        except Exception:
            return self.native_identity_key
        self.native_identity = identity
        self.native_identity_key = key
        return key

    def refresh_native_read_state(self):
        """Preserve native local unread across account switches and known reading."""
        path = self.home / '.codex-global-state.json'
        if not path.is_file():
            self.native_read_state_available = False
            self.native_read_signature = None
            return None
        key = self.refresh_native_identity()
        try:
            stat = path.stat()
            signature = (key, stat.st_mtime_ns, stat.st_size)
            with self.lock:
                if signature == self.native_read_signature and self.native_read_state_available and (not any((expires <= time.monotonic() for _, _, expires in self.pending_native_read_events.values()))):
                    return set(self.unread_threads)
            state = json.loads(path.read_text(encoding='utf-8'))
            sources = native_read_sources(state)
        except (OSError, ValueError, TypeError, UnicodeError):
            sources = None
        if sources is None:
            self.native_read_state_available = False
            return None
        with self.lock:
            identity_changed = self.native_read_identity != key
            if identity_changed:
                self.pending_native_read_events = {tid: event for tid, event in self.pending_native_read_events.items() if key and event[0].startswith(key + '/')}
            self.native_execution_host_key = native_local_host_key(state, key)
            origin = f'{key}/{self.native_execution_host_key}' if key else None
            if origin:
                sources.setdefault(origin, set())
            now = time.monotonic()
            for thread_id, (event_origin, expected, expires) in list(self.pending_native_read_events.items()):
                disk = sources.get(event_origin, set())
                if (thread_id in disk) == expected or now >= expires:
                    self.pending_native_read_events.pop(thread_id, None)
                elif expected:
                    sources.setdefault(event_origin, set()).add(thread_id)
            changed = self.read_progress.observe_sources(sources, None if identity_changed else origin, self.pending_read_receipts)
            self.native_read_signature = signature
            self.native_read_identity = key
            self.native_read_state_available = True
            self.publish_read_progress(changed)
        return set(self.unread_threads)

    def publish_read_progress(self, changed=()):
        unread = self.read_progress.unread()
        self.dirty.update(changed)
        for thread in self.catalog:
            tid = thread.get('id')
            if tid and thread.get('unread') != (tid in unread):
                thread['unread'] = tid in unread
                self.dirty.add(tid)
        for tid, view in self.last_sent.items():
            if isinstance(view, dict):
                view['unread'] = tid in unread
                view['readReceipt'] = self.read_progress.receipt(tid)
        self.unread_threads = unread

    def apply_native_read_event(self, thread_id, has_unread_turn, origin=None, receipt=None):
        """Apply a validated native event or a confirmed panel reading receipt."""
        with self.lock:
            origin = origin or f'{self.native_identity_key}/{self.native_execution_host_key}'
            self.pending_native_read_events[thread_id] = (origin, bool(has_unread_turn), time.monotonic() + 10)
            captured = receipt or self.pending_read_receipts.get(thread_id)
            self.read_progress.native_event(thread_id, origin, bool(has_unread_turn), captured)
            self.native_read_state_available = True
            self.publish_read_progress({thread_id})

    def update_conversation_read_progress(self, tid):
        state = self.states[tid]['state']
        completed = next((turn.get('turnId') or turn.get('id') for turn in reversed(state.get('turns') or []) if isinstance(turn, dict) and turn.get('status') in ('completed', 'failed', 'interrupted')), None)
        if self.read_progress.observe_conversation(tid, completed, state.get('hasUnreadTurn')):
            self.native_read_state_available = True
            self.publish_read_progress({tid})

    @staticmethod
    def queue_message_text(message):
        text = message.get('text')
        if isinstance(text, str):
            return text
        context = message.get('context')
        return str(context.get('prompt', '')) if isinstance(context, dict) else ''

    @staticmethod
    def queue_message_paths(message):
        """Return local attachments in Desktop's queued-message representation."""
        context = message.get('context')
        if not isinstance(context, dict):
            return []
        attachments = []
        for value in context.get('imageAttachments', []) or []:
            if not isinstance(value, dict):
                continue
            path = value.get('localPath') or value.get('src')
            if path:
                inline = str(path).startswith('data:image/')
                name = value.get('filename') or ('图片' if inline else Path(str(path)).name) or '图片'
                attachments.append({'path': str(path), 'name': str(name), 'mime': str(value.get('mime') or (str(path)[5:].split(';', 1)[0] if inline else None) or mimetypes.guess_type(str(path))[0] or 'application/octet-stream'), 'kind': 'image'})
        for key in ('fileAttachments', 'addedFiles'):
            for value in context.get(key, []) or []:
                if not isinstance(value, dict):
                    continue
                path = value.get('fsPath') or value.get('path')
                if not path:
                    continue
                mime = str(value.get('mime') or mimetypes.guess_type(str(path))[0] or 'application/octet-stream')
                attachments.append({'path': str(path), 'name': str(value.get('label') or value.get('name') or Path(str(path)).name or '附件'), 'mime': mime, 'kind': 'image' if mime.startswith('image/') else 'file'})
        seen, result = (set(), [])
        for attachment in attachments:
            identity = (attachment['path'], attachment['name'])
            if identity in seen:
                continue
            seen.add(identity)
            result.append(attachment)
        return result

    def public_queued_followups(self, tid):
        with self.lock:
            messages = copy.deepcopy(self.queued_followups.get(tid, []))
        result = []
        for message in messages:
            message_id = str(message.get('id', ''))
            if not message_id:
                continue
            attachments = []
            for value in self.queue_message_paths(message):
                size = None
                if not value['path'].startswith('data:'):
                    try:
                        size = Path(normalize_local_path(value['path'])).stat().st_size
                    except OSError:
                        pass
                item = {'name': title_text(value['name'], '附件'), 'mime': value['mime'], 'kind': value['kind']}
                if size is not None:
                    item['size'] = size
                attachments.append(item)
            entry = {'id': message_id, 'text': self.queue_message_text(message)[:32000], 'createdAt': message.get('createdAt'), 'attachments': attachments}
            if message.get('pausedReason'):
                entry['pausedReason'] = str(message['pausedReason'])[:1000]
            result.append(entry)
        return result

    def update_native_queue(self, tid, messages, target=None):
        if not self.ipc or not self.ipc.connected:
            raise RuntimeError('请先在目标机打开 Codex 桌面 App')
        if target is None:
            owner = self.ipc.request('thread-owner-discovery', {'conversationId': tid, 'hostId': 'local'})
            target = owner['handledByClientId']
        response = self.ipc.request('thread-follower-set-queued-follow-ups-state', {'conversationId': tid, 'state': {tid: messages}}, target=target, timeout=30)
        with self.lock:
            if messages:
                self.queued_followups[tid] = copy.deepcopy(messages)
            else:
                self.queued_followups.pop(tid, None)
            self.dirty.add(tid)
            self.queue_receipt_dirty.add(tid)
        self.receipt_wakeup.set()
        return (response, target)

    @contextmanager
    def connect_journal(self):
        with closing(sqlite3.connect(self.journal, timeout=10)) as db:
            with db:
                yield db

    def close_all_terminals(self):
        with self.terminal_lock:
            sessions = list(self.terminals.values())
            self.terminals.clear()
        for session in sessions:
            session.close()

    def reap_terminals(self):
        now = time.monotonic()
        expired = []
        with self.terminal_lock:
            for session_id, session in self.terminals.items():
                ended = session.process.poll() is not None
                max_idle = 3600 if ended else 12 * 3600
                if now - session.last_used > max_idle:
                    expired.append(session_id)
            sessions = [self.terminals.pop(session_id) for session_id in expired]
        for session in sessions:
            session.close()

    def terminal_session(self, payload):
        session_id = str(payload.get('sessionId', '')).strip()
        try:
            uuid.UUID(session_id)
        except (ValueError, TypeError) as exc:
            raise ValueError('终端会话标识无效') from exc
        with self.terminal_lock:
            session = self.terminals.get(session_id)
        if not session:
            raise ValueError('终端会话已经关闭或目标代理已重启')
        return session

    def execute_terminal(self, action, payload):
        if action == 'terminal_start':
            self.reap_terminals()
            with self.terminal_lock:
                active = sum((session.process.poll() is None for session in self.terminals.values()))
            if active >= MAX_TERMINALS:
                raise RuntimeError(f'每台电脑最多同时保留 {MAX_TERMINALS} 个终端')
            cols = min(max(int(payload.get('cols', 100)), 20), 500)
            rows = min(max(int(payload.get('rows', 30)), 5), 200)
            raw_cwd = normalize_local_path(str(payload.get('cwd', '')).strip())
            cwd = Path(raw_cwd).expanduser() if raw_cwd else Path.home()
            try:
                cwd = cwd.resolve()
            except OSError:
                cwd = Path.home()
            if not cwd.is_dir():
                cwd = Path.home()
            session_id = str(uuid.uuid4())
            session = TerminalSession(session_id, cwd, cols, rows)
            with self.terminal_lock:
                self.terminals[session_id] = session
            return {'ok': True, 'sessionId': session_id, 'shell': session.shell, 'cwd': session.cwd, 'offset': 0, 'confirmedAt': time.time()}
        session = self.terminal_session(payload)
        if action == 'terminal_read':
            offset = max(int(payload.get('offset', 0)), 0)
            return {'ok': True, 'sessionId': session.id, **session.read(offset)}
        if action == 'terminal_write':
            encoded = str(payload.get('data', ''))
            if len(encoded) > 96 * 1024:
                raise ValueError('单次终端输入过长')
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError) as exc:
                raise ValueError('终端输入格式错误') from exc
            if len(data) > 64 * 1024:
                raise ValueError('单次终端输入过长')
            return {'ok': True, 'sessionId': session.id, 'written': session.write(data), 'confirmedAt': time.time()}
        if action == 'terminal_resize':
            cols = min(max(int(payload.get('cols', session.cols)), 20), 500)
            rows = min(max(int(payload.get('rows', session.rows)), 5), 200)
            session.resize(cols, rows)
            return {'ok': True, 'sessionId': session.id, 'cols': cols, 'rows': rows, 'confirmedAt': time.time()}
        if action == 'terminal_close':
            with self.terminal_lock:
                self.terminals.pop(session.id, None)
            session.close()
            return {'ok': True, 'sessionId': session.id, 'closed': True, 'confirmedAt': time.time()}
        raise ValueError('不支持的终端操作')

    def register_asset(self, tid, source, kind=None, name=None):
        """Register only files already referenced by this Codex conversation."""
        source = str(source or '').strip()
        if not source:
            return None
        data = None
        path = None
        mime = 'application/octet-stream'
        unavailable = ''
        parsed = urlsplit(source)
        if parsed.scheme in ('http', 'https'):
            guessed = mimetypes.guess_type(parsed.path)[0] or mime
            external_name = title_text(Path(unquote(parsed.path)).name, name, '外部文件')
            descriptor = {'id': 'external-' + hashlib.sha256(source.encode()).hexdigest()[:24], 'name': external_name, 'mime': guessed, 'kind': kind or ('image' if guessed.startswith('image/') else 'file'), 'url': source, 'available': True, 'external': True}
            if name and name != external_name:
                descriptor['label'] = title_text(name)
            return descriptor
        if source.startswith('data:'):
            try:
                header, payload = source.split(',', 1)
                mime = header[5:].split(';', 1)[0] or mime
                data = base64.b64decode(payload, validate=False) if ';base64' in header else unquote_to_bytes(payload)
            except (ValueError, TypeError):
                unavailable = '图片数据格式无法识别'
        else:
            if parsed.scheme == 'file':
                raw_path = unquote(parsed.path)
                if os.name == 'nt' and re.match('^/[A-Za-z]:', raw_path):
                    raw_path = raw_path[1:]
                if parsed.netloc:
                    raw_path = '//' + parsed.netloc + raw_path if os.name == 'nt' else raw_path
            else:
                raw_path = unquote(source.split('#', 1)[0])
            candidate = Path(normalize_local_path(raw_path)).expanduser()
            if not candidate.is_file():
                without_line = re.sub(':\\d+(?::\\d+)?$', '', raw_path)
                candidate = Path(without_line).expanduser()
            try:
                path = candidate.resolve()
                if not path.is_file():
                    unavailable = '目标机上的原文件已不存在'
            except OSError:
                unavailable = '目标机上的原文件无法读取'
        asset_name = title_text(path.name if path else None, name, '对话附件')
        asset_label = title_text(name) if name and name != asset_name else None
        if path:
            mime = mimetypes.guess_type(path.name)[0] or mime
            try:
                size = path.stat().st_size
            except OSError:
                size, unavailable = (0, '目标机上的原文件无法读取')
            identity = f"{path}\x00{size}\x00{getattr(path.stat(), 'st_mtime_ns', 0)}" if not unavailable else str(path)
        else:
            size = len(data or b'')
            identity = hashlib.sha256(data or source.encode()).hexdigest()
        asset_kind = kind or ('image' if mime.startswith('image/') else 'file')
        asset_id = hashlib.sha256(f'{tid}\x00{identity}'.encode()).hexdigest()[:32]
        if size > MAX_ASSET_SIZE:
            unavailable = f'文件超过网页传输上限（{MAX_ASSET_SIZE // (1024 * 1024)} MB）'
        descriptor = {'id': asset_id, 'name': asset_name, 'mime': mime, 'kind': asset_kind, 'size': size, 'available': not bool(unavailable)}
        if asset_label:
            descriptor['label'] = asset_label
        if unavailable:
            descriptor['error'] = unavailable
            return descriptor
        with self.lock:
            self.assets[tid, asset_id] = {'path': str(path) if path else None, 'data': data, 'name': asset_name, 'mime': mime, 'kind': asset_kind, 'size': size, 'seen': time.monotonic()}
            if len(self.assets) > 5000:
                oldest = sorted(self.assets, key=lambda key: self.assets[key]['seen'])[:500]
                for key in oldest:
                    self.assets.pop(key, None)
        return descriptor

    def fetch_asset(self, tid, payload):
        asset_id = str(payload.get('assetId', ''))
        try:
            offset = int(payload.get('offset', 0))
            requested = int(payload.get('limit', MAX_ASSET_CHUNK))
        except (TypeError, ValueError) as exc:
            raise ValueError('附件分片参数无效') from exc
        if offset < 0 or requested <= 0:
            raise ValueError('附件分片参数无效')
        limit = min(requested, MAX_ASSET_CHUNK)
        with self.lock:
            asset = self.assets.get((tid, asset_id))
            if asset:
                asset['seen'] = time.monotonic()
                asset = dict(asset)
        if not asset:
            raise ValueError('附件索引已过期，请刷新该对话后重试')
        if asset['size'] > MAX_ASSET_SIZE:
            raise ValueError('附件超过网页传输上限')
        if offset > asset['size']:
            raise ValueError('附件分片位置无效')
        if payload.get('resolveOnly') is True:
            if not asset.get('path'):
                raise ValueError('此附件没有本地文件路径，请先下载到普通目录后打开')
            return {'ok': True, 'path': asset['path'], 'name': asset['name'], 'viewOnly': False}
        if asset.get('data') is not None:
            chunk = asset['data'][offset:offset + limit]
        else:
            path = Path(asset['path'])
            try:
                if not path.is_file() or path.stat().st_size != asset['size']:
                    raise ValueError('目标机上的附件已经变化，请刷新该对话')
                with path.open('rb') as stream:
                    stream.seek(offset)
                    chunk = stream.read(limit)
            except OSError as exc:
                raise ValueError('无法读取目标机上的附件') from exc
        next_offset = offset + len(chunk)
        return {'ok': True, 'transport': 'relay-asset', 'asset': {'id': asset_id, 'name': asset['name'], 'mime': asset['mime'], 'kind': asset['kind'], 'size': asset['size']}, 'offset': offset, 'nextOffset': next_offset, 'eof': next_offset >= asset['size'], 'data': base64.b64encode(chunk).decode('ascii')}

    def cleanup_partial_uploads(self):
        cutoff = time.time() - 86400
        try:
            candidates = list(self.upload_dir.glob('*.part'))
        except OSError:
            return
        for path in candidates:
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    metadata = path.with_suffix('.json')
                    if metadata.is_file():
                        metadata.unlink()
            except OSError:
                continue

    def upload_asset_chunk(self, tid, payload):
        upload_id = str(payload.get('uploadId', ''))
        try:
            uuid.UUID(upload_id)
            offset = int(payload.get('offset', -1))
            expected_size = int(payload.get('size', -1))
        except (ValueError, TypeError) as exc:
            raise ValueError('上传分片参数无效') from exc
        if expected_size < 0 or offset < 0:
            raise ValueError('上传分片参数无效')
        name = safe_upload_name(payload.get('name'))
        mime = str(payload.get('mime') or mimetypes.guess_type(name)[0] or 'application/octet-stream')[:200]
        encoded = payload.get('data', '')
        if not isinstance(encoded, str):
            raise ValueError('上传分片格式无效')
        try:
            chunk = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError('上传分片编码无效') from exc
        if len(chunk) > MAX_UPLOAD_CHUNK:
            raise ValueError('上传分片过大')
        part = self.upload_dir / f'{upload_id}.part'
        metadata = self.upload_dir / f'{upload_id}.json'
        if offset == 0:
            available = max(0, shutil.disk_usage(self.upload_dir).free - UPLOAD_DISK_RESERVE)
            if expected_size > available:
                raise ValueError('目标机剩余磁盘空间不足，无法保存这个附件')
            record = {'threadId': tid, 'name': name, 'mime': mime, 'size': expected_size, 'complete': False}
            metadata.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
            os.chmod(metadata, 384)
            mode = 'wb'
        else:
            try:
                record = json.loads(metadata.read_text(encoding='utf-8'))
            except (OSError, ValueError) as exc:
                raise ValueError('上传会话已过期，请重新选择附件') from exc
            if record.get('threadId') != tid or record.get('name') != name or record.get('mime') != mime or (record.get('size') != expected_size):
                raise ValueError('上传附件信息不匹配')
            mode = 'ab'
        current_size = part.stat().st_size if part.is_file() else 0
        if current_size != offset or offset + len(chunk) > expected_size:
            raise ValueError('上传分片顺序或大小无效')
        available = max(0, shutil.disk_usage(self.upload_dir).free - UPLOAD_DISK_RESERVE)
        if len(chunk) > available:
            raise ValueError('目标机剩余磁盘空间不足，附件上传已停止')
        with part.open(mode) as stream:
            stream.write(chunk)
        complete = bool(payload.get('eof'))
        if complete:
            if part.stat().st_size != expected_size:
                raise ValueError('附件尚未上传完整')
            suffix = Path(name).suffix[:20]
            final_path = self.upload_dir / f'{upload_id}{suffix}'
            os.replace(part, final_path)
            os.chmod(final_path, 384)
            record['complete'] = True
            record['path'] = str(final_path)
            metadata.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
        return {'ok': True, 'transport': 'relay-upload', 'uploadId': upload_id, 'name': name, 'mime': mime, 'size': expected_size, 'received': offset + len(chunk), 'complete': complete, 'confirmedAt': time.time()}

    @staticmethod
    def editable_path(value):
        raw = str(value or '')
        if not raw or len(raw) > 4096 or '\x00' in raw:
            raise ValueError('文件路径无效')
        source = Path(raw).expanduser()
        if source.is_symlink():
            raise ValueError('不直接编辑符号链接，请打开它指向的文件')
        path = source.resolve(strict=True)
        if not path.is_file():
            raise ValueError('不是普通文件')
        return path

    def read_text_file(self, payload):
        path = self.editable_path(payload.get('path'))
        with path.open('rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('不是普通文件')
            data = stream.read(MAX_EDITOR_BYTES + 1)
        if len(data) > MAX_EDITOR_BYTES:
            raise ValueError('在线编辑上限为 512 KB；可预览或下载完整文件')
        if b'\x00' in data:
            raise ValueError('二进制文件不能作为文本编辑')
        try:
            content = data.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ValueError('在线编辑仅支持 UTF-8 文本') from exc
        return {'ok': True, 'path': str(path), 'content': content, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}

    def write_text_file(self, payload):
        path = self.editable_path(payload.get('path'))
        expected = str(payload.get('expectedSha256') or '')
        content = payload.get('content')
        if not re.fullmatch('[0-9a-f]{64}', expected) or not isinstance(content, str):
            raise ValueError('保存参数无效')
        with path.open('rb') as stream:
            original_stat = os.fstat(stream.fileno())
            if not stat.S_ISREG(original_stat.st_mode):
                raise ValueError('不是普通文件')
            original = stream.read(MAX_EDITOR_BYTES + 1)
        if len(original) > MAX_EDITOR_BYTES or b'\x00' in original:
            raise ValueError('目标文件已不适合在线编辑')
        if hashlib.sha256(original).hexdigest() != expected:
            raise ValueError('文件已被其他程序修改；请重新打开后再编辑')
        try:
            original.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ValueError('在线编辑仅支持 UTF-8 文本') from exc
        if original.startswith(b'\xef\xbb\xbf') and (not content.startswith('\ufeff')):
            content = '\ufeff' + content
        if b'\r\n' in original:
            content = content.replace('\r\n', '\n').replace('\n', '\r\n')
        data = content.encode('utf-8')
        if len(data) > MAX_EDITOR_BYTES:
            raise ValueError('保存内容超过 512 KB 上限')
        fd, temp_name = tempfile.mkstemp(prefix=f'.{path.name}.codex-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp_name, stat.S_IMODE(original_stat.st_mode))
            if path.is_symlink() or not path.is_file():
                raise ValueError('目标文件已改变，请重新打开')
            with path.open('rb') as stream:
                if hashlib.sha256(stream.read(MAX_EDITOR_BYTES + 1)).hexdigest() != expected:
                    raise ValueError('文件已被其他程序修改；请重新打开后再编辑')
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return {'ok': True, 'path': str(path), 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}

    def attach_local_file(self, tid, payload):
        if not tid:
            raise ValueError('没有指定目标对话')
        path = self.editable_path(payload.get('path'))
        upload_id = str(uuid.uuid4())
        name = safe_upload_name(path.name)
        mime = mimetypes.guess_type(name)[0] or 'application/octet-stream'
        part = self.upload_dir / f'{upload_id}.part'
        final_path = self.upload_dir / f'{upload_id}{Path(name).suffix[:20]}'
        metadata = self.upload_dir / f'{upload_id}.json'
        try:
            with path.open('rb') as source:
                before = os.fstat(source.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError('不是普通文件')
                if before.st_size > MAX_ASSET_SIZE:
                    raise ValueError('加入对话的文件上限为 256 MB')
                available = max(0, shutil.disk_usage(self.upload_dir).free - UPLOAD_DISK_RESERVE)
                if before.st_size > available:
                    raise ValueError('目标机剩余磁盘空间不足，无法暂存附件')
                with part.open('xb') as target:
                    shutil.copyfileobj(source, target, 1024 * 1024)
                after = os.fstat(source.fileno())
                if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns or part.stat().st_size != before.st_size:
                    raise ValueError('复制期间文件发生变化，请重新加入')
            os.replace(part, final_path)
            os.chmod(final_path, 384)
            record = {'threadId': tid, 'name': name, 'mime': mime, 'size': before.st_size, 'complete': True, 'path': str(final_path)}
            metadata.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
            os.chmod(metadata, 384)
        except Exception:
            for leftover in (part, final_path, metadata):
                leftover.unlink(missing_ok=True)
            raise
        return {'ok': True, 'uploadId': upload_id, 'name': name, 'mime': mime, 'size': before.st_size, 'complete': True}

    def resolve_uploaded_inputs(self, tid, attachments):
        if not isinstance(attachments, list):
            raise ValueError('附件引用格式无效')
        inputs, native_attachments = ([], [])
        for attachment in attachments:
            if not isinstance(attachment, dict):
                raise ValueError('附件引用格式无效')
            upload_id = str(attachment.get('uploadId', ''))
            try:
                uuid.UUID(upload_id)
                record = json.loads((self.upload_dir / f'{upload_id}.json').read_text(encoding='utf-8'))
            except (ValueError, TypeError, OSError) as exc:
                raise ValueError('附件上传记录不存在，请重新选择附件') from exc
            if record.get('threadId') != tid or not record.get('complete'):
                raise ValueError('附件未完成上传或不属于当前对话')
            path = Path(record.get('path', ''))
            if not path_belongs_to(path, self.upload_dir) or not path.is_file() or path.stat().st_size != record.get('size'):
                raise ValueError('目标机上的上传附件已失效')
            mime = str(record.get('mime') or 'application/octet-stream')
            if mime.startswith('image/'):
                inputs.append({'type': 'localImage', 'path': str(path), 'detail': 'auto'})
            else:
                inputs.append({'type': 'mention', 'name': record.get('name') or path.name, 'path': str(path)})
            native = {'label': record.get('name') or path.name, 'path': str(path), 'fsPath': str(path)}
            if mime.startswith('image/'):
                native['isImageAttachment'] = True
            native_attachments.append(native)
        return (inputs, native_attachments)

    @staticmethod
    def native_image_attachment(path, name, image_id=None):
        """Keep Desktop's editable preview separate from its engine-local path."""
        path = Path(path)
        with path.open('rb') as stream:
            data = stream.read(MAX_ASSET_SIZE + 1)
        if len(data) > MAX_ASSET_SIZE:
            raise ValueError('图片超过网页传输上限')
        mime = mimetypes.guess_type(name)[0] or mimetypes.guess_type(path.name)[0]
        if not mime or not mime.startswith('image/'):
            mime = next((kind for signature, kind in ((b'\x89PNG\r\n\x1a\n', 'image/png'), (b'\xff\xd8\xff', 'image/jpeg'), (b'GIF87a', 'image/gif'), (b'GIF89a', 'image/gif'), (b'BM', 'image/bmp')) if data.startswith(signature)), None)
            if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
                mime = 'image/webp'
        if not mime or not mime.startswith('image/'):
            raise ValueError('无法识别图片格式，请重新选择图片')
        source = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
        return {'id': image_id or 'crb-image-' + hashlib.sha256(str(path).encode()).hexdigest(), 'src': source, 'previewSrc': source, 'localPath': str(path), 'filename': name, 'uploadStatus': 'idle'}

    def prepare_queued_images(self, message):
        """Repair only Relay-owned legacy previews, never rewrite native images."""
        message = copy.deepcopy(message)
        context = message.get('context')
        if not isinstance(context, dict):
            return message
        for image in context.get('imageAttachments', []) or []:
            if not isinstance(image, dict):
                continue
            source = str(image.get('src') or '')
            raw_path = image.get('localPath') or source
            if not _local_reference(raw_path):
                continue
            path = Path(normalize_local_path(raw_path)).expanduser()
            if not path_belongs_to(path, self.upload_dir) or (image.get('id') and source.startswith('data:image/') and image.get('previewSrc')):
                continue
            if not path.is_file():
                raise ValueError('队列图片已经失效，请重新选择图片')
            image.update(self.native_image_attachment(path, str(image.get('filename') or path.name), image.get('id')))
        return message

    @staticmethod
    def make_queued_message(tid, message_id, text, state, uploaded_inputs, native_attachments, *, turn_trigger='composer_queue'):
        image_attachments, file_attachments = ([], [])
        for value, native in zip(uploaded_inputs, native_attachments):
            path = str(native.get('fsPath') or native.get('path') or '')
            name = str(native.get('label') or Path(path).name or '附件')
            if value.get('type') == 'localImage':
                image_attachments.append(HostAgent.native_image_attachment(path, name))
            else:
                file_attachments.append({'label': name, 'path': path, 'fsPath': path})
        cwd = str(state.get('cwd') or '')
        context = {'prompt': text, 'workspaceRoots': [cwd] if cwd else [], 'ideContext': None, 'imageAttachments': image_attachments, 'fileAttachments': file_attachments, 'pastedTextAttachments': [], 'uploadedFileAttachments': [], 'addedFiles': [], 'selectedTextAttachments': [], 'commentAttachments': [], 'mcpAppModelContextAttachments': [], 'appshotContexts': []}
        if turn_trigger:
            context['turnTrigger'] = turn_trigger
        collaboration_mode = state.get('latestCollaborationMode')
        if isinstance(collaboration_mode, dict):
            context['collaborationMode'] = copy.deepcopy(collaboration_mode)
        return {'id': message_id, 'text': text, 'context': context, 'cwd': cwd, 'createdAt': int(time.time() * 1000)}

    @classmethod
    def queued_message_inputs(cls, message):
        text = cls.queue_message_text(message).strip()
        inputs = [{'type': 'text', 'text': text, 'text_elements': []}] if text else []
        native_attachments = []
        for attachment in cls.queue_message_paths(message):
            if attachment['kind'] == 'image' and attachment['path'].startswith('data:image/'):
                inputs.append({'type': 'image', 'url': attachment['path']})
                continue
            path = Path(normalize_local_path(attachment['path'])).expanduser()
            try:
                path = path.resolve()
            except OSError:
                pass
            if not path.is_file():
                raise ValueError(f"队列附件已经失效：{attachment['name']}")
            if attachment['kind'] == 'image':
                inputs.append({'type': 'localImage', 'path': str(path), 'detail': 'auto'})
            else:
                inputs.append({'type': 'mention', 'name': attachment['name'], 'path': str(path)})
            native = {'label': attachment['name'], 'path': str(path), 'fsPath': str(path)}
            if attachment['kind'] == 'image':
                native['isImageAttachment'] = True
            native_attachments.append(native)
        if not inputs:
            raise ValueError('队列消息没有可发送的文字或附件')
        return (text, inputs, native_attachments)

    def announce_desktop_following(self, tid, target_client_id=None):
        """Announce a subscription without holding the conversation state lock.

        Reserve the marker before the write, so a disconnect/reset arriving
        during that write can invalidate it instead of being overwritten.
        This is a broadcast, never a blocking request from the IPC reader.
        """
        with self.lock:
            ipc = self.ipc
            if not ipc or not ipc.connected:
                return False
            self.followed.add(tid)
        try:
            if target_client_id:
                ipc.broadcast('thread-stream-following-changed', {'conversationId': tid, 'hostId': 'local', 'following': True}, version=1, target_client_ids=[target_client_id])
            else:
                ipc.follow(tid)
        except Exception:
            with self.lock:
                if self.ipc is ipc:
                    self.followed.discard(tid)
            raise
        return True

    def on_event(self, msg):
        if msg.get('type') == 'broadcast' and msg.get('method') == 'thread-stream-following-status-requested':
            p = msg.get('params') or {}
            tid, owner = (p.get('conversationId'), msg.get('sourceClientId'))
            if msg.get('version') != 1 or p.get('hostId', 'local') != 'local' or (not isinstance(tid, str)) or (not tid) or (not isinstance(owner, str)) or (not owner):
                return
            with self.lock:
                wanted = tid in self.wanted or tid in self.followed
            if wanted:
                try:
                    self.announce_desktop_following(tid, owner)
                except OSError as exc:
                    print('Desktop subscription:', type(exc).__name__, flush=True)
            return
        ui_changed = False
        with self.lock:
            if msg.get('type') == 'disconnected' or (msg.get('type') == 'broadcast' and msg.get('method') == 'ipc-connection-reset' and (msg.get('version') == 1)):
                for tid, slot in self.states.items():
                    slot['owner'] = None
                    self.dirty.add(tid)
                self.followed.clear()
                return
            if msg.get('type') != 'broadcast':
                return
            p = msg.get('params', {})
            method = msg.get('method')
            if method == 'thread-queued-followups-changed' and msg.get('version') == 2:
                tid = p.get('conversationId')
                messages = p.get('messages')
                if p.get('hostId', 'local') == 'local' and tid and isinstance(messages, list) and all((isinstance(message, dict) for message in messages)):
                    if messages:
                        self.queued_followups[tid] = copy.deepcopy(messages)
                    else:
                        self.queued_followups.pop(tid, None)
                    self.dirty.add(tid)
                    self.queue_receipt_dirty.add(tid)
                    self.receipt_wakeup.set()
                return
            if method == 'thread-read-state-changed' and msg.get('version') == 3:
                tid = p.get('conversationId')
                context = p.get('context')
                identity = native_identity_key(context.get('identity')) if isinstance(context, dict) else None
                if p.get('hostId', 'local') == 'local' and tid and identity and (identity == self.native_identity_key) and self.native_execution_host_key and (context.get('executionHostKey') == self.native_execution_host_key) and isinstance(p.get('hasUnreadTurn'), bool):
                    origin = f'{identity}/{self.native_execution_host_key}'
                    self.apply_native_read_event(tid, p['hasUnreadTurn'], origin)
                return
            if method == 'client-status-changed' and p.get('status') == 'disconnected':
                for tid, slot in self.states.items():
                    if slot['owner'] == p.get('clientId'):
                        slot['owner'] = None
                        self.followed.discard(tid)
                        self.dirty.add(tid)
                return
            tid = p.get('conversationId')
            if p.get('hostId', 'local') != 'local' or not tid:
                return
            if method != 'thread-stream-state-changed' or msg.get('version') != 11:
                return
            ch = p.get('change', {})
            if ch.get('type') == 'snapshot':
                self.states[tid] = {'state': ch['conversationState'], 'revision': ch['revision'], 'owner': msg.get('sourceClientId')}
                self.update_conversation_read_progress(tid)
                self.dirty.add(tid)
                ui_changed = True
            elif ch.get('type') == 'patches':
                slot = self.states.get(tid)
                if not slot or slot['owner'] != msg.get('sourceClientId'):
                    if slot and slot['owner'] is None:
                        self.followed.discard(tid)
                    return
                if slot['revision'] != ch.get('baseRevision'):
                    self.followed.discard(tid)
                    return
                try:
                    slot['state'] = apply_patches(slot['state'], ch.get('patches', []), copy_on_write=True)
                    slot['revision'] = ch['revision']
                    self.update_conversation_read_progress(tid)
                    self.dirty.add(tid)
                    ui_changed = True
                except (KeyError, IndexError, TypeError, ValueError):
                    self.followed.discard(tid)
        if ui_changed and getattr(self, 'question_ui', None):
            self.question_ui.notify_native_change(tid)
            self.observe_capacity_retry(tid)
        if ui_changed and getattr(self, 'receipt_wakeup', None):
            with self.lock:
                self.queue_receipt_dirty.add(tid)
            self.receipt_wakeup.set()
        if ui_changed and tid in getattr(self, 'native_output_demand', {}):
            self.native_output_wakeup.set()

    def read_catalog(self):
        self.refresh_native_read_state()
        scheduled = scheduled_thread_automations(self.home)
        dbs = sorted(self.home.glob('state_*.sqlite'), key=lambda p: p.stat().st_mtime, reverse=True)
        if not dbs:
            raise RuntimeError('没有找到 Codex 本地会话数据库')
        database_uri = dbs[0].resolve().as_uri() + '?mode=ro'
        with closing(sqlite3.connect(database_uri, uri=True, timeout=2)) as db:
            db.row_factory = sqlite3.Row
            columns = {row[1] for row in db.execute('PRAGMA table_info(threads)')}
            optional = {'source': 'native_source', 'is_pinned': 'native_pinned', 'agent_path': 'native_agent_path', 'agent_nickname': 'native_agent_nickname', 'agent_role': 'native_agent_role', 'thread_section_id': 'native_section'}
            extra = ','.join(((key if key in columns else 'NULL') + ' AS ' + alias for key, alias in optional.items()))
            query = 'SELECT id,name,title,first_user_message,cwd,model,reasoning_effort,updated_at,rollout_path,archived,project_id,' + extra + ' FROM threads WHERE '
            predicate = native_subagent_predicate(columns)
            rows = db.execute(query + 'NOT (' + predicate + ') ORDER BY archived ASC,updated_at DESC LIMIT 1000').fetchall()
            rows += db.execute(query + predicate + ' ORDER BY archived ASC,updated_at DESC LIMIT 1000').fetchall()
            try:
                sections = dict(db.execute('SELECT id,name FROM thread_sections'))
            except sqlite3.OperationalError:
                sections = {}
            try:
                project_rows = db.execute('SELECT id,name,position FROM projects ORDER BY position,id').fetchall()
                root_rows = db.execute('SELECT project_id,path,position FROM project_roots ORDER BY project_id,position').fetchall()
            except sqlite3.OperationalError:
                project_rows, root_rows = ([], [])
        with self.lock:
            runtime = getattr(self, 'mac_native_runtime', None)
            internal_id = runtime.context_id if runtime else ''
            rows = [row for row in rows if row['id'] not in self.creation_context_ids and (not (row['id'] == internal_id and (not row['first_user_message'])))]
        roots = {}
        for row in root_rows:
            roots.setdefault(row['project_id'], []).append(row['path'])
        projects = [{'id': row['id'], 'name': title_text(row['name']), 'position': row['position'], 'roots': roots.get(row['id'], []), 'threadCount': 0} for row in project_rows]
        projects_by_id = {project['id']: project for project in projects}
        memberships = thread_project_memberships(self.home, projects_by_id)
        root_matches = sorted(((os.path.normcase(os.path.abspath(normalize_local_path(root))), project['id']) for project in projects for root in project['roots'] if root), key=lambda item: len(item[0]), reverse=True)
        with self.lock:
            read_state_available = self.native_read_state_available
            unread_threads = set(self.unread_threads)
            desktop_connected = bool(self.ipc and self.ipc.connected)
            live_statuses = {thread_id: slot.get('state', {}).get('threadRuntimeStatus', {}).get('type', 'unknown') for thread_id, slot in self.states.items() if desktop_connected and slot.get('owner')}
            completion_notifications = {thread_id: scheduled_completion_notification(slot.get('state', {})) for thread_id, slot in self.states.items() if desktop_connected and slot.get('owner')}
        catalog = []
        for row in rows:
            sidebar = native_thread_metadata(row, sections)
            project_id = row['project_id'] if row['project_id'] in projects_by_id else None
            if row['id'] in memberships:
                project_id = memberships[row['id']]
            elif project_id is None:
                matches = [(root, pid) for root, pid in root_matches if path_belongs_to(row['cwd'], root)]
                longest = len(matches[0][0]) if matches else 0
                candidates = {pid for root, pid in matches if len(root) == longest}
                project_id = next(iter(candidates)) if len(candidates) == 1 else None
            if project_id and (not sidebar['isSubagent']):
                projects_by_id[project_id]['threadCount'] += 1
            catalog.append({'id': row['id'], 'title': title_text(row['name'], row['title'], row['first_user_message']), 'cwd': row['cwd'] or '', 'model': row['model'], 'reasoningEffort': row['reasoning_effort'], 'updated_at': row['updated_at'], 'archived': bool(row['archived']), 'projectId': project_id, 'projectName': projects_by_id.get(project_id, {}).get('name', '未分类'), 'status': live_statuses.get(row['id'], 'notLoaded'), 'unread': row['id'] in unread_threads if read_state_available else None, 'automations': scheduled.get(row['id'], []), 'stateSource': 'codex-desktop', **sidebar})
            notification = completion_notifications.get(row['id'])
            if notification is not None:
                catalog[-1]['completionNotification'] = notification
        with self.lock:
            self.catalog = catalog
            self.projects = projects
            self.paths = {row['id']: row['rollout_path'] for row in rows}
            for thread in catalog:
                view = self.last_sent.get(thread['id'])
                if not isinstance(view, dict):
                    continue
                view['unread'] = thread['unread']
                if not view.get('owned'):
                    view['status'] = thread['status']
            recent = [row['id'] for row in catalog if not row['archived'] and (not row['isSubagent'])][:12]
            self.wanted.update(recent)
            self.last_catalog = time.monotonic()

    def model_file_digest(self, name):
        """Read changed local sources only; never publish credentials or cache data."""
        path = self.home / name
        try:
            stat = path.stat()
            stamp = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
        except OSError:
            stamp = None
        previous = self.model_source_records.get(name)
        if previous and previous[0] == stamp:
            return previous[1]
        digest = None
        if stamp is not None:
            try:
                if stat.st_size > 8 * 1024 * 1024:
                    raise ValueError('模型来源文件过大')
                text = path.read_text(encoding='utf-8')
                if name == 'models_cache.json':
                    data = json.loads(text)
                    value = [data.get('identity'), data.get('client_version'), data.get('models')]
                elif name == 'auth.json':
                    data = json.loads(text)
                    tokens = data.get('tokens') or {}
                    identity = native_auth_identity({'authMethod': 'chatgpt', 'authToken': tokens.get('id_token', '')})
                    value = [data.get('auth_mode'), tokens.get('account_id'), identity, data.get('OPENAI_API_KEY')]
                else:
                    value = text
                digest = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            except (OSError, ValueError, TypeError, AttributeError, UnicodeError):
                digest = ('unavailable', stamp)
        self.model_source_records[name] = (stamp, digest)
        return digest

    def maybe_refresh_models(self):
        """Change-driven catalog queries; metadata checks stay local and cheap."""
        now = time.monotonic()
        if now < self.next_model_source_check:
            return False
        if not self.model_refresh_lock.acquire(blocking=False):
            return False
        try:
            self.next_model_source_check = now + MODEL_SOURCE_CHECK_INTERVAL
            binary = find_codex_binary(self.cfg)
            try:
                stat = Path(binary).stat() if binary else None
                binary_signature = (str(binary), stat.st_ino, stat.st_mtime_ns, stat.st_size) if stat else None
            except OSError:
                binary_signature = None
            signature = (binary_signature, *(self.model_file_digest(name) for name in ('models_cache.json', 'auth.json', 'config.toml')))
            changed = signature != self.model_source_signature
            if changed:
                if self.model_source_signature is not None and signature[2] != self.model_source_signature[2]:
                    with self.lock:
                        self.models = []
                self.model_source_signature = signature
                self.next_model_retry = 0
                self.model_refresh_failures = 0
            due = changed or self.last_models == 0 or self.model_refresh_failures or (now - self.last_models >= MODEL_REFRESH_FALLBACK_INTERVAL)
            if not due or now < self.next_model_retry:
                return False
            try:
                if not self.appserver and binary:
                    self.appserver = AppServer(binary, self.home, reuse_reads=True, binary_resolver=lambda: find_codex_binary(self.cfg))
                self.refresh_models()
                self.model_refresh_failures = 0
                self.next_model_retry = 0
                return True
            except Exception as exc:
                self.model_refresh_failures = min(self.model_refresh_failures + 1, 6)
                delay = min(MODEL_REFRESH_RETRY_MAX, MODEL_REFRESH_RETRY_MIN * 2 ** (self.model_refresh_failures - 1))
                self.next_model_retry = time.monotonic() + delay
                print('Model catalog:', type(exc).__name__, f'(retry in {delay}s)', flush=True)
                return False
        finally:
            self.model_refresh_lock.release()

    def refresh_models(self):
        if not self.appserver:
            raise FileNotFoundError('没有找到当前 Codex 模型接口')
        result = self.appserver.request('model/list', {'cursor': None, 'limit': 100, 'includeHidden': False}, timeout=30)
        models = []
        for model in result.get('data', []):
            if model.get('hidden'):
                continue
            efforts = [{'id': item.get('reasoningEffort'), 'description': item.get('description', '')} for item in model.get('supportedReasoningEfforts', []) if item.get('reasoningEffort')]
            tiers = [{'id': 'default', 'name': 'Standard', 'description': 'Default speed'}]
            seen_tiers = {'default'}
            native_tiers = model.get('serviceTiers') or []
            if not native_tiers:
                native_tiers = [{'id': tier, 'name': tier, 'description': ''} for tier in model.get('additionalSpeedTiers', []) if isinstance(tier, str)]
            for tier in native_tiers:
                if not isinstance(tier, dict):
                    continue
                tier_id = tier.get('id')
                if not isinstance(tier_id, str) or not 0 < len(tier_id) <= 64 or tier_id in seen_tiers:
                    continue
                seen_tiers.add(tier_id)
                tiers.append({'id': tier_id, 'name': str(tier.get('name') or tier_id)[:100], 'description': str(tier.get('description') or '')[:1000]})
            models.append({'id': model.get('model') or model.get('id'), 'label': model.get('displayName') or model.get('model'), 'description': model.get('description', ''), 'defaultEffort': model.get('defaultReasoningEffort'), 'isDefault': bool(model.get('isDefault')), 'efforts': efforts, 'serviceTiers': tiers, 'defaultServiceTier': model.get('defaultServiceTier')})
        with self.lock:
            self.models = models
            self.last_models = time.monotonic()

    def refresh_thread_snapshot(self, tid):
        """One explicit, read-only refresh of the existing Desktop stream."""
        with self.lock:
            meta = next((row for row in self.catalog if row['id'] == tid), None)
            previous = self.states.get(tid)
            live = bool(previous and previous.get('owner'))
            connected = bool(self.ipc and self.ipc.connected)
        if not meta:
            raise ValueError('没有找到目标对话')
        if connected and (not meta.get('archived')):
            with self.lock:
                self.wanted.add(tid)
                self.followed.add(tid)
            self.ipc.follow(tid, following=False)
            self.ipc.follow(tid, following=True)
            deadline = time.monotonic() + 8
            while True:
                with self.lock:
                    slot = self.states.get(tid)
                    fresh = bool(slot and slot is not previous and slot.get('owner'))
                    if fresh:
                        factory = lambda source, kind=None, name=None: self.register_asset(tid, source, kind, name)
                        view = view_state(slot['state'], slot['revision'], slot['owner'], factory, capacity_observation=getattr(self, 'capacity_retry_observations', {}).get(tid))
                        view['unread'] = tid in self.unread_threads if self.native_read_state_available else None
                        view['readReceipt'] = self.read_progress.receipt(tid)
                        view['canLoadHistory'] = bool(self.appserver)
                        view['queuedMessages'] = self.public_queued_followups(tid)
                        break
                if time.monotonic() >= deadline:
                    with self.lock:
                        self.followed.discard(tid)
                    if live:
                        raise TimeoutError('目标 App 尚未返回新的对话快照，请稍后重试；任务未停止')
                    view = self.read_history(tid)
                    break
                time.sleep(0.05)
        else:
            view = self.read_history(tid)
        with self.lock:
            self.dirty.discard(tid)
            self.last_sent = {tid: view, **{key: value for key, value in self.last_sent.items() if key != tid}}
        return {'ok': True, 'refreshed': True, 'source': view['source']}

    def read_history(self, tid, *, native_fallback=True):
        with self.lock:
            source = self.paths.get(tid)
            meta = next((r for r in self.catalog if r['id'] == tid), None)
        if not meta:
            raise RuntimeError('没有找到该会话')
        if not source:
            if not native_fallback:
                raise FileNotFoundError('该会话尚无本地日志路径')
            return self.read_native_history_view(tid, meta)
        path = Path(normalize_local_path(source)).resolve()
        if not any((path.is_relative_to(self.home / name) for name in ('sessions', 'archived_sessions'))):
            raise RuntimeError('会话日志路径不在 Codex 会话目录中')
        try:
            stream = path.open('rb')
        except FileNotFoundError:
            if not native_fallback:
                raise
            return self.read_native_history_view(tid, meta)
        items = []
        seen_items = set()
        asset_factory = lambda source, kind=None, name=None: self.register_asset(tid, source, kind, name)
        with stream:
            size = os.fstat(stream.fileno()).st_size
            if size > 8 * 1024 * 1024:
                stream.seek(-8 * 1024 * 1024, 2)
                stream.readline()
            for line in stream:
                try:
                    row = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                p = row.get('payload', {})
                if row.get('type') != 'event_msg':
                    continue
                if p.get('type') == 'item_completed':
                    item = p.get('item', {})
                    if not isinstance(item, dict):
                        continue
                    key = item.get('id') or 'history-' + str(len(items))
                    if key in seen_items:
                        continue
                    seen_items.add(key)
                    normalized = normalize_item(item, key, asset_factory)
                    set_item_timestamp(normalized, item, timing=p, recorded_at=row.get('timestamp'))
                    normalized['turnId'] = p.get('turn_id')
                    if normalized.get('text') or normalized.get('detail') or normalized.get('attachments') or normalized.get('questions'):
                        items.append(normalized)
                elif p.get('type') in ('user_message', 'agent_message'):
                    kind = 'userMessage' if p['type'] == 'user_message' else 'agentMessage'
                    normalized = normalize_item({'id': 'history-' + str(len(items)), 'type': kind, 'text': p.get('message', ''), 'phase': p.get('phase')}, 'history', asset_factory)
                    set_item_timestamp(normalized, recorded_at=row.get('timestamp'))
                    items.append(normalized)
                elif p.get('type') == 'task_complete' and p.get('error'):
                    items.append(set_item_timestamp(turn_error_notice(p.get('turn_id'), p['error'], len(items)), recorded_at=row.get('timestamp')))
        items = compact_public_items(items)
        recent_items = bounded_recent_items(items[-500:])
        return {**meta, 'source': 'local-history', 'owned': False, 'status': meta.get('status', 'notLoaded'), 'unread': meta.get('unread'), 'items': recent_items, 'totalItems': len(items), 'partialHistory': size > 8 * 1024 * 1024 or len(recent_items) < len(items), 'canLoadHistory': bool(self.appserver), 'updated': time.time(), 'pendingRequests': 0}

    def read_native_history_view(self, tid, meta):
        """Read a known, unloaded chat without requiring its rollout to exist."""
        page = self.read_history_page(tid, {'limit': 5})
        items = page['items']
        recent_items = bounded_recent_items(items)
        return {**meta, 'source': 'codex-app-server', 'owned': False, 'status': meta.get('status', 'notLoaded'), 'unread': meta.get('unread'), 'items': recent_items, 'totalItems': len(items), 'partialHistory': bool(page.get('nextCursor') or len(recent_items) < len(items)), 'canLoadHistory': bool(self.appserver), 'updated': time.time(), 'pendingRequests': 0}

    def rollout_item_timestamps(self, tid, wanted):
        """Enrich existing history reads locally, without another remote request."""
        with self.lock:
            source = self.paths.get(tid)
        if not source or not wanted:
            return {}
        path = Path(normalize_local_path(source)).resolve()
        if not any((path.is_relative_to(self.home / name) for name in ('sessions', 'archived_sessions'))):
            return {}
        found = {}
        try:
            with path.open('rb') as stream:
                if path.stat().st_size > 8 * 1024 * 1024:
                    stream.seek(-8 * 1024 * 1024, 2)
                    stream.readline()
                for line in stream:
                    try:
                        row = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    p = row.get('payload') or {}
                    if not isinstance(p, dict):
                        continue
                    if row.get('type') != 'event_msg' or p.get('type') != 'item_completed':
                        continue
                    raw = p.get('item') or {}
                    if not isinstance(raw, dict):
                        continue
                    key = (str(p.get('turn_id') or ''), str(raw.get('id') or ''))
                    if key in wanted:
                        public = set_item_timestamp({}, raw, timing=p, recorded_at=row.get('timestamp'))
                        if 'timestamp' in public:
                            found[key] = public
        except OSError:
            pass
        return found

    def read_history_page(self, tid, payload):
        """Read an older page through the official app-server history API."""
        if not self.appserver:
            raise RuntimeError('目标机没有可用的 Codex 历史接口')
        cursor = payload.get('cursor')
        if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 8192):
            raise ValueError('历史游标格式错误')
        try:
            limit = max(1, min(int(payload.get('limit', 2)), 5))
        except (TypeError, ValueError) as exc:
            raise ValueError('历史分页大小格式错误') from exc
        if payload.get('mode') == 'turn_details':
            return self.read_history_items(tid, payload)
        summary = payload.get('mode') == 'summary'
        params = {'threadId': tid, 'cursor': cursor, 'limit': limit, 'sortDirection': 'desc', 'itemsView': 'summary' if summary else 'full'}
        source = None
        if hasattr(self, 'paths') and hasattr(self, 'lock'):
            with self.lock:
                source = self.paths.get(tid)
        reader = getattr(self, 'history_reader', None)
        if reader and source and (not summary):
            result = reader.read(self.appserver, tid, params, source, timeout=60)
        else:
            result = self.appserver.request('thread/turns/list', params, timeout=60)
        turns = result.get('data', [])
        if not isinstance(turns, list):
            raise RuntimeError('Codex 历史接口返回格式异常')
        asset_factory = lambda source, kind=None, name=None: self.register_asset(tid, source, kind, name)
        items = []
        timing_lookup = self.rollout_item_timestamps(tid, {(str(turn.get('id') or ''), str(item.get('id') or '')) for turn in turns if isinstance(turn, dict) for item in turn.get('items', []) if isinstance(item, dict)}) if hasattr(self, 'paths') and hasattr(self, 'lock') else {}
        for turn_index, turn in enumerate(reversed(turns)):
            if not isinstance(turn, dict):
                continue
            turn_id = turn.get('id')
            for item_index, item in enumerate(turn.get('items', [])):
                if not isinstance(item, dict):
                    continue
                normalized = normalize_item(item, f'page-{turn_index}-{item_index}', asset_factory, turn_status=turn.get('status'))
                timing = timing_lookup.get((str(turn_id or ''), str(item.get('id') or '')))
                set_item_timestamp(normalized, item, turn, timing=timing)
                if timing and timing.get('timestampSource') and (normalized.get('timestamp') == timing['timestamp']):
                    normalized['timestampSource'] = timing['timestampSource']
                normalized['turnId'] = turn_id
                items.append(normalized)
            if turn.get('status') == 'failed' or turn.get('error'):
                items.append(set_item_timestamp(turn_error_notice(turn_id, turn.get('error'), f'page-{turn_index}'), turn=turn))
            if summary:
                items.append({'id': 'history-process-' + str(turn_id), 'turnId': turn_id, 'type': 'historyProcess', 'historyDeferred': True, 'text': '执行详情按需读取', 'historyPageCursor': result.get('backwardsCursor') or cursor, 'historyPageLimit': limit, 'historyCursor': None})
        items = compact_public_items(items)
        return {'ok': True, 'transport': 'codex-app-server', 'items': items, 'nextCursor': result.get('nextCursor'), 'turnCount': len(turns), 'confirmedAt': time.time()}

    def read_history_items(self, tid, payload):
        """One bounded native item page for an explicitly expanded turn."""
        turn_id = payload.get('turnId')
        if not isinstance(turn_id, str) or not turn_id or len(turn_id) > 200:
            raise ValueError('历史轮次标识无效')
        cursor = payload.get('cursor')
        try:
            result = self.appserver.request('thread/items/list', {'threadId': tid, 'turnId': turn_id, 'cursor': cursor, 'limit': 20}, timeout=30)
            rows = result.get('data', [])
            if not isinstance(rows, list) or any((row.get('turnId') != turn_id for row in rows)):
                raise RuntimeError('原生历史返回了不匹配的轮次')
            raw = [{**row['item'], **({'timestamp': value} if (value := timestamp_ms(row.get('startedAtMs') or row.get('startedAt') or row.get('completedAtMs'))) else {})} for row in rows if isinstance(row.get('item'), dict)]
        except RuntimeError as error:
            if not any((s in str(error).lower() for s in ('method not found', 'unsupported', 'unknown method'))):
                raise
            result = self.appserver.request('thread/turns/list', {'threadId': tid, 'cursor': payload.get('pageCursor'), 'limit': min(max(int(payload.get('pageLimit', 5)), 1), 5), 'sortDirection': 'desc', 'itemsView': 'full'}, timeout=30)
            turn = next((row for row in result.get('data', []) if row.get('id') == turn_id), None)
            if turn is None:
                raise RuntimeError('该历史轮次已变化，请刷新对话后重试')
            raw = turn.get('items', [])
            result = {'nextCursor': None}
        factory = lambda source, kind=None, name=None: self.register_asset(tid, source, kind, name)
        items = []
        for index, item in enumerate(raw):
            public = normalize_item(item, f'history-detail-{index}', factory)
            public['turnId'] = turn_id
            items.append(public)
        return {'ok': True, 'items': compact_public_items(items), 'turnId': turn_id, 'nextCursor': result.get('nextCursor'), 'transport': 'codex-app-server', 'confirmedAt': time.time()}

    def open_desktop_thread(self, tid):
        """Ask the native Codex Desktop app to open one known local thread."""
        uri = 'codex://threads/' + quote(tid, safe='')
        if sys.platform == 'darwin':
            command = ['/usr/bin/open', '-g', uri]
        elif os.name == 'nt':
            try:
                self.app_tools.call('navigate_to_codex_page', {'threadId': tid}, tid, timeout=15)
                return
            except AppToolsUnavailable:
                pass
            except RuntimeError as exc:
                if str(exc) != 'Unsupported dynamic tool: navigate_to_codex_page':
                    raise
            try:
                os.startfile(uri)
            except OSError as exc:
                if getattr(exc, 'winerror', None) == 1312:
                    raise RuntimeError('CRB 当前的 Windows 登录会话无法打开桌面对话。请从 Windows 桌面快捷方式启动 CRB 后重试，无需重启 Codex。') from exc
                raise RuntimeError('无法请求目标 Codex Desktop 打开这条对话') from exc
            return
        else:
            opener = shutil.which('xdg-open')
            if not opener:
                raise RuntimeError('目标机没有可用的 Codex Desktop 深链接处理器')
            command = [opener, uri]
        try:
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                code = process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                return
        except OSError as exc:
            raise RuntimeError('无法请求目标 Codex Desktop 打开这条对话') from exc
        if code != 0:
            raise RuntimeError('目标 Codex Desktop 没有接受对话深链接')

    @staticmethod
    def linux_codex_main_processes():
        """Find only this user's Linux desktop main processes, not CLI jobs."""
        executable = Path('/usr/lib/chatgpt/ChatGPT')
        if not executable.is_file():
            return []
        expected = str(executable.resolve())
        processes = []
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit():
                continue
            try:
                if proc.stat().st_uid != os.getuid():
                    continue
                actual = os.readlink(proc / 'exe').removesuffix(' (deleted)')
                if actual != expected:
                    continue
                argv = [os.fsdecode(value) for value in (proc / 'cmdline').read_bytes().split(b'\x00') if value]
                if len(argv) == 1:
                    argv = shlex.split(argv[0])
                if not argv or any((value.startswith('--type=') for value in argv[1:])):
                    continue
                args = argv[1:]
                if (proc / 'stat').read_text().split()[2] == 'Z':
                    continue
                processes.append((int(proc.name), args))
            except (OSError, IndexError, ValueError):
                continue
        return sorted(processes)

    def restart_linux_codex_after_switch(self, status='账号已切换'):
        """Restart the local GUI in a detached user unit after optional provider changes auth."""
        old = self.linux_codex_main_processes()
        old_pids = {pid for pid, _ in old}
        launch_args = old[0][1] if old else []
        config_path = Path(self.cfg['state_dir']) / 'app-launch.json'
        try:
            app_config = json.loads(config_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            app_config = {}
        configured = str(app_config.get('codex_app_path') or '').strip()
        binary = configured if configured and Path(configured).is_file() and os.access(configured, os.X_OK) else shutil.which('chatgpt')
        if not binary or not shutil.which('systemd-run'):
            return {'clientRestarted': False, 'restartWarning': f'{status}，但未找到 Codex 启动器或 systemd-run'}
        if not os.environ.get('DISPLAY') and (not os.environ.get('WAYLAND_DISPLAY')):
            return {'clientRestarted': False, 'restartWarning': f'{status}，但目标机没有可用的图形桌面会话'}
        for pid in old_pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except OSError:
                return {'clientRestarted': False, 'restartWarning': f'{status}，但无法关闭旧 Codex 客户端'}
        deadline = time.monotonic() + 15
        while old_pids and time.monotonic() < deadline:
            remaining = old_pids & {pid for pid, _ in self.linux_codex_main_processes()}
            if not remaining:
                break
            time.sleep(0.25)
        remaining = old_pids & {pid for pid, _ in self.linux_codex_main_processes()}
        for pid in remaining:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                return {'clientRestarted': False, 'restartWarning': f'{status}，但旧 Codex 未完全退出'}
        deadline = time.monotonic() + 5
        while old_pids & {pid for pid, _ in self.linux_codex_main_processes()}:
            if time.monotonic() >= deadline:
                return {'clientRestarted': False, 'restartWarning': f'{status}，但旧 Codex 未完全退出'}
            time.sleep(0.25)
        if old_pids:
            time.sleep(2)
        if any((pid not in old_pids for pid, _ in self.linux_codex_main_processes())):
            return {'clientRestarted': True}
        unit = f'codex-panel-chatgpt-{uuid.uuid4().hex[:12]}'
        session_env = [f'--setenv={key}={os.environ[key]}' for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'XAUTHORITY', 'DBUS_SESSION_BUS_ADDRESS', 'XDG_CURRENT_DESKTOP', 'XDG_SESSION_TYPE') if os.environ.get(key)]
        try:
            launched = subprocess.run(['systemd-run', '--user', '--collect', '--no-block', f'--unit={unit}', *session_env, binary, *launch_args], stdin=subprocess.DEVNULL, capture_output=True, timeout=15, check=False)
        except (OSError, subprocess.SubprocessError):
            return {'clientRestarted': False, 'restartWarning': f'{status}，旧 Codex 已退出，但新客户端未能启动'}
        if launched.returncode != 0:
            return {'clientRestarted': False, 'restartWarning': f'{status}，旧 Codex 已退出，但新客户端未能启动'}
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            new_pids = {pid for pid, _ in self.linux_codex_main_processes()} - old_pids
            if new_pids:
                time.sleep(3)
                if new_pids & {pid for pid, _ in self.linux_codex_main_processes()}:
                    return {'clientRestarted': True}
            time.sleep(0.5)
        return {'clientRestarted': False, 'restartWarning': f'{status}并请求启动 Codex，但未检测到新客户端进程'}

    def restart_windows_codex_after_switch(self, status='账号已切换', force_launch=False):
        """Complete optional provider's configured switch-and-launch flow on Windows.

        The headless optional provider adapter performs the managed credential switch but
        does not run the GUI command's stop/start stages. Only the Codex Store
        package (or optional provider's configured executable) is targeted here.
        """
        config_path = Path(self.cfg['state_dir']) / 'app-launch.json'
        try:
            app_config = json.loads(config_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            app_config = {}
        launch = force_launch or app_config.get('codex_launch_on_switch') is not False
        app_path = str(app_config.get('codex_app_path') or '').strip()
        escaped_path = app_path.replace("'", "''")
        stop_script = '\n$configured = \'%s\'\nfunction Get-CodexPids {\n  @(Get-CimInstance Win32_Process -Filter "Name = \'ChatGPT.exe\'" |\n    Where-Object { $_.ExecutablePath -and\n      ($_.ExecutablePath -like \'*\\OpenAI.Codex_*\' -or\n       ($configured -and $_.ExecutablePath -eq $configured)) } |\n    Select-Object -ExpandProperty ProcessId)\n}\n$running = @(Get-CodexPids)\nforeach ($procId in $running) { & taskkill.exe /PID $procId /T *> $null }\n$deadline = (Get-Date).AddSeconds(15)\ndo {\n  $remaining = @(Get-CodexPids)\n  if ($remaining.Count -eq 0) { exit 0 }\n  Start-Sleep -Milliseconds 500\n} while ((Get-Date) -lt $deadline)\nforeach ($procId in $remaining) { & taskkill.exe /F /PID $procId /T *> $null }\nStart-Sleep -Seconds 1\nif (@(Get-CodexPids).Count -gt 0) { exit 2 }\nexit 0\n' % escaped_path
        try:
            stopped = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', stop_script], stdin=subprocess.DEVNULL, capture_output=True, timeout=35, check=False)
        except (OSError, subprocess.SubprocessError):
            return {'clientRestarted': False, 'restartWarning': f'{status}，但无法关闭旧 Codex；请在目标电脑手动重启'}
        if stopped.returncode != 0:
            return {'clientRestarted': False, 'restartWarning': f'{status}，但旧 Codex 未完全退出；请在目标电脑手动重启'}
        if not launch:
            return {'clientRestarted': False, 'restartWarning': 'optional provider 已关闭切号后自动启动；旧 Codex 已退出'}
        try:
            os.startfile('codex://')
        except (OSError, AttributeError):
            try:
                if not app_path:
                    raise OSError('optional provider has no configured Codex path')
                os.startfile(app_path)
            except (OSError, AttributeError):
                return {'clientRestarted': False, 'restartWarning': f'{status}，旧 Codex 已退出，但新客户端未能启动'}
        probe_script = '\n$configured = \'%s\'\n$deadline = (Get-Date).AddSeconds(20)\ndo {\n  $running = @(Get-CimInstance Win32_Process -Filter "Name = \'ChatGPT.exe\'" |\n    Where-Object { $_.ExecutablePath -and\n      ($_.ExecutablePath -like \'*\\OpenAI.Codex_*\' -or\n       ($configured -and $_.ExecutablePath -eq $configured)) })\n  if ($running.Count -gt 0) { exit 0 }\n  Start-Sleep -Milliseconds 500\n} while ((Get-Date) -lt $deadline)\nexit 3\n' % escaped_path
        try:
            started = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', probe_script], stdin=subprocess.DEVNULL, capture_output=True, timeout=25, check=False)
            if started.returncode == 0:
                return {'clientRestarted': True}
        except (OSError, subprocess.SubprocessError):
            pass
        return {'clientRestarted': False, 'restartWarning': f'{status}并请求启动 Codex，但未检测到新客户端进程'}

    def macos_codex_bundle(self):
        """Resolve only a local Codex app bundle, never an arbitrary process."""
        config_path = Path(self.cfg['state_dir']) / 'app-launch.json'
        try:
            app_config = json.loads(config_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            app_config = {}
        configured = str(app_config.get('codex_app_path') or '').strip()
        candidates = []
        if configured:
            path = Path(configured).expanduser()
            if path.suffix == '.app':
                candidates.append(path)
            else:
                candidates.extend((parent for parent in path.parents if parent.suffix == '.app'))
        candidates.extend((Path('/Applications/ChatGPT.app'), Path.home() / 'Applications/ChatGPT.app'))
        for bundle in candidates:
            if not bundle.is_dir():
                continue
            info = bundle / 'Contents/Info.plist'
            try:
                with info.open('rb') as stream:
                    metadata = plistlib.load(stream)
                if metadata.get('CFBundleIdentifier') != 'com.openai.codex':
                    continue
                executable = metadata.get('CFBundleExecutable')
                if not isinstance(executable, str) or not executable:
                    continue
                binary = bundle / 'Contents/MacOS' / executable
                if binary.is_file():
                    return (bundle, binary, app_config)
            except (OSError, ValueError, plistlib.InvalidFileException):
                continue
        return (None, None, app_config)

    @staticmethod
    def macos_codex_main_processes(binary):
        """Find only this user's Codex GUI main process by its exact binary."""
        if binary is None:
            return []
        try:
            completed = subprocess.run(['/bin/ps', '-axo', 'pid=,uid=,command='], capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError):
            return []
        if completed.returncode != 0:
            return []
        expected = str(binary)
        found = []
        for line in completed.stdout.splitlines():
            fields = line.strip().split(None, 2)
            if len(fields) != 3:
                continue
            try:
                pid, uid = (int(fields[0]), int(fields[1]))
            except ValueError:
                continue
            command = fields[2]
            if uid == os.getuid() and (command == expected or command.startswith(expected + ' ')) and (not any((arg.startswith('--type=') for arg in command[len(expected):].split()))):
                found.append(pid)
        return found

    def restart_macos_codex_after_switch(self, status='账号已切换', force_launch=False):
        """Restart only Codex's GUI after optional provider changes the local auth state."""
        bundle, binary, app_config = self.macos_codex_bundle()
        if bundle is None:
            return {'clientRestarted': False, 'restartWarning': f'{status}，但未找到目标机的 Codex App'}
        old_pids = set(self.macos_codex_main_processes(binary))
        for pid in old_pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except OSError:
                return {'clientRestarted': False, 'restartWarning': f'{status}，但无法关闭旧 Codex 客户端'}
        deadline = time.monotonic() + 15
        while old_pids & set(self.macos_codex_main_processes(binary)):
            if time.monotonic() >= deadline:
                return {'clientRestarted': False, 'restartWarning': f'{status}，但旧 Codex 未完全退出'}
            time.sleep(0.25)
        launch = force_launch or app_config.get('codex_launch_on_switch') is not False
        if not launch:
            return {'clientRestarted': False, 'restartWarning': 'optional provider 已关闭切号后自动启动；旧 Codex 已退出'}
        time.sleep(2)
        opened = None
        for attempt in range(3):
            try:
                opened = subprocess.run(['/usr/bin/open', '-a', str(bundle)], stdin=subprocess.DEVNULL, capture_output=True, timeout=15, check=False)
            except (OSError, subprocess.SubprocessError):
                opened = None
            if opened is not None and opened.returncode == 0:
                break
            if attempt < 2:
                time.sleep(2)
        if opened is None or opened.returncode != 0:
            return {'clientRestarted': False, 'restartWarning': f'{status}，旧 Codex 已退出，但新客户端未能启动'}
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            new_pids = set(self.macos_codex_main_processes(binary)) - old_pids
            if new_pids:
                time.sleep(2)
                if new_pids & set(self.macos_codex_main_processes(binary)):
                    return {'clientRestarted': True}
            time.sleep(0.5)
        return {'clientRestarted': False, 'restartWarning': f'{status}并请求启动 Codex，但未检测到新客户端进程'}

    def desktop_thread_owner(self, tid):
        """Resolve an explicit action's receiver before any mutation is sent.

        Desktop can unload a conversation while retaining its stream snapshot.
        Only its exact no-client response permits one bounded reattachment. A
        lookup timeout, other rejection, or submission timeout is never replayed.
        """
        try:
            owner = self.ipc.request('thread-owner-discovery', {'conversationId': tid, 'hostId': 'local'})
        except RuntimeError as exc:
            if str(exc) != 'no-client-found':
                raise
            with self.lock:
                slot = self.states.get(tid)
                if slot:
                    slot['owner'] = None
                self.wanted.add(tid)
                self.followed.discard(tid)
                self.dirty.add(tid)
            self.attach_desktop_thread(tid, timeout=15)
            owner = self.ipc.request('thread-owner-discovery', {'conversationId': tid, 'hostId': 'local'})
        target = owner.get('handledByClientId')
        if not isinstance(target, str) or not target:
            raise RuntimeError('原 App 尚未返回有效的接收客户端；消息未提交，请重新接入')
        return target

    def attach_desktop_thread(self, tid, timeout=75):
        """Start Desktop if needed, open a thread, and wait for its native owner."""
        with self.lock:
            meta = next((item for item in self.catalog if item['id'] == tid), None)
            if not meta:
                raise ValueError('没有找到该对话')
            if meta.get('archived'):
                raise RuntimeError('该对话已归档，请先取消归档')
            slot = self.states.get(tid)
            if slot and slot.get('owner') and self.ipc and self.ipc.connected and ((slot.get('state', {}).get('threadRuntimeStatus') or {}).get('type') != 'notLoaded'):
                return {'ok': True, 'transport': 'desktop-ipc', 'alreadyConnected': True, 'confirmedAt': time.time()}
            if slot and slot.get('owner'):
                slot['owner'] = None
                self.dirty.add(tid)
            self.wanted.add(tid)
            self.followed.discard(tid)
        desktop_was_connected = bool(self.ipc and self.ipc.connected)
        announcements = 0
        if desktop_was_connected:
            try:
                announcements += int(self.announce_desktop_following(tid))
            except OSError:
                pass
        self.open_desktop_thread(tid)
        started = time.monotonic()
        deadline = started + timeout
        retry_after = iter((5, 15, 30))
        next_retry = next(retry_after, None)
        connected_once = desktop_was_connected
        while time.monotonic() < deadline:
            ipc = self.ipc
            if not ipc or not ipc.connected:
                time.sleep(0.2)
                continue
            connected_once = True
            with self.lock:
                following = tid in self.followed
            elapsed = time.monotonic() - started
            if not following:
                try:
                    announcements += int(self.announce_desktop_following(tid))
                except OSError:
                    time.sleep(0.2)
                    continue
                while next_retry is not None and next_retry <= elapsed:
                    next_retry = next(retry_after, None)
            with self.lock:
                slot = self.states.get(tid)
                if slot and slot.get('owner'):
                    self.dirty.add(tid)
                    return {'ok': True, 'transport': 'desktop-deep-link', 'alreadyConnected': False, 'launchedDesktop': not desktop_was_connected, 'confirmedAt': time.time()}
            due = next_retry is not None and time.monotonic() - started >= next_retry
            if due:
                try:
                    announcements += int(self.announce_desktop_following(tid))
                except OSError:
                    pass
                if due:
                    next_retry = next(retry_after, None)
            time.sleep(0.2)
        if not connected_once:
            raise RuntimeError(f'已请求启动 Codex Desktop，但 {timeout:g} 秒内未连接')
        if not self.ipc or not self.ipc.connected:
            raise RuntimeError('Desktop 通信在接入过程中断开；未重发消息或重启 App，请重新接入')
        raise RuntimeError(f'已请求 Desktop 打开对话，但 {timeout:g} 秒内未建立实时同步（桌面 IPC 已连接，目标对话未返回原生快照；本次主动发送 {announcements} 次订阅确认，未重发消息或重启 App）')

    def call_macos_app_tool(self, tool, arguments, thread_id, timeout=60):
        """Use the actual App-managed MCP connection, without another writer."""
        return self.app_tools.call(tool, arguments, thread_id, timeout=timeout)

    @contextmanager
    def native_creation_context(self, context_tid):
        """Use App's native create_thread even when its task library is empty.

        The native tool transport requires a readable caller thread. A bare
        thread/start is not durable until it has a history item, so materialize
        a developer-only context, never start a turn, and remove only that
        owned context afterwards. The Mac App-managed bridge reads this context
        through the original App; it never resumes or takes over another writer.
        """
        native_call = self.call_macos_app_tool if sys.platform == 'darwin' else self.app_tools.call
        info = {'cleanupWarning': None}
        if context_tid:
            yield (native_call, context_tid, info)
            return
        runtime = getattr(self, 'mac_native_runtime', None)
        if sys.platform == 'darwin' and runtime:
            with runtime.lock:
                caller = runtime.ensure_context()
            yield (native_call, caller, info)
            return
        server = self.appserver
        if server is None:
            raise RuntimeError('首次新建需要目标机的 Codex app-server')
        context_id = None
        with server.session(timeout=60) as request:
            try:
                started = request('thread/start', {'cwd': str(self.home.parent), 'ephemeral': False, 'serviceName': 'codex-relay-internal-create-context'})
                context_id = str(started.get('thread', {}).get('id') or '')
                if not context_id:
                    raise RuntimeError('Codex 未返回临时创建上下文标识')
                with self.lock:
                    self.creation_context_ids.add(context_id)
                request('thread/inject_items', {'threadId': context_id, 'items': [{'type': 'message', 'role': 'developer', 'content': [{'type': 'input_text', 'text': 'Temporary Codex Relay native creation context. No user task or model inference. Delete after the App tool call.'}]}]})
                yield (native_call, context_id, info)
            finally:
                if context_id:
                    try:
                        current = request('thread/read', {'threadId': context_id, 'includeTurns': True}, timeout_override=10)
                        if current.get('thread', {}).get('turns'):
                            raise RuntimeError('临时上下文已被使用，已保留而不删除')
                        request('thread/archive', {'threadId': context_id}, timeout_override=10)
                        request('thread/delete', {'threadId': context_id}, timeout_override=10)
                    except (OSError, RuntimeError, TimeoutError) as exc:
                        info['cleanupWarning'] = str(exc)
                    finally:
                        with self.lock:
                            self.creation_context_ids.discard(context_id)

    def queue_command(self, command):
        """Slow attachment/read-state work must not occupy the send lane."""
        action = command['action']
        lane = self.commands
        if action == 'watch':
            lane = self.watch_commands
        elif action in ('load_older', 'native_read'):
            lane = self.read_commands
        elif action == 'attach_thread':
            lane = self.attach_commands
        elif action == 'mark_read':
            lane = self.read_state_commands
        lane.put(command)

    def command_loop(self, command_queue=None):
        command_queue = command_queue if command_queue is not None else self.commands
        while True:
            command = command_queue.get()
            if command is None:
                command_queue.task_done()
                return
            cid = command['id']
            try:
                with self.connect_journal() as db:
                    previous = db.execute('SELECT result FROM commands WHERE id=?', (cid,)).fetchone()
                    if previous:
                        result = json.loads(previous[0]) if previous[0] else {'ok': False, 'unknown': True, 'error': '这条指令正在确认中；不会重复发送'}
                    else:
                        db.execute('INSERT INTO commands(id,result) VALUES (?,NULL)', (cid,))
                        db.commit()
                        try:
                            result = self.execute(command)
                        except Exception as exc:
                            result = {'ok': False, 'error': str(exc), 'unknown': isinstance(exc, (TimeoutError, ConnectionError))}
                            if result['unknown'] and command.get('action') == 'queue_send_now':
                                with self.lock:
                                    delivery = self.pending_native_deliveries.get(cid) or {}
                                if delivery.get('clientId'):
                                    result['nativeMessageId'] = delivery['clientId']
                        current = db.execute('SELECT result FROM commands WHERE id=?', (cid,)).fetchone()
                        if current and current[0]:
                            saved = json.loads(current[0])
                            if saved.get('ok') is True and saved.get('confirmedBy') == 'native-stream':
                                result = saved
                        db.execute("UPDATE commands SET result=? WHERE id=? AND (result IS NULL OR COALESCE(json_extract(result,'$.confirmedBy'),'')!='native-stream')", (json.dumps(result), cid))
                        saved = db.execute('SELECT result FROM commands WHERE id=?', (cid,)).fetchone()
                        if saved and saved[0]:
                            result = json.loads(saved[0])
                with self.lock:
                    saved = self.results.get(cid) or {}
                    if not (saved.get('ok') is True and saved.get('confirmedBy') == 'native-stream'):
                        self.results[cid] = result
            except Exception as exc:
                self.last_error = type(exc).__name__
            finally:
                with self.lock:
                    self.inflight.discard(cid)
                command_queue.task_done()

    def execute(self, command):
        tid, action = (command['thread'], command['action'])
        if action == 'watch':
            with self.lock:
                self.wanted.add(tid)
                self.followed.discard(tid)
                loaded = tid in self.states
                if loaded:
                    self.dirty.add(tid)
            if self.ipc and self.ipc.connected:
                self.announce_desktop_following(tid)
            if not loaded:
                view = self.read_history(tid)
                with self.lock:
                    if tid not in self.states:
                        self.last_sent[tid] = view
            return {'ok': True}
        if action == 'mark_read':
            if not tid:
                raise ValueError('没有指定要标记已读的对话')
            self.refresh_native_read_state()
            payload = json.loads(command.get('text') or '{}')
            if not isinstance(payload, dict):
                raise ValueError('已读确认格式错误')
            with self.lock:
                captured = self.read_progress.snapshot(tid)
                captured_identity = self.native_identity_key
                if payload.get('readReceipt') and payload['readReceipt'] != captured['receipt']:
                    return {'ok': True, 'threadId': tid, 'read': False, 'stale': True}
                self.pending_read_receipts[tid] = captured
            try:
                try:
                    native_call = self.call_macos_app_tool if sys.platform == 'darwin' else self.app_tools.call
                    native_call('set_thread_read_state', {'threadId': tid, 'read': True, 'source': 'codex'}, tid)
                except (RuntimeError, ConnectionError, OSError) as exc:
                    unsupported_tool = isinstance(exc, RuntimeError) and any((text in str(exc) for text in ('Unsupported dynamic tool: set_thread_read_state', 'Invalid app tool request')))
                    disconnected_mac_tool = sys.platform == 'darwin' and isinstance(exc, (AppToolsUnavailable, ConnectionError, OSError))
                    if not (unsupported_tool or disconnected_mac_tool):
                        raise
                    path = self.home / '.codex-global-state.json'
                    key = self.refresh_native_identity(force=True)
                    if not key:
                        raise RuntimeError('无法确认目标 Codex 当前登录身份') from exc
                    if key != captured_identity:
                        return {'ok': True, 'threadId': tid, 'read': False, 'stale': True}
                    self.open_desktop_thread(tid)
                    deadline = time.monotonic() + 8
                    while time.monotonic() < deadline:
                        state = json.loads(path.read_text(encoding='utf-8'))
                        unread = native_unread_threads(state, key)
                        if unread is not None and tid not in unread:
                            break
                        time.sleep(0.1)
                    else:
                        raise RuntimeError('目标 Codex 未确认已读状态变更')
                if self.refresh_native_identity() != captured_identity:
                    self.refresh_native_read_state()
                    return {'ok': True, 'threadId': tid, 'read': False, 'stale': True}
                self.apply_native_read_event(tid, False, receipt=captured)
                return {'ok': True, 'threadId': tid, 'read': tid not in self.unread_threads}
            finally:
                with self.lock:
                    self.pending_read_receipts.pop(tid, None)
        payload = {}
        message_attachments = []
        if action in ('send', 'steer'):
            raw_message = command.get('text', '')
            try:
                envelope = json.loads(raw_message)
            except (TypeError, ValueError):
                envelope = None
            if isinstance(envelope, dict) and envelope.get('relayMessageVersion') == 1:
                text = str(envelope.get('text', ''))
                message_attachments = envelope.get('attachments', [])
            else:
                text = str(raw_message)
        else:
            try:
                payload = json.loads(command.get('text') or '{}')
            except (TypeError, ValueError) as exc:
                raise ValueError('指令参数格式错误') from exc
            if not isinstance(payload, dict):
                raise ValueError('指令参数格式错误')
        if action == 'project_rename' and payload.get('mode') == 'roots':
            action = 'project_update_roots'
        if action in ('native_read', 'native_action'):
            return NativeFeatures(self).execute(action, tid, payload)
        if action in ('update_permissions', 'update_settings', 'project_create', 'project_delete', 'project_rename', 'project_update_roots', 'new_thread', 'archive', 'unarchive'):
            NativeFeatures(self).cache().invalidate()
        if action in ('terminal_start', 'terminal_read', 'terminal_write', 'terminal_resize', 'terminal_close'):
            return self.execute_terminal(action, payload)
        if action == 'software_check':
            return software_updates.check(str(payload.get('package', '')))
        if action == 'secure_software_update':
            with self.software_lock:
                package = str(payload.get('package', ''))
                password = str(payload.pop('sudoPassword', ''))
                result = software_updates.install(package, password)
                if package == 'codex' and result.get('changed'):
                    result.update(self.restart_linux_codex_after_switch(status='Codex 已更新'))
                return result
        if action == 'fetch_asset':
            return self.fetch_asset(tid, payload)
        if action == 'upload_asset_chunk':
            return self.upload_asset_chunk(tid, payload)
        if action == 'file_read_text':
            return self.read_text_file(payload)
        if action == 'file_write_text':
            return self.write_text_file(payload)
        if action == 'attach_local_file':
            return self.attach_local_file(tid, payload)
        if action == 'load_older':
            return self.read_history_page(tid, payload)
        if action == 'attach_thread':
            return self.attach_desktop_thread(tid)
        if action in ('archive', 'unarchive', 'project_create', 'project_rename', 'project_delete', 'project_update_roots', 'move_project', 'new_thread', 'rename_thread', 'fork_thread'):
            if not self.appserver and action not in ('archive', 'unarchive', 'new_thread', 'rename_thread'):
                raise RuntimeError('目标机没有可用的 Codex app-server')
            transport = 'codex-app-server'
            if action == 'new_thread':
                prompt = str(payload.get('prompt', '')).strip()
                project_id = str(payload.get('projectId', '')).strip()
                model = str(payload.get('model', '')).strip()
                effort = str(payload.get('effort', '')).strip()
                if not 0 < len(prompt) <= 32000:
                    raise ValueError('首条消息长度需在 1–32000 字符之间')
                with self.lock:
                    projects = {item['id']: item for item in self.projects}
                    models = {item['id']: item for item in self.models}
                    context_ids = {item['id'] for item in self.catalog}
                    context_tid = tid if tid in context_ids else next(iter(context_ids), '')
                if project_id and project_id not in projects:
                    raise ValueError('没有找到目标项目')
                if model:
                    selected_model = models.get(model)
                    if not selected_model:
                        raise ValueError('目标机当前没有这个模型')
                    supported = {item['id'] for item in selected_model.get('efforts', [])}
                    if effort and effort not in supported:
                        raise ValueError('该模型不支持所选推理强度')
                if not self.ipc or not self.ipc.connected:
                    raise RuntimeError('新建对话需要目标 Codex Desktop 在线')
                if not project_id and ('fullAccess' in payload or 'serviceTier' in payload):
                    return create_configured(self, payload, None, models.get(model), command.get('id'))
                with self.native_creation_context(context_tid) as (native_call, caller_tid, creation_info):
                    native_project_id = project_id
                    if project_id:
                        selected_project = projects[project_id]
                        listed = app_tool_json(native_call('list_projects', {}, caller_tid))
                        native_project_id = desktop_project_id(self.home, selected_project, local_projects(listed), projects.values())
                    if 'fullAccess' in payload or 'serviceTier' in payload:
                        return create_configured(self, payload, projects.get(project_id), models.get(model), command.get('id'))
                    target = {'type': 'project', 'projectId': native_project_id, 'environment': {'type': 'local'}} if project_id else {'type': 'projectless'}
                    arguments = {'prompt': prompt, 'target': target}
                    if model:
                        arguments['model'] = model
                    if effort:
                        arguments['thinking'] = effort
                    result = native_call('create_thread', arguments, caller_tid, timeout=60)
                    created = app_tool_json(result)
                thread_id = str(created.get('threadId', '')).strip()
                client_thread_id = str(created.get('clientThreadId', '')).strip()
                if not thread_id and (not client_thread_id):
                    raise RuntimeError('Desktop 已接受创建请求，但没有返回新对话标识')
                self.read_catalog()
                project_warning = None
                if project_id and thread_id:
                    if self.appserver:
                        for delay in (0, 0.3, 0.7):
                            if delay:
                                time.sleep(delay)
                            try:
                                self.appserver.request('thread/metadata/update', {'threadId': thread_id, 'projectId': project_id}, timeout=10)
                                self.read_catalog()
                                project_warning = None
                                break
                            except (OSError, RuntimeError, TimeoutError) as exc:
                                project_warning = f'项目归属尚未写入原生元数据：{exc}'
                        else:
                            project_warning = project_warning or '项目归属尚未写入原生元数据'
                    else:
                        project_warning = '目标机没有可用的 Codex app-server，已按目录尝试归类'
                if thread_id:
                    with self.lock:
                        self.wanted.add(thread_id)
                        self.followed.discard(thread_id)
                return {'ok': True, 'transport': 'desktop-app-tools', 'threadId': thread_id or None, 'clientThreadId': client_thread_id or None, 'hostId': created.get('hostId', 'local'), 'projectAssignmentWarning': project_warning, 'creationContextCleanupWarning': creation_info['cleanupWarning'], 'confirmedAt': time.time()}
            elif action == 'archive':
                with self.lock:
                    state = self.states.get(tid, {}).get('state', {})
                if state.get('threadRuntimeStatus', {}).get('type') == 'active':
                    raise RuntimeError('该对话正在运行，请等本轮结束后再归档')
                try:
                    if not self.ipc or not self.ipc.connected:
                        raise AppToolsUnavailable('桌面 App 未连接')
                    self.app_tools.call('set_thread_archived', {'threadId': tid, 'archived': True}, tid)
                    transport = 'desktop-app-tools'
                except (AppToolsUnavailable, OSError, ConnectionError):
                    if not self.appserver:
                        raise RuntimeError('目标机没有可用的 Codex app-server')
                    self.appserver.request('thread/archive', {'threadId': tid})
                    if self.ipc and self.ipc.connected:
                        self.ipc.broadcast('thread-archived', {'hostId': 'local', 'conversationId': tid}, version=2)
                with self.lock:
                    self.wanted.discard(tid)
                    self.followed.discard(tid)
            elif action == 'unarchive':
                try:
                    if not self.ipc or not self.ipc.connected:
                        raise AppToolsUnavailable('桌面 App 未连接')
                    self.app_tools.call('set_thread_archived', {'threadId': tid, 'archived': False}, tid)
                    transport = 'desktop-app-tools'
                except (AppToolsUnavailable, OSError, ConnectionError):
                    if not self.appserver:
                        raise RuntimeError('目标机没有可用的 Codex app-server')
                    self.appserver.request('thread/unarchive', {'threadId': tid})
                    if self.ipc and self.ipc.connected:
                        self.ipc.broadcast('thread-unarchived', {'hostId': 'local', 'conversationId': tid}, version=1)
            elif action == 'project_create':
                name = str(payload.get('name', '')).strip()
                raw_root = str(payload.get('root', '')).strip()
                if not 0 < len(name) <= 120:
                    raise ValueError('项目名称需为 1–120 个字符')
                root = Path(raw_root).expanduser()
                if not root.is_absolute() or not root.is_dir():
                    raise ValueError('项目目录必须是目标机上已存在的绝对路径')
                if not self.ipc or not self.ipc.connected:
                    raise RuntimeError('创建项目需要目标 Codex Desktop 在线')
                root = str(root.resolve())
                with self.lock:
                    context_tid = next((row['id'] for row in self.catalog if row['id'] == tid), '') or next((row['id'] for row in self.catalog), '')
                with self.native_creation_context(context_tid) as (native_call, caller_tid, creation_info):
                    created = app_tool_json(native_call('create_project', {'name': name, 'sources': [root], 'host': {'type': 'local'}}, caller_tid, timeout=60))
                    desktop_id = created.get('projectId')
                    if not isinstance(desktop_id, str) or not desktop_id:
                        raise TimeoutError('原 App 的项目创建结果尚未确认，请先刷新核对，不要重复创建')
                    confirmed = False
                    warning = None
                    try:
                        listed = local_projects(app_tool_json(native_call('list_projects', {}, caller_tid)))
                        confirmed = any((row['projectId'] == desktop_id and project_paths(row) == {path_key(root)} for row in listed))
                        for delay in (0, 0.1, 0.25, 0.5, 0.9):
                            if delay:
                                time.sleep(delay)
                            if desktop_id in project_id_map(self.home):
                                break
                        self.read_catalog()
                    except (OSError, RuntimeError, TimeoutError) as exc:
                        warning = str(exc)
                    with self.lock:
                        project = registered_backend_project(self.home, desktop_id, [root], self.projects)
                    ready = confirmed and project is not None
                return {'ok': True, 'transport': 'desktop-app-tools', 'project': {'id': project['id'] if ready else None, 'desktopId': desktop_id, 'name': name, 'roots': [root], 'ready': ready}, 'registrationPending': not ready, 'warning': None if ready else '项目已在原 App 登记，可用性尚未确认；请刷新后核对，不要重复创建' + ('：' + warning if warning else ''), 'creationContextCleanupWarning': creation_info['cleanupWarning'], 'confirmedAt': time.time()}
            elif action == 'project_update_roots':
                project_id = str(payload.get('projectId', '')).strip()
                with self.lock:
                    known = any((project['id'] == project_id for project in self.projects))
                if not known:
                    raise ValueError('没有找到该项目')
                roots = payload.get('roots')
                expected = payload.get('expectedRoots')
                if not isinstance(roots, list) or len(roots) > 64 or (not isinstance(expected, list)) or any((not isinstance(path, str) for path in roots + expected)):
                    raise ValueError('项目目录必须是目录列表，且带有原目录版本')

                def root_key(path):
                    return os.path.normcase(os.path.normpath(normalize_local_path(path)))
                clean, seen = ([], set())
                for raw in roots:
                    if not raw.strip() or len(raw) > 4096 or '\x00' in raw:
                        raise ValueError('项目目录不能为空或包含无效字符')
                    path = Path(normalize_local_path(raw.strip())).expanduser()
                    if not path.is_absolute() or not path.is_dir():
                        raise ValueError(f'项目目录必须是目标机上已存在且可访问的绝对路径：{raw}')
                    key = root_key(str(path))
                    if key in seen:
                        raise ValueError('项目目录不能重复')
                    seen.add(key)
                    clean.append(str(path))
                before = self.appserver.request('project/read', {'projectId': project_id})['project']
                native_roots = [item['path'] for item in before.get('roots', [])]
                if [root_key(path) for path in native_roots] != [root_key(path) for path in expected]:
                    self.read_catalog()
                    raise RuntimeError('项目目录已在原 App 或其他面板中改变，请重新打开项目目录后再修改')
                result = self.appserver.request('project/update', {'projectId': project_id, 'roots': [{'path': path} for path in clean]})
                actual = result.get('project', {})
                actual_roots = [item['path'] for item in actual.get('roots', [])]
                if actual.get('id') != project_id or [root_key(path) for path in actual_roots] != [root_key(path) for path in clean]:
                    self.read_catalog()
                    raise RuntimeError('Codex 未返回预期项目目录，请重新查看原生目录后确认，不要重复提交')
                self.read_catalog()
                return {'ok': True, 'transport': 'codex-app-server', 'project': {'id': project_id, 'name': actual.get('name', before.get('name')), 'roots': actual_roots}, 'confirmedAt': time.time()}
            elif action == 'project_delete':
                project_id = str(payload.get('projectId', '')).strip()
                with self.lock:
                    known = any((project['id'] == project_id for project in self.projects))
                if not known:
                    raise ValueError('没有找到该项目')
                self.appserver.request('project/delete', {'projectId': project_id})
            elif action == 'project_rename':
                project_id = str(payload.get('projectId', '')).strip()
                name = str(payload.get('name', '')).strip()
                if not 0 < len(name) <= 120:
                    raise ValueError('项目名称需为 1–120 个字符')
                with self.lock:
                    known = any((project['id'] == project_id for project in self.projects))
                if not known:
                    raise ValueError('没有找到该项目')
                self.appserver.request('project/update', {'projectId': project_id, 'name': name})
            elif action == 'move_project':
                project_id = str(payload.get('projectId', '')).strip()
                with self.lock:
                    known = not project_id or any((project['id'] == project_id for project in self.projects))
                if not known:
                    raise ValueError('没有找到目标项目')
                self.appserver.request('thread/metadata/update', {'threadId': tid, 'projectId': project_id})
            elif action == 'rename_thread':
                name = str(payload.get('name', '')).strip()
                if not 0 < len(name) <= 240:
                    raise ValueError('对话名称需为 1–240 个字符')
                try:
                    if not self.ipc or not self.ipc.connected:
                        raise AppToolsUnavailable('桌面 App 未连接')
                    self.app_tools.call('set_thread_title', {'threadId': tid, 'title': name}, tid)
                    transport = 'desktop-app-tools'
                except (AppToolsUnavailable, OSError, ConnectionError, RuntimeError):
                    if not self.appserver:
                        raise RuntimeError('目标机没有可用的 Codex app-server')
                    self.appserver.request('thread/name/set', {'threadId': tid, 'name': name})
                    transport = 'codex-app-server'
                with self.lock:
                    slot = self.states.get(tid)
                    if slot:
                        slot['state']['title'] = name
                        self.dirty.add(tid)
            elif action == 'fork_thread':
                if 'environment' in payload:
                    environment = payload['environment']
                    if environment not in ('same-directory', 'worktree') or payload.get('lastTurnId'):
                        raise ValueError('分支位置无效')
                    native_call = self.call_macos_app_tool if sys.platform == 'darwin' else self.app_tools.call
                    value = app_tool_json(native_call('fork_thread', {'threadId': tid, 'environment': {'type': environment}}, tid, timeout=60))
                    fork_id = value.get('threadId')
                    client_id = value.get('clientThreadId')
                    if not fork_id and (not client_id):
                        raise TimeoutError('原 App 已接收分支请求，但尚未确认结果；请勿重复创建')
                    self.read_catalog()
                    return {'ok': True, 'transport': 'desktop-app-tools', 'threadId': fork_id, 'clientThreadId': client_id, 'pending': not bool(fork_id), 'sourceThreadId': tid, 'title': '新分支对话'}
                last_turn_id = str(payload.get('lastTurnId', '')).strip()
                params = {'threadId': tid, 'excludeTurns': True}
                if last_turn_id:
                    params['lastTurnId'] = last_turn_id
                result = self.appserver.request('thread/fork', params, timeout=60)
                forked = result.get('thread', {})
                fork_id = str(forked.get('id', '')).strip()
                if not fork_id:
                    raise RuntimeError('Codex 已处理分支请求，但没有返回新对话标识')
                source_title = next((item.get('title') for item in self.catalog if item.get('id') == tid), '新分支')
                branch_name = title_text(source_title) + ' · 分支'
                self.appserver.request('thread/name/set', {'threadId': fork_id, 'name': branch_name})
                self.read_catalog()
                with self.lock:
                    self.wanted.add(fork_id)
                    self.followed.discard(fork_id)
                try:
                    self.open_desktop_thread(fork_id)
                except RuntimeError:
                    pass
                return {'ok': True, 'transport': 'codex-app-server', 'threadId': fork_id, 'title': branch_name, 'confirmedAt': time.time()}
            self.read_catalog()
            return {'ok': True, 'transport': transport, 'confirmedAt': time.time()}
        with self.lock:
            state = self.states.get(tid, {}).get('state', {})
        if action in ('update_settings', 'update_permissions'):
            if action == 'update_permissions':
                profile = payload.get('profileId')
                if profile is not None:
                    profiles = self.appserver.request('permissionProfile/list', {'cwd': state.get('cwd') or None, 'limit': 100})['data']
                    if not any((row.get('id') == profile and row.get('allowed') for row in profiles)):
                        raise ValueError('原 App 不允许选择这个权限配置')
                    policy = payload.get('approvalPolicy', 'on-request')
                    reviewer = payload.get('approvalsReviewer', 'user')
                    if policy not in ('untrusted', 'on-failure', 'on-request', 'never') or reviewer not in ('user', 'auto_review', 'guardian_subagent'):
                        raise ValueError('审批设置无效')
                else:
                    profile = ':danger-full-access' if payload.get('fullAccess') is True else ':workspace'
                    policy = 'never' if profile == ':danger-full-access' else 'on-request'
                    reviewer = 'user'
                full_access = profile == ':danger-full-access'
                settings = {'permissions': profile, 'approvalPolicy': policy, 'approvalsReviewer': reviewer}
            else:
                full_access = None
                model = str(payload.get('model', '')).strip()
                effort = str(payload.get('effort', '')).strip()
                with self.lock:
                    selected_model = next((item for item in self.models if item['id'] == model), None)
                if not selected_model:
                    raise ValueError('目标机当前没有这个模型')
                supported = {item['id'] for item in selected_model.get('efforts', [])}
                if effort not in supported:
                    raise ValueError('该模型不支持所选推理强度')
                settings = {'model': model, 'effort': effort}
                if 'serviceTier' in payload:
                    tier = payload['serviceTier']
                    allowed = {item['id'] for item in selected_model.get('serviceTiers', [])}
                    if not isinstance(tier, str) or tier not in allowed:
                        raise ValueError('该模型不支持所选加速模式')
                    settings['serviceTier'] = tier
            if self.ipc and self.ipc.connected and state:
                target = self.desktop_thread_owner(tid)
                response = self.ipc.request('thread-follower-update-thread-settings', {'conversationId': tid, 'threadSettings': settings, 'activeTurnId': None}, version=2, target=target, timeout=30)
                transport = 'desktop-ipc'
                if not response.get('result', {}).get('applied', False):
                    raise RuntimeError('原桌面会话没有接受设置')
            else:
                if not self.appserver:
                    raise RuntimeError('目标机没有可用的 Codex app-server')
                self.appserver.request('thread/settings/update', {'threadId': tid, **settings})
                transport = 'codex-app-server'
            self.read_catalog()
            result = {'ok': True, 'transport': transport, 'confirmedAt': time.time()}
            if action == 'update_permissions':
                result['fullAccess'] = full_access
            else:
                result.update({'model': model, 'effort': effort})
                if 'serviceTier' in settings:
                    result['serviceTier'] = settings['serviceTier']
            return result
        if not self.ipc or not self.ipc.connected:
            raise RuntimeError('请先在目标机打开 Codex 桌面 App')
        target = self.desktop_thread_owner(tid)
        with self.lock:
            state = self.states.get(tid, {}).get('state', {})
        if not state:
            raise RuntimeError('请先在桌面 App 打开这条对话，等待网页显示“实时同步”')
        question_ack = None
        question_retire = None
        if action == 'question_control':
            turn_id = payload.get('turnId')
            question_ids = payload.get('questionIds')
            operation = payload.get('operation')
            if operation not in ('close', 'skip', 'reopen'):
                raise ValueError('不支持的问题操作')
            question_turn = next((turn for turn in state_turns(state) if turn.get('turnId') == turn_id), None)
            offered_ids = {question['questionItemId'] for item, questions in pending_async_questions(state) if question_turn and any((value.get('id') == item.get('id') for value in question_turn.get('items', []) if isinstance(value, dict))) for question in questions}
            if not isinstance(question_ids, list) or not 1 <= len(question_ids) <= 8 or any((not isinstance(value, str) or value not in offered_ids for value in question_ids)) or (len(set(question_ids)) != len(question_ids)):
                raise RuntimeError('这组选项已过期，请刷新对话')
            current_id = payload.get('questionId')
            if current_id not in question_ids:
                raise ValueError('问题标识无效')
            history = state.get('turnHistory') or {}
            entities = (history.get('history') or {}).get('entitiesByKey', {})
            entity_key = next((key for key, value in entities.items() if isinstance(value, dict) and value.get('turnId') == turn_id), 'turn:' + str(turn_id))
            result = self.question_ui.control(tid, turn_id, entity_key, operation, question_ids, current_id)
            return {'ok': True, 'transport': 'desktop-question-ui', 'questionUi': result, 'confirmedAt': time.time()}
        if action == 'answer_choice':
            item_id = str(payload.get('itemId', ''))
            offered, questions = next(((item, questions) for item, questions in pending_async_questions(state) if str(item.get('id', '')) == item_id), (None, []))
            if not item_id or not offered:
                raise RuntimeError('这组选项已过期，请刷新对话')
            requested_ids = payload.get('questionIds')
            if requested_ids is not None and requested_ids != [question['questionItemId'] for question in questions]:
                raise RuntimeError('部分问题已在原 App 回答，请等待同步后回答剩余问题')
            answers = payload.get('answers')
            skipped = payload.get('skippedQuestionIds', [])
            ids = [question['questionItemId'] for question in questions]
            if not isinstance(skipped, list) or len(skipped) > 8 or any((not isinstance(value, str) or value not in ids for value in skipped)) or (len(set(skipped)) != len(skipped)) or (not isinstance(answers, list)) or (len(answers) != len(questions)) or any((not isinstance(answer, str) or len(answer) > 4000 or (not answer.strip() and question_id not in skipped) or (answer.strip() and question_id in skipped) for question_id, answer in zip(ids, answers))) or (not any((answer.strip() for answer in answers))):
                raise ValueError('请选择或填写每个问题的回答')
            values = [answer.strip() for answer in answers]
            replies = [{'questionItemId': question['questionItemId'], 'question': question['title'], 'answer': value} for question, value in zip(questions, values) if value]
            question_turn = next((turn for turn in reversed(state_turns(state)) if any((isinstance(item, dict) and item.get('id') == item_id for item in turn.get('items', [])))), None)
            if question_turn and question_turn.get('turnId'):
                question_ack = (str(question_turn['turnId']), replies)
            text = '<send_user_message_question_reply>\n' + json.dumps(replies, ensure_ascii=False, separators=(',', ':')) + '\n</send_user_message_question_reply>'
            if len(text) > 32000:
                raise ValueError('选项回答过长')
            action = 'steer' if state.get('threadRuntimeStatus', {}).get('type') == 'active' else 'send'
        if action == 'resolve_request':
            request_id = str(payload.get('requestId', ''))
            if not 0 < len(request_id) <= 200:
                raise ValueError('待确认请求标识无效')
            with self.lock:
                slot = self.states.get(tid) or {}
                if slot.get('owner') != target:
                    raise RuntimeError('原桌面会话已切换，请刷新待确认内容')
                pending = next((copy.deepcopy(request) for request in slot.get('state', {}).get('requests', []) if isinstance(request, dict) and str(request.get('id')) == request_id), None)
            if not pending:
                raise RuntimeError('该请求已处理或失效，请刷新面板')
            method = pending.get('method')
            params = pending.get('params') or {}
            if not isinstance(params, dict) or payload.get('method') != method or str(payload.get('turnId') or '') != str(params.get('turnId') or '') or (params.get('threadId') not in (None, tid)):
                raise RuntimeError('待确认内容已变化，请刷新面板')
            route, field, answer = pending_request_reply(method, params, payload)
            response = self.ipc.request(route, {'conversationId': tid, 'requestId': pending['id'], field: answer}, target=target, timeout=45)
            if response.get('result', {}).get('ok') is not True:
                raise RuntimeError('原桌面 App 未接受此回复')
            return {'ok': True, 'transport': 'desktop-ipc', 'requestId': request_id, 'confirmedAt': time.time()}
        if action == 'stop_turn':
            if state.get('threadRuntimeStatus', {}).get('type') != 'active':
                raise RuntimeError('当前没有正在运行的任务')
            response = self.ipc.request('thread-follower-interrupt-turn', {'conversationId': tid, 'mode': 'user-stop'}, version=3, target=target, timeout=45)
            result = response.get('result') or {}
            if result.get('ok') is not True:
                raise RuntimeError('桌面 App 没有确认停止任务')
            return {'ok': True, 'transport': 'desktop-ipc', 'interruptedTurnId': result.get('interruptedTurnId'), 'warning': '任务已停止，但自动续跑状态未能同步' if result.get('goalPauseError') else None, 'confirmedAt': time.time()}
        if action == 'continue_turn':
            turns = state_turns(state)
            capacity = native_capacity_retry(state, turns, self.capacity_retry_observations.get(tid))
            expected_turn = payload.get('turnId')
            interrupted = state.get('threadRuntimeStatus', {}).get('type') == 'idle' and bool(turns) and (turns[-1].get('status') == 'interrupted')
            if not (interrupted or (capacity and capacity['canContinue'])) or (expected_turn and expected_turn != (turns[-1].get('turnId') or turns[-1].get('id'))):
                raise RuntimeError('当前没有可继续的已停止任务，请刷新面板')
            response = self.ipc.request('thread-follower-start-turn', {'conversationId': tid, 'turnStart': {'request': {'threadId': tid, 'input': [], 'turnTrigger': 'capacity_retry_manual' if capacity else 'resume_interrupted_task'}, 'context': {'inheritThreadSettings': True}}}, version=2, target=target, timeout=45)
            if response.get('result', {}).get('ok') is False:
                raise RuntimeError('桌面 App 没有接受继续任务')
            return {'ok': True, 'transport': 'desktop-ipc', 'method': response.get('method'), 'confirmedAt': time.time()}
        if action == 'edit_user_turn':
            return edit_history(self, tid, payload, target)
        if action == 'edit_last_user_turn':
            raw_message = payload.get('text')
            if not isinstance(raw_message, str):
                raise ValueError('重新发送的消息格式无效')
            message = raw_message.strip()
            turn_id = str(payload.get('turnId', ''))
            item_id = str(payload.get('itemId', ''))
            if not message or len(message) > 32000:
                raise ValueError('重新发送的消息需为 1–32000 字符')
            editable = latest_editable_user_item(state)
            if not editable or editable['turnId'] != turn_id or editable['itemId'] != item_id:
                raise RuntimeError('最后一条消息已变化，请刷新后重试')
            response = self.ipc.request('thread-follower-edit-last-user-turn', {'conversationId': tid, 'turnId': turn_id, 'message': message}, version=2, target=target, timeout=60)
            if response.get('result', {}).get('ok') is not True:
                raise RuntimeError('桌面 App 没有接受编辑重发')
            return {'ok': True, 'transport': 'desktop-ipc', 'method': response.get('method'), 'confirmedAt': time.time()}
        if action == 'queue_resume':
            with self.lock:
                queued = copy.deepcopy(self.queued_followups.get(tid, []))
            expected = payload.get('messageIds')
            if not isinstance(expected, list) or not expected or any((not isinstance(value, str) or not 0 < len(value) <= 200 for value in expected)) or (expected != [message.get('id') for message in queued]):
                raise RuntimeError('队列内容已经变化，请刷新后重试')
            result = self.question_ui.resume_queue(tid, expected)
            return {'ok': True, 'transport': 'desktop-native-queue', 'resumed': result['status'] == 'queue-resumed', 'pending': result['status'] == 'queue-resume-pending', 'confirmedAt': time.time()}
        if action in ('queue_edit', 'queue_delete', 'queue_send_now', 'queue_reorder'):
            message_id = str(payload.get('messageId', '')).strip()
            if action != 'queue_reorder' and (not 0 < len(message_id) <= 200):
                raise ValueError('队列消息标识无效')
            with self.lock:
                queued = copy.deepcopy(self.queued_followups.get(tid, []))
            index = next((i for i, message in enumerate(queued) if str(message.get('id', '')) == message_id), -1)
            if action != 'queue_reorder' and index < 0:
                raise ValueError('这条队列消息已发送或已被删除')
            if action in ('queue_edit', 'queue_send_now') and (native_message_receipt(state, message_id) or native_message_receipt(state, message_id, steering=True)):
                raise ValueError('这条队列消息已发送或已被删除')
            if action == 'queue_edit':
                queued[index] = self.prepare_queued_images(queued[index])
                edited_text = str(payload.get('text', '')).strip()
                if len(edited_text) > 32000:
                    raise ValueError('消息长度不能超过 32000 字符')
                if not edited_text and (not self.queue_message_paths(queued[index])):
                    raise ValueError('请输入消息或保留至少一个附件')
                queued[index]['text'] = edited_text
                context = queued[index].setdefault('context', {})
                if not isinstance(context, dict):
                    context = {}
                    queued[index]['context'] = context
                context['prompt'] = edited_text
                self.update_native_queue(tid, queued, target)
                return {'ok': True, 'transport': 'desktop-native-queue', 'messageId': message_id, 'edited': True, 'confirmedAt': time.time()}
            if action == 'queue_delete':
                queued.pop(index)
                self.update_native_queue(tid, queued, target)
                return {'ok': True, 'transport': 'desktop-native-queue', 'messageId': message_id, 'deleted': True, 'confirmedAt': time.time()}
            if action == 'queue_reorder':
                message_ids = payload.get('messageIds')
                if not isinstance(message_ids, list) or any((not isinstance(value, str) or not value for value in message_ids)):
                    raise ValueError('队列顺序格式无效')
                current_ids = [str(message.get('id', '')) for message in queued]
                if len(message_ids) != len(current_ids) or set(message_ids) != set(current_ids):
                    raise ValueError('队列内容已经变化，请刷新后重试')
                by_id = {str(message.get('id', '')): message for message in queued}
                queued = [by_id[value] for value in message_ids]
                self.update_native_queue(tid, queued, target)
                return {'ok': True, 'transport': 'desktop-native-queue', 'reordered': True, 'confirmedAt': time.time()}
            message = self.prepare_queued_images(queued[index])
            queued_text, inputs, native_attachments = self.queued_message_inputs(message)
            queued.pop(index)
            self.update_native_queue(tid, queued, target)
            try:
                active = state.get('threadRuntimeStatus', {}).get('type') == 'active'
                if active:
                    params = {'conversationId': tid, 'input': inputs, 'clientUserMessageId': message_id, 'attachments': native_attachments, 'restoreMessage': message}
                    response = self.deliver_native_message(tid, command['id'], target, 'thread-follower-steer-turn', params, steering=True, receipt_id=message_id, receipt_context={'queueReceipt': {'messageId': message_id, 'sentNow': True, 'behavior': 'steer'}})
                    behavior = 'steer'
                else:
                    params = {'conversationId': tid, 'turnStart': {'request': {'threadId': tid, 'input': inputs, 'clientUserMessageId': message_id}, 'context': {'inheritThreadSettings': True, 'restoreMessage': message, 'attachments': native_attachments, 'localTurnMetadata': {'fileAttachmentCount': sum((not item.get('isImageAttachment') for item in native_attachments))}}}}
                    response = self.deliver_native_message(tid, command['id'], target, 'thread-follower-start-turn', params, version=2, receipt_id=message_id, receipt_context={'queueReceipt': {'messageId': message_id, 'sentNow': True, 'behavior': 'start'}})
                    behavior = 'start'
            except (TimeoutError, ConnectionError):
                raise
            except Exception:
                restored = queued[:index] + [message] + queued[index:]
                try:
                    self.update_native_queue(tid, restored, target)
                except Exception:
                    pass
                raise
            return {'ok': True, 'transport': 'desktop-native-queue', 'messageId': message_id, 'sentNow': True, 'behavior': behavior, 'method': response.get('method'), 'confirmedAt': time.time()}
        text = text.strip()
        if action in ('send', 'steer') and len(text) > 32000:
            raise ValueError('消息长度不能超过 32000 字符')
        uploaded_inputs, native_attachments = self.resolve_uploaded_inputs(tid, message_attachments)
        if not text and (not uploaded_inputs):
            raise ValueError('请输入消息或添加附件')
        inputs = ([{'type': 'text', 'text': text, 'text_elements': []}] if text else []) + uploaded_inputs
        restore_message = None
        if uploaded_inputs and (not (action == 'send' and state.get('threadRuntimeStatus', {}).get('type') == 'active')):
            restore_message = self.make_queued_message(tid, command['id'], text, state, uploaded_inputs, native_attachments, turn_trigger=None)
        if action == 'send':
            if state.get('threadRuntimeStatus', {}).get('type') == 'active':
                message = self.make_queued_message(tid, command['id'], text, state, uploaded_inputs, native_attachments)
                with self.lock:
                    queued = copy.deepcopy(self.queued_followups.get(tid, []))
                queued.append(message)
                self.update_native_queue(tid, queued, target)
                return {'ok': True, 'transport': 'desktop-native-queue', 'queued': True, 'messageId': command['id'], 'position': len(queued), 'confirmedAt': time.time()}
            if not question_ack and (not async_question_replies(text)):
                turns = state_turns(state)
                question_retire = [{'turnId': turn['turnId'], 'questionIds': [question['questionItemId'] for question in questions]} for item, questions in pending_async_questions(state) for turn in turns if turn.get('turnId') and any((isinstance(value, dict) and value.get('id') == item.get('id') for value in turn.get('items', [])))][-16:]
            params = {'conversationId': tid, 'turnStart': {'request': {'threadId': tid, 'input': inputs, 'clientUserMessageId': command['id']}, 'context': {'inheritThreadSettings': True, **({'restoreMessage': restore_message} if restore_message else {}), 'attachments': native_attachments, 'localTurnMetadata': {'fileAttachmentCount': sum((not item.get('isImageAttachment') for item in native_attachments))}}}}
            response = self.deliver_native_message(tid, command['id'], target, 'thread-follower-start-turn', params, version=2, receipt_context={'questionAck': question_ack} if question_ack else {'questionRetire': question_retire} if question_retire else None)
        elif action == 'steer':
            if state.get('threadRuntimeStatus', {}).get('type') != 'active':
                raise RuntimeError('当前没有正在运行的轮次，请使用“发送”')
            restore_text = text or '附件：' + '、'.join((item.get('name', '附件') for item in message_attachments))
            params = {'conversationId': tid, 'input': inputs, 'clientUserMessageId': command['id'], 'attachments': native_attachments, 'restoreMessage': restore_message or {'text': restore_text, 'cwd': state.get('cwd'), 'context': {'workspaceRoots': [state.get('cwd')] if state.get('cwd') else []}}}
            response = self.deliver_native_message(tid, command['id'], target, 'thread-follower-steer-turn', params, steering=True, receipt_context={'questionAck': question_ack} if question_ack else None)
        else:
            raise ValueError('不支持的操作')
        result = {'ok': True, 'transport': 'desktop-ipc', 'owner': target, 'method': response.get('method'), 'confirmedAt': time.time()}
        if response.get('nativeReceipt'):
            result.update(confirmedBy='native-stream', nativeReceipt=response['nativeReceipt'])
        if question_ack:
            try:
                result['questionUi'] = self.question_ui.defer_ack(tid, *question_ack)
            except Exception:
                result['questionUi'] = {'status': 'unavailable', 'answerDelivered': True}
        elif question_retire:
            try:
                result['questionUi'] = self.question_ui.defer_retire(tid, command['id'], question_retire)
            except Exception:
                result['questionUi'] = {'status': 'unavailable', 'reason': 'new-turn'}
        return result

    def deliver_native_message(self, tid, command_id, target, method, params, version=1, steering=False, receipt_context=None, receipt_id=None):
        native_id = receipt_id or command_id
        try:
            response = self.ipc.request(method, params, version=version, target=target, timeout=45)
            if (response.get('result') or {}).get('ok') is False:
                raise RuntimeError('原桌面 App 明确拒绝了消息，未标记为已送达')
            return response
        except (TimeoutError, ConnectionError):
            with self.lock:
                slot = self.states.get(tid) or {}
                receipt = native_message_receipt(slot.get('state', {}), native_id, steering)
                if slot.get('owner') != target:
                    receipt = None
            if not receipt:
                details = {'threadId': tid, 'clientId': native_id, 'owner': target, 'method': method, 'steering': steering, 'identity': getattr(self, 'native_identity_key', None), 'created': time.time()}
                if receipt_context:
                    details.update(receipt_context)
                with self.connect_journal() as db:
                    db.execute('INSERT OR REPLACE INTO pending_native_deliveries VALUES (?,?,?)', (command_id, json.dumps(details), details['created']))
                with self.lock:
                    self.pending_native_deliveries[command_id] = details
                self.receipt_wakeup.set()
                raise
            return {'method': method, 'nativeReceipt': receipt}

    def reconcile_native_deliveries(self):
        """Durably recover an UNKNOWN submission only from exact native IDs.

        No second send, text matching, model invocation, or read-mark operation.
        Disk work happens outside the IPC/state lock, and confirmations survive
        HTTP races and client restarts.
        """
        with self.lock:
            pending = list(self.pending_native_deliveries.items())
            captured = {p['threadId']: (self.states.get(p['threadId']) or {}).copy() for _, p in pending}
            identity = getattr(self, 'native_identity_key', None)
        for cid, details in pending:
            if details.get('created', 0) < time.time() - 86400:
                with self.connect_journal() as db:
                    db.execute('DELETE FROM pending_native_deliveries WHERE id=? AND created<?', (cid, time.time() - 86400))
                with self.lock:
                    if self.pending_native_deliveries.get(cid) == details:
                        self.pending_native_deliveries.pop(cid, None)
                continue
            slot = captured.get(details['threadId']) or {}
            if slot.get('owner') != details['owner'] or (details.get('identity') is not None and details['identity'] != identity):
                continue
            receipt = native_message_receipt(slot.get('state', {}), details['clientId'], details['steering'])
            if not receipt or not receipt.get('itemId') or (not receipt.get('turnId')):
                continue
            result = {'ok': True, 'transport': 'desktop-ipc', 'confirmedBy': 'native-stream', 'lateReceipt': True, 'nativeReceipt': receipt, 'owner': details['owner'], 'method': details['method'], 'threadId': details['threadId'], 'confirmedAt': time.time()}
            if details.get('queueReceipt'):
                result.update(details['queueReceipt'])
            if details.get('questionAck'):
                result['questionUi'] = self.question_ui.defer_ack(details['threadId'], *details['questionAck'])
            elif details.get('questionRetire'):
                result['questionUi'] = self.question_ui.defer_retire(details['threadId'], details['clientId'], details['questionRetire'])
            with self.connect_journal() as db:
                current = db.execute('SELECT result FROM commands WHERE id=?', (cid,)).fetchone()
                if not current:
                    continue
                old = json.loads(current[0]) if current[0] else None
                if old is not None and (not old.get('unknown')) and (not old.get('ok')):
                    continue
                db.execute('UPDATE commands SET result=? WHERE id=?', (json.dumps(result), cid))
                db.execute('DELETE FROM pending_native_deliveries WHERE id=?', (cid,))
            with self.lock:
                if self.pending_native_deliveries.get(cid) == details:
                    self.pending_native_deliveries.pop(cid, None)
                    self.results[cid] = result

    def reconcile_native_queue_receipts(self):
        """Retire only this Relay's queued IDs proven accepted by their owner.

        Desktop's automatic queue removes a captured message by deep equality.
        A concurrent full-state edit can therefore leave its already accepted ID
        in the queue. Use Desktop's per-ID removal, not another queue snapshot,
        another send, a text match, or a direct edit of its durable state file.
        """
        with self.lock:
            dirty = self.queue_receipt_dirty
            self.queue_receipt_dirty = set()
            snapshots = []
            for tid in dirty:
                slot = self.states.get(tid) or {}
                owner = slot.get('owner')
                if not owner:
                    continue
                mids = [message.get('id') for message in self.queued_followups.get(tid, [])]
                if mids:
                    snapshots.append((tid, owner, slot.get('state') or {}, mids))
        captured = []
        for tid, owner, state, mids in snapshots:
            for mid in mids:
                if not isinstance(mid, str) or not mid:
                    continue
                receipt = native_message_receipt(state, mid) or native_message_receipt(state, mid, steering=True)
                if receipt and receipt.get('itemId') and receipt.get('turnId'):
                    captured.append((tid, mid, owner))
        processed = 0
        for tid, mid, owner in captured:
            key = (tid, mid, owner)
            attempts, previous = self.queue_receipt_attempts.get(key, (0, 0))
            if attempts >= 5:
                continue
            if processed >= 2 or (attempts and time.monotonic() - previous < 5):
                with self.lock:
                    self.queue_receipt_dirty.add(tid)
                continue
            with self.connect_journal() as db:
                row = db.execute('SELECT result FROM commands WHERE id=?', (mid,)).fetchone()
            result = json.loads(row[0]) if row and row[0] else None
            if not isinstance(result, dict) or result.get('ok') is not True or result.get('queued') is not True or (result.get('messageId') != mid):
                continue
            with self.lock:
                current = self.states.get(tid) or {}
                if current.get('owner') != owner or not any((m.get('id') == mid for m in self.queued_followups.get(tid, []))):
                    continue
                if not (native_message_receipt(current.get('state') or {}, mid) or native_message_receipt(current.get('state') or {}, mid, steering=True)):
                    continue
            if not self.ipc or not self.ipc.connected:
                continue
            processed += 1
            self.queue_receipt_attempts[key] = (attempts + 1, time.monotonic())
            try:
                response = self.ipc.request('thread-follower-remove-queued-message', {'conversationId': tid, 'messageId': mid}, target=owner, timeout=5)
                native = response.get('result')
                if not isinstance(native, dict) or 'removed' not in native or native.get('ok') is False:
                    raise ValueError('Native queue removal was not confirmed')
                removed = native['removed']
                if removed is not None and (not isinstance(removed, dict) or not isinstance(removed.get('message'), dict) or removed['message'].get('id') != mid):
                    raise ValueError('Native queue removal identity changed')
            except (TimeoutError, ConnectionError, OSError, RuntimeError, ValueError, TypeError):
                with self.lock:
                    self.queue_receipt_dirty.add(tid)
                continue
            with self.lock:
                if (self.states.get(tid) or {}).get('owner') != owner:
                    continue
                messages = self.queued_followups.get(tid, [])
                remaining = [message for message in messages if message.get('id') != mid]
                if remaining:
                    self.queued_followups[tid] = remaining
                else:
                    self.queued_followups.pop(tid, None)
                self.dirty.add(tid)
                self.queue_receipt_attempts.pop(key, None)

    def native_receipt_loop(self):
        while True:
            self.receipt_wakeup.wait(1)
            self.receipt_wakeup.clear()
            try:
                self.reconcile_native_deliveries()
                self.reconcile_native_queue_receipts()
            except (OSError, sqlite3.Error, ValueError, KeyError):
                pass

    def model_catalog_loop(self):
        """Catalog refresh/network waits never stall heartbeat or receipts."""
        while True:
            try:
                self.maybe_refresh_models()
            except (OSError, RuntimeError, ValueError):
                pass
            time.sleep(2)

    def observe_capacity_retry(self, tid):
        """One bounded local UI observation per failed turn, outside IPC/state locks."""
        with self.lock:
            slot = self.states.get(tid)
            capacity = native_capacity_retry(slot['state']) if slot and slot.get('owner') else None
            if not capacity:
                self.capacity_retry_attempted.pop(tid, None)
                self.capacity_retry_observations.pop(tid, None)
                return
            identity = (capacity['turnId'], slot['owner'])
            if self.capacity_retry_attempted.get(tid) == identity:
                return
            self.capacity_retry_attempted[tid] = identity
            self.capacity_ui_pending[tid] = identity
            if self.capacity_ui_busy:
                return
            self.capacity_ui_busy = True

        def observe():
            while True:
                with self.lock:
                    if not self.capacity_ui_pending:
                        self.capacity_ui_busy = False
                        return
                    target, identity = self.capacity_ui_pending.popitem()
                try:
                    value = self.question_ui.capacity_retry_state(target, identity[0])
                except Exception:
                    value = {'status': 'unavailable'}
                with self.lock:
                    slot = self.states.get(target)
                    current = native_capacity_retry(slot['state']) if slot else None
                    if current and (current['turnId'], slot.get('owner')) == identity and (value.get('status') == 'capacity-observed'):
                        self.capacity_retry_observations[target] = value
                        self.dirty.add(target)
        threading.Thread(target=observe, name='codex-native-capacity-observer', daemon=True).start()

    def probe_native_output_capability(self):
        """One version-reviewed read after IPC connects, including workspace-only use."""
        try:
            capabilities = native_conversation_ui.capabilities(self) if sys.platform.startswith('linux') else {}
            self.native_live_output_available = capabilities.get('liveOutput') is True
        finally:
            self.native_output_probe_busy = False

    def update_native_output_demand(self, rows):
        """Short foreground leases; no native reads or journal records here."""
        now = time.monotonic()
        targets = {}
        if sys.platform.startswith('linux') and self.native_live_output_available and isinstance(rows, list):
            for row in rows[:4]:
                if not isinstance(row, dict):
                    continue
                tid, ttl = (row.get('threadId'), row.get('ttl'))
                if isinstance(tid, str) and tid in self.states and isinstance(ttl, (int, float)) and (not isinstance(ttl, bool)) and math.isfinite(ttl) and (0 < ttl <= 8):
                    targets[tid] = now + ttl
        with self.lock:
            changed = set(targets) != set(self.native_output_demand)
            self.native_output_demand = targets
            if targets and self.native_output_worker is None:
                self.native_output_worker = threading.Thread(target=self.native_output_loop, name='codex-native-output-reader', daemon=True)
                self.native_output_worker.start()
                changed = True
            if changed:
                self.native_output_wakeup.set()

    @staticmethod
    def native_output_base(item):
        detail = item.get('aggregatedOutput') or item.get('aggregated_output') or item.get('formatted_output') or item.get('stdout') or ''
        return hashlib.sha256(str(detail)[:MAX_TOOL_DETAIL].encode()).hexdigest()

    def read_native_outputs_once(self):
        now = time.monotonic()
        with self.lock:
            self.native_output_demand = {tid: expiry for tid, expiry in self.native_output_demand.items() if expiry > now}
            self.native_output_retry = {tid: expiry for tid, expiry in self.native_output_retry.items() if expiry > now and tid in self.native_output_demand}
            if not self.native_output_demand or not self.native_live_output_available:
                return False
            if now < getattr(self, 'native_output_next_read', 0):
                return True
            jobs = {}
            reads = getattr(self, 'native_output_last_read', {})
            for tid in sorted(self.native_output_demand, key=lambda key: reads.get(key, 0)):
                slot = self.states.get(tid) or {}
                state = slot.get('state') or {}
                turns = state_turns(state)
                if not slot.get('owner') or state.get('threadRuntimeStatus', {}).get('type') != 'active' or (not turns) or (not turns[-1].get('turnId')) or (self.native_output_retry.get(tid, 0) > now):
                    continue
                turn = turns[-1]
                if turn.get('status') != 'inProgress':
                    continue
                items = [item for item in turn.get('items', []) if item.get('type') in ('commandExecution', 'CommandExecution') and item.get('status') in ('inProgress', 'in_progress', 'running') and item.get('id')][-4:]
                if items:
                    jobs[tid] = {'turnId': turn['turnId'], 'owner': slot['owner'], 'bases': {item['id']: self.native_output_base(item) for item in items}}
                if len(jobs) == 2:
                    break
            if not jobs:
                return True
            self.native_output_next_read = now + 2
            self.native_output_last_read = {tid: now if tid in jobs else reads.get(tid, 0) for tid in self.native_output_demand}
        result = native_conversation_ui.call({'operation': 'live-output', 'targets': [{'threadId': tid, 'turnId': job['turnId'], 'itemIds': list(job['bases'])} for tid, job in jobs.items()]})
        rows = result.get('targets')
        if not isinstance(rows, list) or len(rows) > 2:
            raise ValueError('Invalid native public output response')
        with self.lock:
            for row in rows:
                tid = row.get('threadId')
                job = jobs.get(tid)
                if not job or row.get('turnId') != job['turnId']:
                    raise ValueError('Native public output scope changed')
                if row.get('available') is not True:
                    self.native_output_retry[tid] = time.monotonic() + 20
                    continue
                slot = self.states.get(tid) or {}
                state = slot.get('state') or {}
                turns = state_turns(state)
                if self.native_output_demand.get(tid, 0) <= time.monotonic() or not turns or slot.get('owner') != job['owner'] or (turns[-1].get('turnId') != job['turnId']) or (turns[-1].get('status') != 'inProgress') or (state.get('threadRuntimeStatus', {}).get('type') != 'active'):
                    continue
                raw = {item.get('id'): item for item in turns[-1].get('items', [])}
                values = {}
                if not isinstance(row.get('items'), list) or len(row['items']) > 4:
                    raise ValueError('Too many native public outputs')
                for item in row['items']:
                    iid, output = (item.get('id'), item.get('output'))
                    if iid not in job['bases'] or not isinstance(output, str) or len(output.encode()) > 65568:
                        raise ValueError('Invalid native public output')
                    current = raw.get(iid) or {}
                    if current.get('status') not in ('inProgress', 'in_progress', 'running') or self.native_output_base(current) != job['bases'][iid]:
                        continue
                    values[iid] = {'detail': output, 'base': job['bases'][iid]}
                overlay = {'turnId': job['turnId'], 'owner': job['owner'], 'items': values}
                if values and self.native_output_overlays.get(tid) != overlay:
                    self.native_output_overlays.pop(tid, None)
                    self.native_output_overlays[tid] = overlay
                    self.dirty.add(tid)
            while len(self.native_output_overlays) > 8:
                self.native_output_overlays.pop(next(iter(self.native_output_overlays)))
        return True

    def native_output_loop(self):
        delay = None
        while True:
            self.native_output_wakeup.wait(delay)
            self.native_output_wakeup.clear()
            try:
                delay = 2 if self.read_native_outputs_once() else None
            except Exception as exc:
                self.native_live_output_available = False
                print('Native public output:', type(exc).__name__, flush=True)
                delay = None

    def apply_native_outputs(self, tid, state, view):
        with self.lock:
            overlay = getattr(self, 'native_output_overlays', {}).get(tid)
            owner = (self.states.get(tid) or {}).get('owner')
        if not overlay or not view.get('owned') or view.get('status') != 'active' or (overlay['owner'] != owner) or (overlay['turnId'] != view.get('lastTurnId')):
            return view
        turns = state_turns(state)
        turn = turns[-1] if turns else {}
        if turn.get('status') != 'inProgress':
            return view
        raw = {item.get('id'): item for item in turn.get('items', [])}
        items = {item.get('id'): item for item in view['items'] if item.get('turnId') == overlay['turnId']}
        changed = False
        for iid, value in overlay['items'].items():
            item = raw.get(iid) or {}
            if item.get('status') not in ('inProgress', 'in_progress', 'running') or self.native_output_base(item) != value['base']:
                continue
            public = items.get(iid)
            if public is None:
                public = normalize_item(item, iid)
                public['turnId'] = overlay['turnId']
                set_item_timestamp(public, item, turn)
            items[iid] = {**public, 'detail': value['detail'], 'liveOutput': True}
            changed = True
        if changed:
            order = {item.get('id'): index for index, item in enumerate(turn.get('items', []))}
            values = [item for item in view['items'] if item.get('turnId') != overlay['turnId']]
            values += sorted(items.values(), key=lambda item: order.get(item.get('id'), float('inf')))
            latest_user = next((item for item in reversed(values) if item.get('type') in ('userMessage', 'steeringUserMessage')), None)
            optional = [item for item in values if not item.get('liveOutput') and item is not latest_user]
            while optional and len(json.dumps(values, ensure_ascii=False, separators=(',', ':')).encode()) > MAX_VIEW_ITEMS_BYTES:
                dropped = optional.pop(0)
                values = [item for item in values if item is not dropped]
            view['items'] = bounded_recent_items(values)
            view['partialHistory'] |= len(view['items']) < view['totalItems']
        return view

    def build_dirty_views(self):
        """Normalize immutable snapshots outside the foreground state lock."""
        with self.lock:
            jobs = [(tid, slot['state'], slot['revision'], slot['owner'], tid in self.unread_threads if self.native_read_state_available else None, self.read_progress.receipt(tid), getattr(self, 'capacity_retry_observations', {}).get(tid)) for tid in self.dirty if (slot := self.states.get(tid))]
            self.dirty.difference_update((tid for tid, *_ in jobs))
        for tid, state, revision, owner, unread, read_receipt, capacity_observation in jobs:
            cache = self.live_view_caches.setdefault(tid, LiveTurnCache())
            factory = lambda source, kind=None, name=None, tid=tid: self.register_asset(tid, source, kind, name)
            try:
                view = view_state(state, revision, owner, factory, turn_cache=cache, capacity_observation=capacity_observation)
                self.apply_native_outputs(tid, state, view)
                view.update(unread=unread, readReceipt=read_receipt, canLoadHistory=bool(self.appserver), queuedMessages=self.public_queued_followups(tid))
            except Exception:
                with self.lock:
                    self.dirty.add(tid)
                raise
            with self.lock:
                slot = self.states.get(tid)
                previous = self.last_sent.get(tid) or {}
                if slot and slot['owner'] == owner and (previous.get('revision', -1) <= revision):
                    self.last_sent[tid] = view
                if slot and (slot['revision'] != revision or slot['state'] is not state):
                    self.dirty.add(tid)
        while len(self.live_view_caches) > 40:
            self.live_view_caches.pop(next(iter(self.live_view_caches)))

    def run(self):
        lanes = (self.commands, self.watch_commands, self.read_commands, self.attach_commands, self.read_state_commands)
        for lane in lanes:
            threading.Thread(target=self.command_loop, args=(lane,), daemon=True).start()
        threading.Thread(target=self.native_receipt_loop, daemon=True).start()
        threading.Thread(target=self.model_catalog_loop, daemon=True).start()
        while True:
            try:
                self.reap_terminals()
                if not self.ipc or not self.ipc.connected:
                    retry = getattr(self, 'connection_retry', None)
                    if retry is None or retry.begin('desktop'):
                        try:
                            self.ipc = DesktopIPC(self.desktop_ipc, self.on_event)
                            self.ipc.connect()
                            if sys.platform.startswith('linux') and (not getattr(self, 'native_output_probe_busy', False)):
                                self.native_output_probe_busy = True
                                threading.Thread(target=self.probe_native_output_capability, name='codex-native-output-capability', daemon=True).start()
                            if retry is not None:
                                retry.success('desktop')
                        except OSError:
                            if retry is not None:
                                retry.failure('desktop')
                            self.last_error = 'Codex 桌面 App 尚未连接'
                if time.monotonic() - self.last_native_read_poll > 1:
                    self.refresh_native_read_state()
                    self.last_native_read_poll = time.monotonic()
                automation_signature = None
                if time.monotonic() - self.last_automation_poll > 1:
                    self.last_automation_poll = time.monotonic()
                    automation_signature = scheduled_automation_signature(self.home)
                if time.monotonic() - self.last_catalog > 5 or (automation_signature is not None and automation_signature != self.automation_signature):
                    self.read_catalog()
                    if automation_signature is not None:
                        self.automation_signature = automation_signature
                with self.lock:
                    desired = list(self.wanted)[-40:]
                if self.ipc and self.ipc.connected:
                    for tid in desired:
                        with self.lock:
                            following = tid in self.followed
                        if not following:
                            self.announce_desktop_following(tid)
                self.build_dirty_views()
                with self.lock:
                    views = dict(list(self.last_sent.items())[:1])
                    pending_results = list(self.results.items())
                    payload = {'host': self.cfg['host_id'], 'label': self.cfg['label'], 'desktopConnected': bool(self.ipc and self.ipc.connected), 'terminal': self.terminal_info, 'capabilities': {'queueResume': True, 'nativeSidebar': True, 'nativeReadCache': True, 'nativeReadCacheScope': NativeFeatures(self).cache_scope(), 'nativeLiveOutput': bool(getattr(self, 'native_live_output_available', False)), 'editHistory': True, 'newThreadSettings': True, 'softwarePackages': ['codex']}, 'catalog': [row.copy() for row in self.catalog], 'projects': self.projects, 'models': self.models, 'views': views}
                results = {}
                result_bytes = 0
                for cid, value in pending_results:
                    encoded_size = len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode())
                    if results and result_bytes + encoded_size > 3 * 1024 * 1024:
                        break
                    results[cid] = value
                    result_bytes += encoded_size
                    if len(results) >= 12:
                        break
                payload['results'] = results
                body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
                headers = {'Authorization': 'Bearer ' + self.cfg['agent_token'], 'Content-Type': 'application/json'}
                if len(body) >= 64 * 1024:
                    body = gzip.compress(body, compresslevel=5)
                    headers['Content-Encoding'] = 'gzip'
                req = urllib.request.Request(self.cfg['relay_url'] + '/agent/sync', data=body, headers=headers, method='POST')
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(req, timeout=60) as resp:
                    reply = json.load(resp)
                self.update_native_output_demand(reply.get('liveOutputDemand', []))
                refills = []
                with self.lock:
                    for tid, value in views.items():
                        if self.last_sent.get(tid) is value:
                            self.last_sent.pop(tid, None)
                    for cid, value in results.items():
                        if self.results.get(cid) is value:
                            self.results.pop(cid, None)
                    if reply.get('generation') != self.relay_generation:
                        self.relay_generation = reply.get('generation')
                        self.dirty.update(self.states)
                        refills = [tid for tid in self.wanted - self.states.keys() if tid in self.paths]
                    for command in reply.get('commands', []):
                        if command['id'] not in self.inflight:
                            self.inflight.add(command['id'])
                            self.queue_command(command)
                for tid in refills:
                    try:
                        fallback = self.read_history(tid, native_fallback=False)
                    except FileNotFoundError:
                        continue
                    with self.lock:
                        if tid not in self.states:
                            self.last_sent[tid] = fallback
                self.last_error = ''
            except Exception as exc:
                reason = type(exc).__name__
                if reason != self.last_error:
                    print('Agent sync:', reason, flush=True)
                self.last_error = reason
            time.sleep(0.6)
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    os.umask(63)
    HostAgent(json.loads(Path(args.config).read_text())).run()
