"""Stable persisted names and lossless recovery of the module-layout regression.

Python package names must never become database/configuration filenames. The
mistaken database is retained, and consistent SQLite backups are made before
merging anything. No chat or credential data is included in recovery logs.
"""
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import time
DATABASE_NAME = 'relay.sqlite'
REGRESSED_DATABASE_NAME = 'crb.server.relay.sqlite'
SCHEMAS = {'commands': 'id TEXT PRIMARY KEY, host TEXT, thread TEXT, action TEXT, text TEXT, created REAL, result TEXT', 'traffic_sessions': 'id INTEGER PRIMARY KEY, started REAL NOT NULL, ended REAL', 'traffic_minutes': 'session_id INTEGER NOT NULL, minute INTEGER NOT NULL, upload_bytes INTEGER NOT NULL DEFAULT 0, download_bytes INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(session_id,minute)', 'ui_settings': 'key TEXT PRIMARY KEY, value TEXT NOT NULL', 'workspace_threads': 'host TEXT NOT NULL, thread TEXT NOT NULL, title TEXT NOT NULL, host_label TEXT NOT NULL, created REAL NOT NULL, sort_order INTEGER NOT NULL, PRIMARY KEY(host,thread)', 'client_devices': 'host_id TEXT PRIMARY KEY, physical_host_id TEXT NOT NULL, application_id TEXT NOT NULL, installation_id TEXT NOT NULL, label TEXT NOT NULL, token_sha256 TEXT NOT NULL UNIQUE, created REAL NOT NULL, updated REAL NOT NULL', 'client_credential_revocations': 'token_sha256 TEXT PRIMARY KEY, host_id TEXT NOT NULL, application_id TEXT NOT NULL, revoked REAL NOT NULL', 'client_pairing_epochs': 'host_id TEXT PRIMARY KEY, epoch INTEGER NOT NULL'}

def _fingerprint(path):
    digest = hashlib.sha256()
    for file in (path, path.with_name(path.name + '-wal')):
        digest.update(file.name.encode())
        if file.exists():
            with file.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
    return digest.hexdigest()

def _backup(source, destination):
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True, timeout=15)) as reader, closing(sqlite3.connect(destination)) as writer:
        reader.backup(writer)
        if writer.execute('PRAGMA quick_check').fetchone() != ('ok',):
            raise RuntimeError('Database recovery backup failed integrity verification')
    destination.chmod(384)

def _merge(db, source, include=None):
    tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if include is not None:
        if not set(include).issubset(SCHEMAS):
            raise ValueError('Unknown recovery table')
        tables.intersection_update(include)
    for table, schema in SCHEMAS.items():
        db.execute(f'CREATE TABLE IF NOT EXISTS {table} ({schema})')
    for connection in (db, source):
        if 'workspace_threads' in tables and 'sort_order' not in {row[1] for row in connection.execute('PRAGMA table_info(workspace_threads)')}:
            if connection is source:
                raise RuntimeError('Unexpected regressed workspace schema; backups retained')
            connection.execute('ALTER TABLE workspace_threads ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0')

    def rows(table):
        return source.execute(f'SELECT * FROM {table}') if table in tables else ()
    for row in rows('client_credential_revocations'):
        db.execute('INSERT INTO client_credential_revocations VALUES(?,?,?,?) ON CONFLICT(token_sha256) DO UPDATE SET revoked=MAX(revoked,excluded.revoked)', tuple(row))
    for row in rows('client_pairing_epochs'):
        db.execute('INSERT INTO client_pairing_epochs VALUES(?,?) ON CONFLICT(host_id) DO UPDATE SET epoch=MAX(epoch,excluded.epoch)', tuple(row))
    for row in rows('client_devices'):
        if db.execute('SELECT 1 FROM client_credential_revocations WHERE token_sha256=?', (row['token_sha256'],)).fetchone():
            continue
        previous = db.execute('SELECT * FROM client_devices WHERE host_id=?', (row['host_id'],)).fetchone()
        if previous and any((previous[key] != row[key] for key in ('physical_host_id', 'application_id', 'installation_id'))):
            raise RuntimeError('Conflicting device identities; recovery stopped, backups retained')
        if previous is None or row['updated'] > previous['updated']:
            db.execute('INSERT INTO client_devices VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(host_id) DO UPDATE SET label=excluded.label,token_sha256=excluded.token_sha256,updated=excluded.updated', tuple(row))
    for row in rows('commands'):
        db.execute('INSERT OR IGNORE INTO commands VALUES(?,?,?,?,?,?,?)', tuple(row))
    for row in rows('ui_settings'):
        db.execute('INSERT INTO ui_settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', tuple(row))
    first_recovery = not db.execute("SELECT 1 FROM sqlite_master WHERE name='database_recovery'").fetchone()
    db.execute('CREATE TABLE IF NOT EXISTS database_recovery_workspace (host TEXT,thread TEXT,PRIMARY KEY(host,thread))')
    authoritative = first_recovery and 'workspace_recovery_baseline' in tables and ('workspace_threads' in tables)
    if authoritative:
        for row in source.execute('SELECT host,thread FROM workspace_recovery_baseline'):
            if not source.execute('SELECT 1 FROM workspace_threads WHERE host=? AND thread=?', tuple(row)).fetchone():
                db.execute('DELETE FROM workspace_threads WHERE host=? AND thread=?', tuple(row))
    order = 0 if authoritative else db.execute('SELECT COALESCE(MAX(sort_order),-1)+1 FROM workspace_threads').fetchone()[0]
    if 'workspace_threads' in tables:
        for row in source.execute('SELECT * FROM workspace_threads ORDER BY sort_order,created,rowid'):
            key = (row['host'], row['thread'])
            if db.execute('SELECT 1 FROM database_recovery_workspace WHERE host=? AND thread=?', key).fetchone():
                continue
            if authoritative:
                db.execute('UPDATE workspace_threads SET title=?,host_label=?,sort_order=? WHERE host=? AND thread=?', (row['title'], row['host_label'], order, *key))
            inserted = db.execute('INSERT OR IGNORE INTO workspace_threads VALUES(?,?,?,?,?,?)', (row['host'], row['thread'], row['title'], row['host_label'], row['created'], order)).rowcount
            order += 1 if authoritative else inserted
            db.execute('INSERT INTO database_recovery_workspace VALUES(?,?)', key)
    sessions = {}
    for row in rows('traffic_sessions'):
        found = db.execute('SELECT id FROM traffic_sessions WHERE started=?', (row['started'],)).fetchone()
        if found:
            sessions[row['id']] = found[0]
        else:
            sessions[row['id']] = db.execute('INSERT INTO traffic_sessions(started,ended) VALUES(?,?)', (row['started'], row['ended'])).lastrowid
    db.execute('CREATE TABLE IF NOT EXISTS database_recovery_traffic (source_session INTEGER,minute INTEGER,upload_bytes INTEGER,download_bytes INTEGER,PRIMARY KEY(source_session,minute))')
    for row in rows('traffic_minutes'):
        if row['session_id'] not in sessions:
            raise RuntimeError('Orphaned traffic records; recovery stopped, backups retained')
        before = db.execute('SELECT upload_bytes,download_bytes FROM database_recovery_traffic WHERE source_session=? AND minute=?', (row['session_id'], row['minute'])).fetchone() or (0, 0)
        upload, download = (max(0, row['upload_bytes'] - before[0]), max(0, row['download_bytes'] - before[1]))
        db.execute('INSERT INTO traffic_minutes VALUES(?,?,?,?) ON CONFLICT(session_id,minute) DO UPDATE SET upload_bytes=upload_bytes+excluded.upload_bytes,download_bytes=download_bytes+excluded.download_bytes', (sessions[row['session_id']], row['minute'], upload, download))
        db.execute('INSERT INTO database_recovery_traffic VALUES(?,?,?,?) ON CONFLICT(source_session,minute) DO UPDATE SET upload_bytes=MAX(upload_bytes,excluded.upload_bytes),download_bytes=MAX(download_bytes,excluded.download_bytes)', (row['session_id'], row['minute'], row['upload_bytes'], row['download_bytes']))

def relay_database(state_dir):
    root = Path(state_dir).expanduser()
    primary, regressed = (root / DATABASE_NAME, root / REGRESSED_DATABASE_NAME)
    root.mkdir(parents=True, exist_ok=True, mode=448)
    if not regressed.is_file():
        return primary
    if regressed.is_symlink() or primary.is_symlink():
        raise RuntimeError('Database recovery does not follow symbolic links')
    signature = _fingerprint(regressed)
    with closing(sqlite3.connect(primary, timeout=15)) as db:
        db.row_factory = sqlite3.Row
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='database_recovery'").fetchone()
        if exists and db.execute('SELECT 1 FROM database_recovery WHERE source=? AND signature=?', (REGRESSED_DATABASE_NAME, signature)).fetchone():
            return primary
        db.execute('BEGIN IMMEDIATE')
        backups = root / 'backups' / ('database-recovery-' + str(time.time_ns()))
        backups.mkdir(parents=True, mode=448)
        try:
            _backup(primary, backups / DATABASE_NAME)
            _backup(regressed, backups / REGRESSED_DATABASE_NAME)
            if _fingerprint(regressed) != signature:
                raise RuntimeError('Regressed database is still being written; stop the old server before recovery')
            with closing(sqlite3.connect((backups / REGRESSED_DATABASE_NAME).resolve().as_uri() + '?mode=ro', uri=True)) as source:
                source.row_factory = sqlite3.Row
                _merge(db, source)
            db.execute('CREATE TABLE IF NOT EXISTS database_recovery (source TEXT PRIMARY KEY,signature TEXT NOT NULL,recovered REAL NOT NULL,backup TEXT NOT NULL)')
            db.execute('INSERT OR REPLACE INTO database_recovery VALUES(?,?,?,?)', (REGRESSED_DATABASE_NAME, signature, time.time(), backups.name))
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeError('Recovered database failed integrity verification; backups retained')
            db.commit()
        except Exception:
            db.rollback()
            raise
    return primary
