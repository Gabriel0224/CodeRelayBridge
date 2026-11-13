"""Read the running Desktop's queue wire contract without executing its code."""
import json
from pathlib import Path
import re
import struct
import threading
import psutil
from crb.codex.question_ui import codex_image
SET_QUEUE = 'thread-follower-set-queued-follow-ups-state'
QUEUE_CHANGED = 'thread-queued-followups-changed'
_REQUIRED = (SET_QUEUE, QUEUE_CHANGED, 'thread-owner-discovery', 'thread-follower-start-turn')
_MARKER = re.compile(b'"thread-follower-set-queued-follow-ups-state"\\s*:\\s*[0-9]+')

def running_archives():
    owner = psutil.Process().username()
    result = set()
    for process in psutil.process_iter(['exe', 'cmdline']):
        try:
            exe = process.info['exe'] or ''
            if not codex_image(exe) or process.username().casefold() != owner.casefold() or any((arg.startswith('--type=') for arg in process.info['cmdline'] or [])):
                continue
            binary = Path(exe)
            for path in (binary.parent / 'resources/app.asar', binary.parent.parent / 'Resources/app.asar'):
                if path.is_file():
                    result.add(path.resolve())
        except (psutil.Error, OSError):
            continue
    return sorted(result)

def read_contract(path):
    """Parse only literal integer protocol tables from bounded main-process assets."""
    path = Path(path)
    before = path.stat()
    tables = set()
    with path.open('rb') as stream:
        prefix = stream.read(16)
        if len(prefix) != 16:
            raise ValueError('Invalid Desktop archive')
        _, header_size, _, json_size = struct.unpack('<IIII', prefix)
        if not 0 < json_size <= 16 * 1024 * 1024 or header_size < json_size + 8:
            raise ValueError('Invalid Desktop archive header')
        header = json.loads(stream.read(json_size))
        base = 8 + header_size
        assets = []

        def walk(node, parent=''):
            for name, value in node.get('files', {}).items():
                filename = parent + name
                if 'files' in value:
                    walk(value, filename + '/')
                elif filename.startswith('.vite/build/') and filename.endswith(('.js', '.cjs', '.mjs')) and ('offset' in value) and (not value.get('unpacked')):
                    size, offset = (int(value.get('size', 0)), int(value['offset']))
                    if 0 < size <= 24 * 1024 * 1024 and 0 <= offset and (base + offset + size <= before.st_size):
                        assets.append((filename, offset, size))
        walk(header)
        assets.sort(key=lambda row: (row[0] != '.vite/build/early-bootstrap.js', row[2]))
        read_bytes = 0
        for name, offset, size in assets:
            read_bytes += size
            if read_bytes > 128 * 1024 * 1024:
                raise ValueError('Desktop protocol assets exceed inspection limit')
            stream.seek(base + offset)
            raw = stream.read(size)
            for match in _MARKER.finditer(raw):
                start = raw.rfind(b'{', max(0, match.start() - 16384), match.start())
                if start < 0:
                    continue
                try:
                    end = raw.find(b'}', match.end(), start + 32768)
                    if end < 0:
                        continue
                    value = json.loads(raw[start:end + 1])
                except (ValueError, UnicodeError):
                    continue
                if isinstance(value, dict) and all((key in value for key in _REQUIRED)) and all((isinstance(k, str) and type(v) is int and (v >= 0) for k, v in value.items())):
                    tables.add(tuple((value[key] for key in _REQUIRED)))
            if name == '.vite/build/early-bootstrap.js' and tables:
                break
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('Desktop changed during protocol inspection')
    if len(tables) != 1:
        raise ValueError('Ambiguous Desktop queue protocol')
    setter, event, owner, start = tables.pop()
    if setter not in (1, 2, 3) or event not in (2, 3) or owner != 1 or (start != 2):
        raise ValueError('Unsupported Desktop queue protocol')
    return {'setter': setter, 'event': event}

class NativeQueueProtocol:

    def __init__(self, paths=running_archives):
        self.paths = paths
        self.cached = None
        self.signature = None
        self.lock = threading.Lock()

    def current(self):
        with self.lock:
            try:
                paths = self.paths()
                if len(paths) != 1:
                    raise ValueError('Cannot identify one running Desktop')
                path = Path(paths[0])
                stat = path.stat()
                signature = (str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns)
                if signature != self.signature:
                    self.cached = read_contract(path)
                    self.signature = signature
                return dict(self.cached)
            except (OSError, ValueError, TypeError, KeyError, struct.error) as error:
                self.cached = self.signature = None
                raise RuntimeError('无法确认原 App 的队列协议；消息未提交，请更新客户端或重新连接原 App') from error
