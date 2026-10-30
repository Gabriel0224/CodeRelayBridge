"""File/terminal bridge for newly enrolled PCs, without an SSH/Python prerequisite."""
import ast
import gzip
import json
import mimetypes
import sys
from pathlib import Path
import threading
import time
import types
import urllib.request

class LocalAccess:

    def __init__(self, agent, host_id, core, preview_config=None):
        self.agent, self.host_id = (agent, host_id)
        self.terminal_agent = object.__new__(core.HostAgent)
        self.terminal_agent.terminals = {}
        self.terminal_agent.terminal_lock = threading.RLock()
        self.created, self.sequence = ({}, 0)
        self.lock = threading.RLock()
        from crb.files.file_access import POSIX_HELPER
        tree = ast.parse(POSIX_HELPER)
        tree.body = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef))]
        namespace = {}
        exec(compile(tree, '<bundled-file-helper>', 'exec'), namespace)
        self.file_run = namespace['run']
        self.preview = None
        if preview_config is not None:
            from desktop_client.local_preview import LocalPreviewClient
            self.preview = LocalPreviewClient(preview_config)
        original = agent.execute
        access = self

        def execute(instance, command):
            if command['action'] == 'client_web_preview':
                if access.preview is None:
                    raise ValueError('本地网页预览尚未配置')
                return access.preview.connect(json.loads(command['text']))
            if command['action'] == 'client_files':
                return access.files(json.loads(command['text']))
            if command['action'] == 'client_terminal':
                return access.terminal(json.loads(command['text']))
            if command['action'] in ('file_read_text', 'file_write_text'):
                function = access.terminal_agent.read_text_file if command['action'] == 'file_read_text' else access.terminal_agent.write_text_file
                return function(json.loads(command['text']))
            return original(command)
        agent.execute = types.MethodType(execute, agent)

    def files(self, request):
        if not isinstance(request, dict) or request.get('action') not in ('roots', 'list', 'folders', 'read', 'create', 'copy', 'delete', 'document_probe', 'document_begin', 'document_chunk', 'document_commit', 'document_status', 'document_abort', 'document_forget'):
            raise ValueError('不支持的客户端文件操作')
        action = request['action']
        payload = request.get('payload') or {}
        if not isinstance(payload, dict):
            raise ValueError('文件参数格式错误')
        for name in ('path', 'destination'):
            value = payload.get(name, '')
            if not isinstance(value, str) or len(value) > 4096 or '\x00' in value:
                raise ValueError('文件路径无效')
        offset, limit = (int(payload.get('offset', 0)), int(payload.get('limit', 2 * 1024 * 1024 if action == 'read' else 250)))
        if not 0 <= offset <= (256 * 1024 * 1024 if action == 'read' else 32 * 1024 * 1024 if action.startswith('document_') else 1000000) or not 1 <= limit <= (2 * 1024 * 1024 if action == 'read' else 300):
            raise ValueError('文件读取范围无效')
        args = dict(payload, action=action, offset=offset, limit=limit)
        if action.startswith('document_') and len(json.dumps(args)) > 256 * 1024:
            raise ValueError('文档分块请求过长')
        args.setdefault('path', '')
        if action == 'copy' and (not args.get('destination')):
            raise ValueError('未指定粘贴目录')
        result = self.file_run(args)
        if action == 'read':
            result['mime'] = mimetypes.guess_type(result.get('name', ''))[0] or 'application/octet-stream'
        return result

    def sessions(self):
        self.terminal_agent.reap_terminals()
        with self.lock, self.terminal_agent.terminal_lock:
            return [{'sessionId': s.id, 'host': self.host_id, 'shell': s.shell, 'cwd': s.cwd, 'ordinal': self.created[s.id][1], 'createdAt': self.created[s.id][0], 'exited': s.process.poll() is not None, 'cols': s.cols, 'rows': s.rows} for s in self.terminal_agent.terminals.values()]

    def terminal(self, request):
        if not isinstance(request, dict) or request.get('action') not in ('start', 'read', 'write', 'resize', 'close'):
            raise ValueError('不支持的客户端终端操作')
        payload = request.get('payload') or {}
        if not isinstance(payload, dict):
            raise ValueError('终端参数格式错误')
        with self.lock:
            result = self.terminal_agent.execute_terminal('terminal_' + request['action'], payload)
            if request['action'] == 'start':
                self.sequence += 1
                self.created[result['sessionId']] = (time.time(), self.sequence)
            if request['action'] == 'close':
                result['closed'] = True
            result['transport'] = 'client'
            return result

    def decorate(self, request):
        raw = request.data or b''
        compressed = request.get_header('Content-encoding') == 'gzip'
        data = json.loads(gzip.decompress(raw) if compressed else raw)
        self.decorate_payload(data)
        encoded = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()
        request.data = gzip.compress(encoded, compresslevel=5, mtime=0) if compressed else encoded
        return request

    def decorate_payload(self, data):
        data.setdefault('capabilities', {})['documentEditing'] = True
        if self.preview is not None:
            data['capabilities']['localWebPreview'] = True
        data['terminal'] = {'supported': True, 'transport': 'client', 'shell': 'PowerShell' if sys.platform == 'win32' else 'Shell', 'kind': 'windows' if sys.platform == 'win32' else 'posix', 'sessions': self.sessions()}
        actions = data.get('capabilities', {}).get('actions')
        if isinstance(actions, list):
            data['capabilities']['actions'] = sorted(set(actions) | {'client_files', 'client_terminal', 'file_read_text', 'file_write_text'} | ({'client_web_preview'} if self.preview is not None else set()))
        return data

    def close(self):
        if self.preview:
            self.preview.close()
        self.terminal_agent.close_all_terminals()
