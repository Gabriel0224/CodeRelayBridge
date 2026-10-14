"""Remote file browsing and explicit file operations over the relay's SSH identity."""
import base64
import json
import mimetypes
import secrets
from pathlib import Path
import shutil
import subprocess
import threading
import zlib
from crb.platform.process_launch import hidden_launch_options
POSIX_HELPER = '\nimport base64, json, os, pathlib, shutil, sys, zlib\n\ndef existing(raw):\n    path = pathlib.Path(os.path.abspath(os.path.expanduser(str(raw or \'~\'))))\n    path.lstat()\n    return path\n\ndef directory(raw):\n    path = existing(raw)\n    if not path.is_dir():\n        raise ValueError(\'不是文件夹\')\n    return path\n\ndef child_name(value):\n    name = str(value or \'\').strip()\n    if (not name or name in (\'.\', \'..\') or len(name) > 255 or\n            any(c in name for c in (\'/\', \'\\\\\', \'\\x00\', \'\\r\', \'\\n\')) or\n            (os.name == \'nt\' and (name.rstrip(\' .\') != name or\n             any(c in name for c in (\':\', \'*\', \'?\', \'"\', \'<\', \'>\', \'|\')) or\n             name.split(\'.\')[0].upper() in (\'CON\', \'PRN\', \'AUX\', \'NUL\',\n                 *(f\'COM{i}\' for i in range(1, 10)),\n                 *(f\'LPT{i}\' for i in range(1, 10)))))):\n        raise ValueError(\'文件名无效\')\n    return name\n\ndef is_junction(path):\n    return bool(getattr(os.path, \'isjunction\', lambda _: False)(path))\n\ndef destination_name(source, folder):\n    original = folder / source.name\n    if not os.path.lexists(original):\n        return original\n    stem = source.stem if source.is_file() else source.name\n    suffix = source.suffix if source.is_file() else \'\'\n    for number in range(1, 1001):\n        label = \' - 副本\' if number == 1 else f\' - 副本 ({number})\'\n        candidate = folder / f\'{stem}{label}{suffix}\'\n        if not os.path.lexists(candidate):\n            return candidate\n    raise ValueError(\'同名副本过多\')\n\ndef has_subdirectories(path):\n    try:\n        with os.scandir(path) as entries:\n            return any(entry.is_dir(follow_symlinks=True) for entry in entries)\n    except (OSError, ValueError):\n        return None\n\ndef run(req):\n    action = req[\'action\']\n    if action == \'create\':\n        folder = directory(req[\'path\'])\n        name = child_name(req.get(\'name\'))\n        target = folder / name\n        if req.get(\'kind\') == \'directory\':\n            target.mkdir()\n            is_directory = True\n        elif req.get(\'kind\') == \'file\':\n            with target.open(\'xb\'):\n                pass\n            is_directory = False\n        else:\n            raise ValueError(\'新建类型无效\')\n        return {\'ok\': True, \'path\': str(target), \'name\': name,\n                \'isDirectory\': is_directory}\n    if action == \'delete\':\n        target = existing(req[\'path\'])\n        if (target.parent == target or os.path.ismount(target) or\n                (not target.is_symlink() and target.resolve() == pathlib.Path.home().resolve())):\n            raise ValueError(\'不能删除磁盘根目录或主目录\')\n        if req.get(\'confirmName\') != target.name:\n            raise ValueError(\'删除确认与目标不一致\')\n        if is_junction(target):\n            raise ValueError(\'为安全起见，不删除目录联接点\')\n        if target.is_symlink() or not target.is_dir():\n            target.unlink()\n        else:\n            if req.get(\'recursive\') is not True:\n                raise ValueError(\'删除文件夹需要确认包含的全部内容\')\n            shutil.rmtree(target)\n        return {\'ok\': True, \'path\': str(target)}\n    if action == \'copy\':\n        source = existing(req[\'path\'])\n        folder = directory(req.get(\'destination\'))\n        if source.is_symlink() or is_junction(source):\n            raise ValueError(\'暂不支持复制符号链接或目录联接点\')\n        if source.is_dir() and (folder.resolve() == source.resolve() or\n                source.resolve() in folder.resolve().parents):\n            raise ValueError(\'不能把文件夹复制到自身内部\')\n        target = destination_name(source, folder)\n        if source.is_dir():\n            shutil.copytree(source, target, symlinks=True)\n        else:\n            with source.open(\'rb\') as reader, target.open(\'xb\') as writer:\n                shutil.copyfileobj(reader, writer, 1024 * 1024)\n            shutil.copystat(source, target)\n        return {\'ok\': True, \'path\': str(target), \'name\': target.name,\n                \'isDirectory\': source.is_dir()}\n    if action == \'roots\':\n        home = pathlib.Path.home().resolve()\n        roots = [{\'name\': \'主目录\', \'path\': str(home), \'kind\': \'home\'}]\n        if os.name == \'nt\':\n            for drive in os.listdrives():\n                if pathlib.Path(drive).is_dir():\n                    roots.append({\'name\': drive, \'path\': drive, \'kind\': \'volume\'})\n        else:\n            roots.append({\'name\': \'文件系统 /\', \'path\': \'/\', \'kind\': \'volume\'})\n            if sys.platform == \'darwin\' and pathlib.Path(\'/Volumes\').is_dir():\n                roots.append({\'name\': \'已挂载磁盘\', \'path\': \'/Volumes\', \'kind\': \'volumes\'})\n        for root in roots:\n            root[\'hasChildren\'] = has_subdirectories(root[\'path\'])\n        return {\'ok\': True, \'roots\': roots}\n    raw = str(req.get(\'path\') or \'~\')\n    path = pathlib.Path(raw).expanduser().resolve(strict=True)\n    if action in (\'list\', \'folders\'):\n        if not path.is_dir():\n            raise ValueError(\'不是文件夹\')\n        rows = []\n        with os.scandir(path) as scanner:\n            for child in scanner:\n                try:\n                    child_directory = child.is_dir(follow_symlinks=True)\n                    if action == \'folders\' and not child_directory:\n                        continue\n                    stat = child.stat(follow_symlinks=True)\n                    rows.append({\'name\': child.name, \'path\': child.path,\n                                 \'isDirectory\': child_directory,\n                                 \'size\': 0 if child_directory else stat.st_size,\n                                 \'modified\': stat.st_mtime})\n                except (OSError, ValueError):\n                    continue\n        rows.sort(key=lambda row: (not row[\'isDirectory\'], row[\'name\'].casefold()))\n        offset, limit = req[\'offset\'], req[\'limit\']\n        page = rows[offset:offset + limit]\n        if action == \'folders\':\n            for row in page:\n                row[\'hasChildren\'] = has_subdirectories(row[\'path\'])\n        return {\'ok\': True, \'path\': str(path), \'parent\': str(path.parent),\n                \'entries\': page, \'total\': len(rows),\n                \'nextOffset\': min(offset + limit, len(rows))}\n    if not path.is_file():\n        raise ValueError(\'不是普通文件\')\n    size = path.stat().st_size\n    offset, limit = req[\'offset\'], req[\'limit\']\n    with path.open(\'rb\') as file:\n        file.seek(offset)\n        chunk = file.read(limit)\n    return {\'ok\': True, \'path\': str(path), \'name\': path.name, \'size\': size,\n            \'data\': base64.b64encode(chunk).decode(\'ascii\'),\n            \'offset\': offset, \'nextOffset\': offset + len(chunk),\n            \'eof\': offset + len(chunk) >= size}\n\nrequest = {}\ntry:\n    request = json.loads(zlib.decompress(base64.b64decode(sys.argv[1])))\n    result = run(request)\nexcept Exception as error:\n    result = {\'ok\': False, \'error\': str(error)}\nnonce = request.get(\'responseNonce\', \'\')\nprefix = (\'CODEX_FILE_RESPONSE:\' + nonce + \':\') if nonce else \'\'\nprint(prefix + json.dumps(result, ensure_ascii=True, separators=(\',\', \':\')))\n'

class SshFileManager:

    def __init__(self, config):
        self.hosts = config.get('ssh_hosts', {})
        self.ssh_binary = config.get('ssh_binary') or shutil.which('ssh') or 'ssh'
        self.slots = threading.BoundedSemaphore(8)

    def _command(self, config, request):
        hostname = str(config.get('hostname') or '').strip()
        user = str(config.get('user') or '').strip()
        if not hostname or not user:
            raise ValueError('SSH 目标缺少 hostname 或 user')
        port = min(max(int(config.get('port', 22)), 1), 65535)
        import os
        command = [str(self.ssh_binary), '-F', os.devnull, '-T', '-p', str(port), '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'ConnectTimeout=10', '-o', 'LogLevel=ERROR']
        identity = str(config.get('identity_file') or '').strip()
        if identity:
            identity_path = Path(identity).expanduser().resolve()
            if not identity_path.is_file():
                raise RuntimeError('中继机上没有找到 SSH 私钥')
            command.extend(['-i', str(identity_path)])
        known_hosts = str(config.get('known_hosts_file') or '').strip()
        if known_hosts:
            known_hosts_path = Path(known_hosts).expanduser().resolve()
            if not known_hosts_path.is_file():
                raise RuntimeError('中继机上没有找到 SSH 主机指纹文件')
            command.extend(['-o', f'UserKnownHostsFile={known_hosts_path}', '-o', 'StrictHostKeyChecking=yes'])
        command.append(f'{user}@{hostname}')
        encoded_request = base64.b64encode(zlib.compress(json.dumps(request, ensure_ascii=False, separators=(',', ':')).encode(), 9)).decode()
        windows = str(config.get('kind') or 'posix').lower() == 'windows'
        encoded_script = base64.b64encode(zlib.compress(POSIX_HELPER.encode(), 9)).decode()
        python_binary = str(config.get('python_binary') or ('C:\\Python314\\python.exe' if windows else 'python3'))
        if any((char in python_binary for char in ('"', "'", '\n', '\r'))):
            raise ValueError('目标 Python 路径无效')
        command.append(('& ' if windows else '') + '"' + python_binary + '" -c "import base64,zlib;exec(zlib.decompress(base64.b64decode(\'' + encoded_script + '\')))" ' + encoded_request)
        return command

    def execute(self, data):
        action = str(data.get('action') or '')
        host = str(data.get('host') or '')
        payload = data.get('payload', {})
        if action not in ('list', 'folders', 'roots', 'read', 'create', 'copy', 'delete'):
            raise ValueError('不支持的文件操作')
        if not isinstance(payload, dict):
            raise ValueError('文件参数格式错误')
        config = self.hosts.get(host)
        if not config:
            raise ValueError('该电脑尚未配置 SSH 文件访问')
        for field in ('path', 'destination'):
            path = str(payload.get(field) or '')
            if len(path) > 4096 or '\x00' in path:
                raise ValueError('文件路径无效或过长')
        if action == 'copy' and (not payload.get('destination')):
            raise ValueError('没有指定粘贴文件夹')
        if action == 'create':
            if payload.get('kind') not in ('file', 'directory'):
                raise ValueError('新建类型无效')
            name = payload.get('name')
            if not isinstance(name, str) or not name.strip() or len(name) > 255:
                raise ValueError('文件名无效')
        if action == 'delete' and (not isinstance(payload.get('confirmName'), str)):
            raise ValueError('删除操作需要确认名称')
        offset = int(payload.get('offset', 0))
        limit = int(payload.get('limit', 250 if action != 'read' else 2 * 1024 * 1024))
        if offset < 0 or offset > (256 * 1024 * 1024 if action == 'read' else 1000000):
            raise ValueError('文件偏移量无效')
        if limit < 1 or limit > (2 * 1024 * 1024 if action == 'read' else 300):
            raise ValueError('文件读取范围无效')
        request = {'action': action, 'path': str(payload.get('path') or ''), 'offset': offset, 'limit': limit, 'responseNonce': secrets.token_hex(16)}
        if action == 'create':
            request.update(name=payload['name'], kind=payload['kind'])
        elif action == 'copy':
            request['destination'] = str(payload['destination'])
        elif action == 'delete':
            request.update(confirmName=payload['confirmName'], recursive=payload.get('recursive') is True)
        if not self.slots.acquire(timeout=10):
            raise RuntimeError('文件请求过多，请稍后重试')
        try:
            completed = subprocess.run(self._command(config, request), capture_output=True, timeout=300 if action == 'copy' else 120 if action == 'delete' else 45 if action == 'read' else 25, check=False, **hidden_launch_options())
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('目标机文件访问超时') from exc
        finally:
            self.slots.release()
        if completed.returncode != 0:
            message = completed.stderr.decode('utf-8', errors='replace').strip()
            raise RuntimeError('目标机 SSH 文件访问失败' + (': ' + message[-300:] if message else ''))
        try:
            prefix = ('CODEX_FILE_RESPONSE:' + request['responseNonce'] + ':').encode('ascii')
            responses = [line[len(prefix):] for line in completed.stdout.splitlines() if line.startswith(prefix)]
            if len(responses) != 1:
                raise RuntimeError('目标机文件响应标识缺失或重复')
            result = json.loads(responses[0].decode('utf-8'))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError('目标机返回了无法识别的文件信息') from exc
        if not isinstance(result, dict):
            raise RuntimeError('目标机文件响应格式错误')
        if not result.get('ok'):
            raise ValueError(str(result.get('error') or '无法读取文件'))
        if action == 'read':
            result['mime'] = mimetypes.guess_type(result.get('name', ''))[0] or 'application/octet-stream'
        return result
