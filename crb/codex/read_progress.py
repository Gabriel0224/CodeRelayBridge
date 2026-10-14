"""Private, per-machine reading receipts; no prompts or login credentials."""
from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import threading
import uuid

class ReadProgress:

    def __init__(self, database):
        self.database = str(database)
        self.lock = threading.RLock()
        self.records = {}
        Path(database).parent.mkdir(parents=True, exist_ok=True, mode=448)
        with closing(sqlite3.connect(self.database, timeout=5)) as db:
            db.execute('CREATE TABLE IF NOT EXISTS read_progress (thread TEXT PRIMARY KEY, state TEXT NOT NULL)')
            for thread, state in db.execute('SELECT thread,state FROM read_progress'):
                value = json.loads(state)
                if isinstance(value, dict):
                    self.records[thread] = value
            db.commit()

    def _record(self, thread):
        if thread not in self.records:
            self.records[thread] = {'sources': {}, 'ack': {}, 'completed': None, 'receipt': str(uuid.uuid4())}
        return self.records[thread]

    def _save(self, threads):
        if not threads:
            return
        with closing(sqlite3.connect(self.database, timeout=5)) as db:
            for thread in threads:
                value = self.records[thread]
                value['receipt'] = str(uuid.uuid4())
                db.execute('INSERT OR REPLACE INTO read_progress VALUES (?,?)', (thread, json.dumps(value, separators=(',', ':'))))
            db.commit()

    def snapshot(self, thread):
        with self.lock:
            value = self._record(thread)
            return {'receipt': value['receipt'], 'completed': value['completed'], 'sources': {key: source[1] for key, source in value['sources'].items() if source[0]}}

    def receipt(self, thread):
        return self.snapshot(thread)['receipt']

    def _unread(self, value):
        return any((present and value['ack'].get(key) != [version, value['completed']] for key, (present, version) in value['sources'].items()))

    def unread(self):
        with self.lock:
            return {thread for thread, value in self.records.items() if self._unread(value)}

    def _source(self, thread, origin, present, force=False):
        value = self._record(thread)
        previous = value['sources'].get(origin, [False, 0])
        if previous[0] == present and (not (present and force)):
            return False
        value['sources'][origin] = [present, previous[1] + int(present)]
        return True

    def _acknowledge(self, thread, snapshot):
        value = self._record(thread)
        changed = False
        for origin, version in snapshot['sources'].items():
            existing = value['ack'].get(origin)
            if existing and (existing[0] > version or (existing == [version, value['completed']] and snapshot['completed'] != value['completed'])):
                continue
            receipt = [version, snapshot['completed']]
            if existing != receipt:
                value['ack'][origin] = receipt
                changed = True
        return changed

    def acknowledge(self, thread, snapshot):
        with self.lock:
            if self._acknowledge(thread, snapshot):
                self._save({thread})
            return thread not in self.unread()

    def observe_sources(self, sources, current_origin=None, pending=None):
        """Absence from a different/new account is not a reading confirmation."""
        with self.lock:
            changed = set()
            for origin, unread in sources.items():
                known = {thread for thread, value in self.records.items() if origin in value['sources']}
                for thread in known | unread:
                    value = self._record(thread)
                    was_unread = value['sources'].get(origin, [False, 0])[0]
                    present = thread in unread
                    if not self._source(thread, origin, present):
                        continue
                    changed.add(thread)
                    if was_unread and (not present) and (origin == current_origin):
                        snapshot = (pending or {}).get(thread) or self.snapshot(thread)
                        self._acknowledge(thread, snapshot)
            self._save(changed)
            return changed

    def native_event(self, thread, origin, present, snapshot=None):
        with self.lock:
            captured = copy.deepcopy(snapshot or self.snapshot(thread))
            changed = self._source(thread, origin, True, force=True) if present else False
            if not present:
                changed |= self._acknowledge(thread, captured)
            if changed:
                self._save({thread})
            return thread not in self.unread()

    def observe_conversation(self, thread, completed, has_unread):
        with self.lock:
            value = self._record(thread)
            changed = False
            new_completion = bool(completed and completed != value['completed'])
            if new_completion:
                if has_unread is False:
                    for origin, ack in value['ack'].items():
                        source = value['sources'].get(origin)
                        if source and ack == [source[1], value['completed']]:
                            value['ack'][origin] = [ack[0], completed]
                value['completed'] = completed
                changed = True
            if isinstance(has_unread, bool):
                changed |= self._source(thread, 'core', has_unread, force=has_unread and new_completion)
            if changed:
                self._save({thread})
            return changed
