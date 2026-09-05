"""Content-free worker health and intent, readable even while Keychain is locked."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import fcntl
import os
import sqlite3
import threading
import time

from . import operations
from .securestore import StorageError, private_directory

SCHEMA_VERSION = 1
HEARTBEAT_INTERVAL = 5
STALE_AFTER = 30
STATES = {'starting', 'ready', 'paused', 'offline', 'permission_needed', 'error', 'stopped'}
REASONS = {None, 'user_paused', 'keychain_locked', 'storage_unavailable', 'permission_denied',
           'network_unavailable', 'provider_cooldown', 'worker_failed', 'heartbeat_stale',
           'worker_missing', 'initializing', 'resuming', 'legacy_review', 'queue_review'}
_OWNER = ContextVar('notron_worker_owner', default=None)


def classify(*, registered, heartbeat_age, paused, permission_ok, network_ok):
    if heartbeat_age is not None and (heartbeat_age < 0 or heartbeat_age > STALE_AFTER):
        return 'error'
    if paused:
        return 'paused'
    if heartbeat_age is None:
        return 'starting' if registered else 'stopped'
    if not permission_ok:
        return 'permission_needed'
    if not network_ok:
        return 'offline'
    return 'ready'


class WorkerLock:
    """The existing request mutex, held for the entire listener/CLI lifecycle.

    Ownership is reentrant in one thread only. A fork or a second thread/process
    cannot inherit execution authority from a Python context value.
    """
    def __init__(self, *, blocking=False):
        self.path = operations.PATH.parent / 'request-execution.lock'
        self.blocking = blocking
        self.acquired = False
        self.fd = self.token = None

    @staticmethod
    def owned():
        return _OWNER.get() == (os.getpid(), threading.get_ident(), str(operations.PATH.parent))

    def try_acquire(self):
        if self.acquired:
            return True
        if self.owned():
            self.acquired = True
            return True
        private_directory(self.path.parent)
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | (0 if self.blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            os.close(self.fd)
            self.fd = None
            return False
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise
        self.token = _OWNER.set((os.getpid(), threading.get_ident(), str(operations.PATH.parent)))
        self.acquired = True
        return True

    def release(self):
        if self.fd is not None:
            _OWNER.reset(self.token)
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = self.token = None
        self.acquired = False

    def __enter__(self):
        self.try_acquire()
        return self

    def __exit__(self, *exc):
        self.release()


def owner_alive():
    if WorkerLock.owned():
        return True
    with WorkerLock() as lock:
        return not lock.acquired


def control_artifacts(root):
    """Recognize an unused control plane without treating user storage as empty.

    Status/pause may precede key provisioning or offline migration. Only a known
    schema with zero jobs and validated metadata can accompany a fresh key.
    """
    root = root.absolute()
    if not root.exists() or root.is_symlink():
        return set()
    names = {p.name for p in root.iterdir()}
    allowed = {'worker.sqlite3', 'worker.sqlite3-wal', 'worker.sqlite3-shm',
               'request-execution.lock', 'queue-admission.lock'}
    present = names & allowed
    if any((root / name).is_symlink() or not (root / name).is_file() for name in present):
        return set()
    for name in present & {'request-execution.lock', 'queue-admission.lock'}:
        if (root / name).stat().st_size:
            return set()
    if present & {'worker.sqlite3-wal', 'worker.sqlite3-shm'} and 'worker.sqlite3' not in present:
        return set()
    if 'worker.sqlite3' in present:
        try:
            with sqlite3.connect((root / 'worker.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
                if (db.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION
                        or {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} != {'health', 'jobs', 'cooldowns'}
                        or db.execute('SELECT count(*) FROM jobs').fetchone()[0] != 0
                        or db.execute('SELECT count(*) FROM cooldowns').fetchone()[0] != 0):
                    return set()
                rows = db.execute('SELECT state,reason_code,heartbeat_at,last_success_at,paused,intent_version,stop_requested FROM health').fetchall()
                if len(rows) != 1:
                    return set()
                state, reason, beat, success, paused, intent, stopped = rows[0]
                if (state not in STATES or reason not in REASONS or paused not in (0, 1) or stopped not in (0, 1)
                        or type(intent) is not int or intent < 0
                        or any(v is not None and not isinstance(v, (int, float)) for v in (beat, success))):
                    return set()
        except sqlite3.DatabaseError:
            return set()
    return present


class HealthStore:
    def __init__(self):
        self.path = operations.PATH.parent / 'worker.sqlite3'
        private_directory(self.path.parent)
        self._safe_paths()
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with self.connection() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise StorageError('Worker schema requires explicit migration.')
            if version == 0:
                db.executescript('''
                    BEGIN IMMEDIATE;
                    CREATE TABLE IF NOT EXISTS health (
                        id INTEGER PRIMARY KEY CHECK(id=1),
                        state TEXT NOT NULL DEFAULT 'stopped', reason_code TEXT,
                        heartbeat_at REAL, last_success_at REAL,
                        paused INTEGER NOT NULL DEFAULT 0, intent_version INTEGER NOT NULL DEFAULT 0,
                        stop_requested INTEGER NOT NULL DEFAULT 0
                    );
                    INSERT OR IGNORE INTO health(id) VALUES(1);
                    CREATE TABLE IF NOT EXISTS jobs (
                        job_id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload_ref TEXT,
                        payload_hash TEXT NOT NULL, args_hash TEXT NOT NULL, status TEXT NOT NULL,
                        request_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS cooldowns (
                        key_hash TEXT PRIMARY KEY, failures INTEGER NOT NULL, attempted_at REAL NOT NULL
                    );
                    PRAGMA user_version=1;
                    COMMIT;
                ''')

    def _safe_paths(self):
        from pathlib import Path
        if any(Path(str(self.path) + suffix).is_symlink() for suffix in ('', '-wal', '-shm')):
            raise StorageError('Unsafe worker database path.')

    @contextmanager
    def connection(self):
        self._safe_paths()
        db = sqlite3.connect(self.path, timeout=2, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            yield db
        finally:
            db.close()

    @property
    def paused(self):
        return bool(self.row()['paused'])

    def row(self):
        with self.connection() as db:
            return dict(db.execute('SELECT * FROM health WHERE id=1').fetchone())

    def set_paused(self, value):
        with self.connection() as db:
            db.execute('UPDATE health SET paused=?,intent_version=intent_version+1 WHERE id=1', (bool(value),))

    def set_stop(self, value):
        with self.connection() as db:
            db.execute('UPDATE health SET stop_requested=? WHERE id=1', (bool(value),))

    def update(self, **values):
        if (set(values) - {'state', 'reason_code', 'heartbeat_at', 'last_success_at'}
                or ('state' in values and values['state'] not in STATES)
                or ('reason_code' in values and values['reason_code'] not in REASONS)):
            raise ValueError('Invalid worker metadata.')
        if values:
            with self.connection() as db:
                db.execute('UPDATE health SET ' + ','.join(k + '=?' for k in values) + ' WHERE id=1', tuple(values.values()))

    def success(self):
        self.update(last_success_at=time.time())

    def status(self, *, registered=False):
        row = self.row()
        alive = owner_alive()
        age = time.time() - row['heartbeat_at'] if row['heartbeat_at'] is not None else None
        state, reason = row['state'], row['reason_code']
        if alive:
            if age is None or age < 0 or age > STALE_AFTER:
                state, reason = 'error', 'heartbeat_stale'
            elif row['paused']:
                state, reason = 'paused', 'user_paused'
        elif registered:
            state, reason = 'error', 'worker_missing'
        elif row['paused']:
            state, reason = 'paused', 'user_paused'
        else:
            state, reason = 'stopped', None
        pending_ids = set()
        if operations.PATH.exists():
            # No OperationStore construction: status must not require Keychain.
            if operations.PATH.is_symlink():
                raise StorageError('Unsafe operation database path.')
            db = sqlite3.connect(operations.PATH.as_uri() + '?mode=ro', uri=True, timeout=2)
            try:
                pending_ids = {r[0] for r in db.execute(
                    "SELECT request_id FROM requests WHERE status IN ('prepared','running','needs_review')")}
            finally:
                db.close()
        with self.connection() as db:
            jobs = db.execute("SELECT job_id,request_id FROM jobs WHERE status IN ('queued','running','needs_review')").fetchall()
        count = len(pending_ids) + sum(j['request_id'] not in pending_ids for j in jobs)
        def stamp(value):
            return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None
        return {'version': 1, 'state': state, 'heartbeat_at': stamp(row['heartbeat_at']),
                'last_success_at': stamp(row['last_success_at']), 'pending_count': count,
                'reason_code': reason, 'running': alive and age is not None and 0 <= age <= STALE_AFTER}


class Heartbeat:
    """Never opens payloads or touches native apps/provider transports."""
    def __init__(self, store, *, interval=HEARTBEAT_INTERVAL):
        self.store, self.interval = store, interval
        self.stop = threading.Event()
        self.failed = threading.Event()
        self.closed = False

    def _beat(self):
        while not self.stop.wait(self.interval):
            try:
                self.store.update(heartbeat_at=time.time())
            except Exception:
                self.failed.set()
                return

    def __enter__(self):
        self.store.update(state='starting', reason_code='initializing', heartbeat_at=time.time())
        self.thread = threading.Thread(target=self._beat, name='notron-heartbeat', daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self.closed:
            return
        self.stop.set()
        self.thread.join(timeout=3)
        self.store.update(state='stopped', reason_code=None)
        self.closed = True
