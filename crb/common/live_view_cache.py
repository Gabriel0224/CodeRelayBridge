"""Bounded memory caches for native reads and immutable native turn snapshots."""
from collections import OrderedDict
import hashlib
import json
import threading
import time
NATIVE_READ_TTLS = {'sidebar': 30, 'tasks': 30, 'permissions': 15, 'tools': 60, 'metadata': 5}

class NativeReadCache:
    """Only sanitized results; no disk cache, background worker or native writes."""

    def __init__(self, max_entries=32, max_bytes=2 * 1024 * 1024, clock=time.monotonic):
        self.entries = OrderedDict()
        self.max_entries, self.max_bytes, self.clock = (max_entries, max_bytes, clock)
        self.size = 0
        self.lock = threading.RLock()
        self.generation = 0
        self.inflight = {}

    def invalidate(self):
        with self.lock:
            self.entries.clear()
            self.size = 0
            self.generation += 1

    def read(self, key, payload, loader, ttl):
        while True:
            with self.lock:
                now = self.clock()
                for old_key, entry in list(self.entries.items()):
                    if entry['expires'] <= now:
                        self.size -= len(self.entries.pop(old_key)['raw'])
                entry = None if payload.get('refresh') is True else self.entries.get(key)
                if entry is not None:
                    self.entries.move_to_end(key)
                    return self.reply(entry, payload)
                previous = self.entries.pop(key, None)
                if previous:
                    self.size -= len(previous['raw'])
                flight_key = (self.generation, key)
                event = self.inflight.get(flight_key)
                if event is None:
                    event = self.inflight[flight_key] = threading.Event()
                    break
            if not event.wait(60):
                raise TimeoutError('刷新超时，请稍后重试；正在运行的任务未停止')
        try:
            value = loader()
            if not isinstance(value, dict) or value.get('ok') is not True:
                return value
            raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
            if len(raw) > min(256 * 1024, self.max_bytes):
                return value
            entry = {'raw': raw, 'revision': hashlib.sha256(raw).hexdigest(), 'expires': self.clock() + ttl}
            with self.lock:
                if flight_key[0] != self.generation:
                    return value
                previous = self.entries.pop(key, None)
                if previous:
                    self.size -= len(previous['raw'])
                self.entries[key] = entry
                self.size += len(raw)
                while len(self.entries) > self.max_entries or self.size > self.max_bytes:
                    self.size -= len(self.entries.popitem(last=False)[1]['raw'])
                return self.reply(entry, payload)
        finally:
            with self.lock:
                self.inflight.pop(flight_key, None)
                event.set()

    def reply(self, entry, payload):
        info = {'revision': entry['revision'], 'maxAgeMs': max(0, int((entry['expires'] - self.clock()) * 1000))}
        if payload.get('ifRevision') == entry['revision']:
            return {'ok': True, 'notModified': True, 'readCache': info}
        return {**json.loads(entry['raw']), 'readCache': info}

class LiveTurnCache:

    def __init__(self, max_turns=2048):
        self.entries = OrderedDict()
        self.max_turns = max_turns

    def project(self, turns, convert, recent_limit=500):
        total = 0
        recent = []
        needed = True
        active_keys = set()
        now = time.monotonic()
        for index in range(len(turns) - 1, -1, -1):
            turn = turns[index]
            key = (str(turn.get('turnId') or turn.get('id') or index), index)
            active_keys.add(key)
            entry = self.entries.get(key)
            refresh_assets = needed and entry and entry.get('hasAssets') and (now - entry['at'] >= 60)
            if entry is None or entry['raw'] is not turn or refresh_assets or (needed and entry['items'] is None):
                values = convert(turn, index)
                entry = {'raw': turn, 'count': len(values), 'items': values[-recent_limit:] if needed else None, 'hasAssets': any((i.get('attachments') for i in values)), 'at': now}
                self.entries[key] = entry
            elif not needed:
                entry['items'] = None
            self.entries.move_to_end(key)
            total += entry['count']
            if needed:
                recent = entry['items'] + recent
                if len(recent) >= recent_limit:
                    recent = recent[-recent_limit:]
                    needed = False
        for key in list(self.entries):
            if key not in active_keys:
                del self.entries[key]
        while len(self.entries) > self.max_turns:
            self.entries.popitem(last=True)
        return (recent, total)
