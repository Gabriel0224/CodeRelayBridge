"""Acknowledged metadata deltas shared by native bridge workers and relay.

The first request remains a legacy full sync. Deltas start only after an exact
server acknowledgement; a lost base is retried in full before results are retired.
"""
import copy
import gzip
import hashlib
import json
ENVELOPE = 'catalogSync'
TRANSIENT = frozenset(('host', 'views', 'results', 'controlHeartbeat', 'clientControl', ENVELOPE))

def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()

def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()

def indexed(rows):
    if not isinstance(rows, list):
        raise ValueError('Invalid catalog rows')
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or row['id'] in result:
            raise ValueError('Invalid catalog identity')
        result[row['id']] = row
    return result

def rows_patch(before, after):
    old, new = (indexed(before), indexed(after))
    patch = {'upserts': [row for key, row in new.items() if old.get(key) != row], 'removed': [key for key in old if key not in new]}
    if list(old) != list(new):
        patch['order'] = list(new)
    return patch

def apply_rows(before, patch):
    if not isinstance(patch, dict):
        raise ValueError('Invalid catalog patch')
    rows = indexed(before).copy()
    for key in patch.get('removed', []):
        rows.pop(key, None)
    rows.update(indexed(patch.get('upserts', [])))
    order = patch.get('order', list(rows))
    if not isinstance(order, list) or len(order) != len(rows) or set(order) != set(rows):
        raise ValueError('Invalid catalog order')
    return [rows[key] for key in order]

class CatalogSync:

    def __init__(self):
        self.acknowledged = None
        self.ack_hash = None
        self.pending = None
        self.pending_hash = None

    def prepare(self, payload, force_full=False):
        current = {key: value for key, value in payload.items() if key not in TRANSIENT}
        self.pending = copy.deepcopy(current)
        self.pending_hash = digest(current)
        full = {**payload, ENVELOPE: {'protocol': 1, 'hash': self.pending_hash}}
        if self.acknowledged is None or force_full:
            return full
        changed = {key: value for key, value in current.items() if self.acknowledged.get(key) != value or key not in self.acknowledged}
        patch = {'protocol': 1, 'base': self.ack_hash, 'hash': self.pending_hash, 'set': changed, 'unset': [key for key in self.acknowledged if key not in current]}
        if 'catalog' in changed and 'catalog' in self.acknowledged:
            try:
                rows = rows_patch(self.acknowledged['catalog'], current['catalog'])
                if len(encode(rows)) < len(encode(changed['catalog'])):
                    patch['catalog'] = rows
                    changed.pop('catalog')
            except ValueError:
                pass
        delta = {key: value for key, value in payload.items() if key in TRANSIENT and key != ENVELOPE}
        delta[ENVELOPE] = patch
        return delta if len(encode(delta)) < len(encode(full)) else full

    def acknowledge(self, response):
        ack = response.get(ENVELOPE, {})
        if isinstance(ack, dict) and ack.get('protocol') == 1 and (not ack.get('resync')) and (ack.get('hash') == self.pending_hash):
            self.acknowledged, self.ack_hash = (self.pending, self.pending_hash)
        else:
            self.acknowledged = self.ack_hash = None

def receive_metadata(data, previous):
    """Return (expanded legacy-compatible payload, next base), or None to resync.

    Do not mutate the prior base or process any command results until validated.
    """
    envelope = data.get(ENVELOPE)
    if envelope is None:
        return (data, None)
    if not isinstance(envelope, dict) or envelope.get('protocol') != 1:
        raise ValueError('Unsupported catalog sync protocol')
    if 'base' in envelope:
        if not previous or envelope['base'] != previous['hash']:
            return None
        metadata = copy.deepcopy(previous['metadata'])
        updates = envelope.get('set', {})
        if not isinstance(updates, dict) or any((key in TRANSIENT for key in updates)):
            raise ValueError('Invalid metadata fields')
        metadata.update(updates)
        for key in envelope.get('unset', []):
            metadata.pop(key, None)
        if 'catalog' in envelope:
            metadata['catalog'] = apply_rows(metadata.get('catalog', []), envelope['catalog'])
    else:
        metadata = {key: value for key, value in data.items() if key not in TRANSIENT}
    checksum = digest(metadata)
    if checksum != envelope.get('hash'):
        return None
    expanded = {**metadata, **{key: value for key, value in data.items() if key in TRANSIENT}}
    return (expanded, {'hash': checksum, 'metadata': copy.deepcopy(metadata)})

def wire_body(payload):
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
    if len(raw) >= 1024:
        compressed = gzip.compress(raw, compresslevel=5, mtime=0)
        if len(compressed) + 32 < len(raw):
            return (compressed, 'gzip')
    return (raw, '')
