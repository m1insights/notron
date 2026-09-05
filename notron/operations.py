"""Durable local intent metadata; sensitive payloads live in the encrypted store.

SQLite commits precede external effects. This ledger cannot make Apple writes
transactional: APPLYING is uncertain until an adapter establishes the outcome.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from .securestore import EncryptedStore, StorageError, private_directory
from .paths import DATA_DIR

PATH = DATA_DIR / 'operations.sqlite3'
SCHEMA_VERSION = 1


class OperationConflict(RuntimeError):
    pass


class OperationStatus(StrEnum):
    PREPARED = 'prepared'
    APPLYING = 'applying'
    APPLIED = 'applied'
    RECEIPTED = 'receipted'
    NEEDS_REVIEW = 'needs_review'
    CANCELLED = 'cancelled'


S = OperationStatus
_EDGES = {
    S.PREPARED: {S.APPLYING, S.CANCELLED, S.NEEDS_REVIEW},
    S.APPLYING: {S.APPLIED, S.NEEDS_REVIEW},
    S.APPLIED: {S.RECEIPTED, S.NEEDS_REVIEW},
    # Review may establish a known outcome, never authorize another blind save.
    S.NEEDS_REVIEW: {S.APPLIED, S.CANCELLED},
    S.RECEIPTED: set(), S.CANCELLED: set(),
}
FAILURE_CODES = {'source_changed', 'source_missing', 'ambiguous_occurrence',
                 'interrupted', 'write_failed', 'policy_changed', 'unknown_outcome',
                 'payload_missing', 'revision_conflict', 'post_write_divergence'}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def identity(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512 or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid operation identity.')
    return value


@dataclass(frozen=True)
class Operation:
    request_id: str
    operation_id: str
    payload_hash: str
    payload_ref: str | None
    status: OperationStatus
    created_at: str
    updated_at: str
    source_id: str | None
    target_id: str | None
    expected_revision: str | None
    external_id: str | None
    failure_code: str | None


class OperationStore:
    def __init__(self, path: Path, payload_store: EncryptedStore):
        self.path, self.payload_store = Path(path), payload_store
        private_directory(self.path.parent)
        self._safe_paths()
        marker = 'ledger-history-' + sha256(str(self.path.absolute()).encode()).hexdigest()
        marker_path = self.payload_store.root / (marker + '.enc')
        history_exists = marker_path.exists() or any(self.payload_store.root.glob('operation-*.enc'))
        if (not self.path.exists() or self.path.stat().st_size == 0) and history_exists:
            raise StorageError('Operation history exists but its ledger is missing; explicit recovery is required.')
        if marker_path.exists() and self.payload_store.read(marker) != b'notron-operation-ledger-v1':
            raise StorageError('Operation history marker is invalid; explicit recovery is required.')
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with self.connection() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise StorageError('Operation schema requires explicit migration.')
            if version == 0:
                if history_exists:
                    raise StorageError('Operation history exists without a valid ledger; explicit recovery is required.')
                if db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                    raise StorageError('Unrecognized operation database; processing paused.')
                db.executescript('''
                    BEGIN IMMEDIATE;
                    CREATE TABLE requests (
                        request_id TEXT PRIMARY KEY,
                        payload_hash TEXT,
                        payload_ref TEXT,
                        note_id TEXT,
                        source_revision TEXT,
                        status TEXT NOT NULL DEFAULT 'prepared' CHECK(status IN
                            ('prepared','running','completed','needs_review','cancelled')),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        failure_code TEXT
                    );
                    CREATE TABLE operations (
                        operation_id TEXT PRIMARY KEY,
                        request_id TEXT NOT NULL REFERENCES requests(request_id),
                        payload_hash TEXT NOT NULL,
                        payload_ref TEXT,
                        status TEXT NOT NULL CHECK(status IN
                            ('prepared','applying','applied','receipted','needs_review','cancelled')),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        source_id TEXT,
                        target_id TEXT,
                        expected_revision TEXT,
                        external_id TEXT,
                        failure_code TEXT
                    );
                    CREATE INDEX operations_request ON operations(request_id);
                    CREATE INDEX requests_note ON requests(note_id);
                    CREATE TABLE observations (
                        note_id TEXT NOT NULL,
                        source TEXT NOT NULL,
                        revision TEXT NOT NULL,
                        payload_ref TEXT,
                        PRIMARY KEY(note_id, source)
                    );
                    PRAGMA user_version=1;
                    COMMIT;
                ''')
        if not marker_path.exists():
            self.payload_store.write(marker, b'notron-operation-ledger-v1')
        # Persist directory entries as well as the SQLite transaction contents.
        fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _safe_paths(self):
        if any(Path(str(self.path) + suffix).is_symlink() for suffix in ('', '-wal', '-shm')):
            raise StorageError('Unsafe operation database path.')

    @contextmanager
    def connection(self):
        self._safe_paths()
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def put_payload(self, data: bytes) -> str:
        # Immutable references: replacing a payload before SQL commit would make
        # readers of the previous transaction observe uncommitted content.
        ref = 'operation-' + uuid4().hex
        self.payload_store.write(ref, data)
        return ref

    @staticmethod
    def _record(row) -> Operation | None:
        if row is None:
            return None
        values = dict(row)
        values['status'] = S(values['status'])
        return Operation(**values)

    def prepare(self, request_id: str, operation_id: str, payload_hash: str, *,
                payload: bytes | None = None, source_id: str | None = None,
                target_id: str | None = None, expected_revision: str | None = None) -> Operation:
        identity(request_id), identity(operation_id), identity(payload_hash)
        if payload is not None and sha256(payload).hexdigest() != payload_hash:
            raise OperationConflict('Operation payload does not match its digest.')
        with self.transaction() as db:
            row = db.execute('SELECT * FROM operations WHERE operation_id=?', (operation_id,)).fetchone()
            if row:
                if (row['request_id'], row['payload_hash'], row['source_id'], row['target_id'], row['expected_revision']) != (
                        request_id, payload_hash, source_id, target_id, expected_revision):
                    raise OperationConflict('Operation identity already belongs to different work.')
                if payload is not None and (not row['payload_ref'] or self.payload_store.read(row['payload_ref']) != payload):
                    raise OperationConflict('Operation payload cannot be replaced.')
                return self._record(row)
            ref = self.put_payload(payload) if payload is not None else None
            stamp = now()
            db.execute('INSERT OR IGNORE INTO requests(request_id,created_at,updated_at) VALUES(?,?,?)',
                       (request_id, stamp, stamp))
            db.execute('''INSERT INTO operations(operation_id,request_id,payload_hash,payload_ref,status,
                       created_at,updated_at,source_id,target_id,expected_revision)
                       VALUES(?,?,?,?,?,?,?,?,?,?)''',
                       (operation_id, request_id, payload_hash, ref, S.PREPARED, stamp, stamp,
                        source_id, target_id, expected_revision))
            return self._record(db.execute('SELECT * FROM operations WHERE operation_id=?', (operation_id,)).fetchone())

    def transition(self, operation_id: str, expected: S, target: S, external_id: str | None = None,
                   *, failure_code: str | None = None) -> Operation:
        expected, target = S(expected), S(target)
        if target not in _EDGES[expected]:
            raise OperationConflict('Invalid operation transition.')
        if failure_code is not None and failure_code not in FAILURE_CODES:
            raise ValueError('Unknown operation failure code.')
        with self.transaction() as db:
            row = db.execute('SELECT * FROM operations WHERE operation_id=?', (operation_id,)).fetchone()
            if not row or row['status'] != expected:
                raise OperationConflict('Operation state changed; processing paused.')
            if row['external_id'] and external_id not in (None, row['external_id']):
                raise OperationConflict('External identity cannot be replaced.')
            db.execute('UPDATE operations SET status=?,updated_at=?,external_id=COALESCE(?,external_id),failure_code=? '
                       'WHERE operation_id=? AND status=?',
                       (target, now(), external_id, failure_code, operation_id, expected))
            return self._record(db.execute('SELECT * FROM operations WHERE operation_id=?', (operation_id,)).fetchone())

    def get(self, operation_id: str) -> Operation | None:
        with self.connection() as db:
            return self._record(db.execute('SELECT * FROM operations WHERE operation_id=?', (operation_id,)).fetchone())

    def pending(self) -> list[Operation]:
        with self.connection() as db:
            return [self._record(r) for r in db.execute(
                "SELECT * FROM operations WHERE status NOT IN ('receipted','cancelled') ORDER BY created_at,operation_id")]

    def payload(self, operation_id: str) -> bytes | None:
        record = self.get(operation_id)
        if record is None or record.payload_ref is None:
            return None
        payload = self.payload_store.read(record.payload_ref)
        if sha256(payload).hexdigest() != record.payload_hash:
            raise StorageError('Operation payload digest mismatch.')
        return payload


def current() -> OperationStore:
    from .securestore import store_for
    return OperationStore(PATH, store_for(PATH))
