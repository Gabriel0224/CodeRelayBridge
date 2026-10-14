"""Bounded live-view normalization cache for immutable native turn snapshots."""
from collections import OrderedDict
import time

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
