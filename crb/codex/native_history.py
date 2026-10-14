"""Conservative, read-only item hydration for native paginated turn metadata.

Pagination, turn status and cursors stay authoritative in app-server. Only an
already current local projection with the known schema is read here. No schema
change, repair, cache reset, task resume, or guessed cursor is ever performed.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
SEGMENT_SUFFIX = re.compile('[-_](' + UUID + ')\\.jsonl$', re.IGNORECASE)
MAX_META_BYTES = 4 * 1024 * 1024
MAX_SEGMENTS = 128

def canonical_path(value):
    raw = str(value)
    if raw.startswith('\\\\?\\UNC\\'):
        raw = '\\\\' + raw[8:]
    elif raw.startswith('\\\\?\\'):
        raw = raw[4:]
    return Path(raw).expanduser().resolve()

class ProjectionUnavailable(Exception):
    """Fall back to the unchanged official full read, never partial success."""

class NativeHistoryReader:

    def __init__(self, home):
        self.home = Path(home).expanduser().resolve()
        self.lock = threading.RLock()
        self.paths = {}
        self.paths_at = 0.0
        self.metadata = {}
        self.last_source = 'codex-app-server'
        self.last_fallback_reason = None

    def _index(self):
        paths = {}
        count = 0
        deadline = time.monotonic() + 3
        for root in (self.home / 'sessions', self.home / 'archived_sessions'):
            if not root.is_dir():
                continue
            for path in root.rglob('*.jsonl'):
                if time.monotonic() > deadline:
                    raise ProjectionUnavailable('Rollout index walk exceeds time bound')
                match = SEGMENT_SUFFIX.search(path.name)
                if match:
                    paths.setdefault(match.group(1).lower(), []).append(path)
                    count += 1
                if count > 50000:
                    raise ProjectionUnavailable('Rollout index exceeds safe bound')
        self.paths = paths
        self.paths_at = time.monotonic()

    def _path(self, segment):
        candidates = self.paths.get(segment.lower(), [])
        if len(candidates) != 1:
            if time.monotonic() - self.paths_at > 5:
                self._index()
                candidates = self.paths.get(segment.lower(), [])
        if len(candidates) != 1:
            raise ProjectionUnavailable('Missing or ambiguous history segment')
        return candidates[0]

    def _meta(self, path):
        path = canonical_path(path)
        if not path.is_relative_to(self.home):
            raise ProjectionUnavailable('History lives outside configured Codex home')
        match = SEGMENT_SUFFIX.search(path.name)
        if not match:
            raise ProjectionUnavailable('Unknown rollout filename format')
        stat = path.stat()
        signature = (str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        cached = self.metadata.get(signature)
        if cached and stat.st_size >= cached['headerBytes']:
            return cached
        with path.open('rb') as f:
            line = f.readline(MAX_META_BYTES + 1)
        if len(line) > MAX_META_BYTES or not line.endswith(b'\n'):
            raise ProjectionUnavailable('Incomplete or oversized rollout header')
        row = json.loads(line)
        value = row.get('payload')
        if row.get('type') != 'session_meta' or not isinstance(value, dict):
            raise ProjectionUnavailable('Missing session metadata')
        if value.get('history_mode') != 'paginated':
            raise ProjectionUnavailable('Legacy history uses native full API')
        base = value.get('history_base')
        if base is not None and (not isinstance(base, dict)):
            raise ProjectionUnavailable('Unknown history base format')
        parent = base.get('thread_id') if base is not None else None
        boundary = base.get('end_ordinal_exclusive') if base is not None else None
        if base is not None and parent is None:
            raise ProjectionUnavailable('Incomplete history base')
        if parent is not None:
            if not isinstance(parent, str) or not re.fullmatch(UUID, parent, re.IGNORECASE):
                raise ProjectionUnavailable('Invalid history parent')
            if not isinstance(boundary, int) or isinstance(boundary, bool) or boundary < 0:
                raise ProjectionUnavailable('Unknown fork boundary for ' + match.group(1))
        parent_bytes = base.get('end_byte_offset') if base else None
        if parent is not None and (not isinstance(parent_bytes, int) or isinstance(parent_bytes, bool) or parent_bytes < 0):
            raise ProjectionUnavailable('Unknown parent projection boundary')
        result = {'segment': match.group(1).lower(), 'parent': parent, 'parentBytes': parent_bytes, 'boundary': boundary, 'headerBytes': len(line), 'path': path}
        if len(self.metadata) >= 256:
            self.metadata.clear()
        self.metadata[signature] = result
        return result

    def _segments(self, path, size_before):
        result, seen = ([], set())
        current = self._meta(path)
        upper = None
        required_bytes = size_before
        while True:
            segment = current['segment']
            if segment in seen or len(seen) >= MAX_SEGMENTS:
                raise ProjectionUnavailable('Cyclic or excessive history lineage')
            seen.add(segment)
            lower = current['boundary'] + 1 if current['parent'] else 1
            if upper is not None and lower > upper:
                raise ProjectionUnavailable('Inconsistent history boundaries')
            if current['path'].stat().st_size < required_bytes:
                raise ProjectionUnavailable('Referenced segment has been truncated')
            result.append((segment, lower, upper, required_bytes))
            if not current['parent']:
                break
            upper = current['boundary']
            required_bytes = current['parentBytes']
            current = self._meta(self._path(current['parent']))
        return list(reversed(result))

    @staticmethod
    def _schema(db):
        required = {'thread_items': {'thread_id', 'turn_id', 'item_id', 'rollout_ordinal', 'item_json'}, 'thread_turns': {'thread_id', 'turn_id', 'rollout_ordinal', 'status'}, 'thread_history_projection_state': {'thread_id', 'next_rollout_byte_offset'}}
        for table, columns in required.items():
            actual = {row[1] for row in db.execute('PRAGMA table_info(' + table + ')')}
            if not columns.issubset(actual):
                raise ProjectionUnavailable('Unsupported projection schema')
        columns = [row[2] for row in db.execute('PRAGMA index_info(idx_thread_items_by_turn_page)')]
        if columns != ['thread_id', 'turn_id', 'rollout_ordinal']:
            raise ProjectionUnavailable('Required native index is not present')

    def hydrate(self, metadata, source_path, size_before):
        turns = metadata.get('data')
        if not isinstance(turns, list) or any((not isinstance(t, dict) or t.get('itemsView') != 'notLoaded' or (not isinstance(t.get('id'), str)) for t in turns)):
            raise ProjectionUnavailable('Unknown native turn metadata format')
        if len({t['id'] for t in turns}) != len(turns) or any((t.get('rootTurnId') not in (None, t['id']) or t.get('items') for t in turns)):
            raise ProjectionUnavailable('Turn aliases or unexpected metadata items need native hydration')
        path = canonical_path(source_path)
        database = self.home / 'thread_history_1.sqlite'
        if not database.is_file():
            raise ProjectionUnavailable('No native projection')
        with self.lock:
            if not self.paths or time.monotonic() - self.paths_at > 60:
                self._index()
            segments = self._segments(path, size_before)
            with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
                db.execute('PRAGMA query_only=ON')
                self._schema(db)
                db.execute('BEGIN')
                for segment, _, _, required_bytes in segments:
                    head = db.execute('SELECT next_rollout_byte_offset FROM thread_history_projection_state WHERE thread_id=?', (segment,)).fetchone()
                    if head is None or head[0] < required_bytes:
                        raise ProjectionUnavailable('Projection has not consumed the requested file snapshot')
                hydrated = []
                for turn in turns:
                    found_turn = False
                    items = []
                    seen_items = set()
                    for segment, lower, upper, _ in segments:
                        bound = ' AND rollout_ordinal < ?' if upper is not None else ''
                        args = (segment, turn['id'], lower) + ((upper,) if upper is not None else ())
                        exists = db.execute('SELECT status FROM thread_turns WHERE thread_id=? AND turn_id=? AND rollout_ordinal>=?' + bound, args).fetchone()
                        if exists is not None:
                            found_turn = True
                        rows = db.execute('SELECT item_id,item_json FROM thread_items INDEXED BY idx_thread_items_by_turn_page WHERE thread_id=? AND turn_id=? AND rollout_ordinal>=?' + bound + ' ORDER BY rollout_ordinal ASC', args)
                        for item_id, raw in rows:
                            item = json.loads(raw)
                            if not isinstance(item, dict) or item.get('id') != item_id:
                                raise ProjectionUnavailable('Malformed projected item')
                            if item_id in seen_items:
                                raise ProjectionUnavailable('Overlapping history items require native hydration')
                            seen_items.add(item_id)
                            items.append(item)
                    if not found_turn:
                        raise ProjectionUnavailable('Native metadata is not projected')
                    hydrated.append({**turn, 'itemsView': 'full', 'items': items})
                return {**metadata, 'data': hydrated}

    def read(self, appserver, thread_id, params, source_path, timeout=60):
        self.last_source = 'codex-app-server'
        self.last_fallback_reason = None
        try:
            self._meta(source_path)
            size_before = canonical_path(source_path).stat().st_size
        except (OSError, ValueError, TypeError, ProjectionUnavailable) as exc:
            self.last_fallback_reason = type(exc).__name__ + ': ' + str(exc)[:200]
            return appserver.request('thread/turns/list', params, timeout=timeout)
        metadata = appserver.request('thread/turns/list', {**params, 'itemsView': 'notLoaded'}, timeout=timeout)
        try:
            result = self.hydrate(metadata, source_path, size_before)
        except (OSError, ValueError, TypeError, sqlite3.Error, ProjectionUnavailable) as exc:
            self.last_fallback_reason = type(exc).__name__ + ': ' + str(exc)[:200]
            return appserver.request('thread/turns/list', params, timeout=timeout)
        self.last_source = 'codex-app-server-indexed-projection'
        return result
