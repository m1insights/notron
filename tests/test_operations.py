"""Durable state transitions use real SQLite and encrypted temporary payloads."""
import sqlite3

import pytest

from notron.operations import OperationConflict, OperationStatus as S, OperationStore
from notron.securestore import EncryptedStore


@pytest.fixture
def payload_store(tmp_path):
    return EncryptedStore(tmp_path / 'payloads', bytes(range(32)))


def test_operation_identity_cannot_be_reused_for_different_work(tmp_path, payload_store):
    ledger = OperationStore(tmp_path / 'ops.sqlite3', payload_store)
    ledger.prepare('r1', 'r1:action:0', 'hash-a')
    assert ledger.prepare('r1', 'r1:action:0', 'hash-a').status == S.PREPARED
    with pytest.raises(OperationConflict):
        ledger.prepare('r1', 'r1:action:0', 'hash-b')
    with pytest.raises(OperationConflict):
        ledger.prepare('r2', 'r1:action:0', 'hash-a')


def test_reopen_preserves_applied_and_external_id(tmp_path, payload_store):
    path = tmp_path / 'ops.sqlite3'
    ledger = OperationStore(path, payload_store)
    ledger.prepare('r1', 'op1', 'hash')
    ledger.transition('op1', S.PREPARED, S.APPLYING)
    ledger.transition('op1', S.APPLYING, S.APPLIED, external_id='apple-id')
    reopened = OperationStore(path, payload_store)
    assert reopened.get('op1').status == S.APPLIED
    assert reopened.get('op1').external_id == 'apple-id'
    assert [op.operation_id for op in reopened.pending()] == ['op1']
    reopened.transition('op1', S.APPLIED, S.RECEIPTED)
    assert reopened.pending() == []


def test_compare_and_swap_and_forbidden_edges_cannot_replay(tmp_path, payload_store):
    ledger = OperationStore(tmp_path / 'ops.sqlite3', payload_store)
    ledger.prepare('r', 'op', 'hash')
    with pytest.raises(OperationConflict):
        ledger.transition('op', S.PREPARED, S.APPLIED)
    ledger.transition('op', S.PREPARED, S.APPLYING)
    with pytest.raises(OperationConflict):
        ledger.transition('op', S.PREPARED, S.CANCELLED)
    ledger.transition('op', S.APPLYING, S.NEEDS_REVIEW)
    with pytest.raises(OperationConflict):
        ledger.transition('op', S.NEEDS_REVIEW, S.APPLYING)
    assert ledger.get('op').status == S.NEEDS_REVIEW


def test_payloads_are_encrypted_and_target_metadata_is_durable(tmp_path, payload_store):
    from hashlib import sha256
    body = b'synthetic confidential note body'
    path = tmp_path / 'ops.sqlite3'
    ledger = OperationStore(path, payload_store)
    row = ledger.prepare('r', 'op', sha256(body).hexdigest(), payload=body,
                         source_id='source', target_id='target', expected_revision='revision')
    assert ledger.payload('op') == body
    assert row.target_id == 'target' and row.source_id == 'source'
    assert row.expected_revision == 'revision' and row.payload_ref
    for file in tmp_path.rglob('*'):
        if file.is_file():
            assert body not in file.read_bytes()
    with sqlite3.connect(path) as db:
        assert db.execute('pragma journal_mode').fetchone()[0] == 'wal'
        assert db.execute('pragma foreign_key_check').fetchall() == []
    assert path.stat().st_mode & 0o777 == 0o600


def test_payload_failure_does_not_prepare_operation(tmp_path, payload_store, monkeypatch):
    ledger = OperationStore(tmp_path / 'ops.sqlite3', payload_store)
    monkeypatch.setattr(payload_store, 'write', lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    from hashlib import sha256
    with pytest.raises(OSError):
        ledger.prepare('r', 'op', sha256(b'body').hexdigest(), payload=b'body')
    assert ledger.get('op') is None


def test_symlink_ledger_and_unknown_schema_fail_closed(tmp_path, payload_store):
    from notron.securestore import StorageError
    target = tmp_path / 'target'
    target.write_text('unchanged')
    link = tmp_path / 'ops.sqlite3'
    link.symlink_to(target)
    with pytest.raises(StorageError):
        OperationStore(link, payload_store)
    assert target.read_text() == 'unchanged'
    link.unlink()
    with sqlite3.connect(link) as db:
        db.execute('pragma user_version=900')
    with pytest.raises(StorageError):
        OperationStore(link, payload_store)


def test_policy_change_purges_payload_for_standalone_operation():
    from hashlib import sha256
    from notron import operations, library
    ledger = operations.current()
    body = b'synthetic standalone payload'
    ledger.prepare('standalone-request', 'standalone-op', sha256(body).hexdigest(), payload=body, source_id='n1')
    lib = library.load()
    lib.ignore.add('n1')
    library.save(lib)
    assert ledger.payload('standalone-op') is None
    assert ledger.get('standalone-op').status == S.NEEDS_REVIEW


@pytest.mark.parametrize('loss', ['missing', 'empty'])
def test_missing_ledger_with_surviving_encrypted_history_fails_closed(loss):
    from notron import operations, requests
    from notron.securestore import StorageError
    store = requests.current()
    envelope = requests.create('synthetic request')
    store.capture(envelope)
    store.claim(envelope.request_id)
    artifacts = {path.name: path.read_bytes() for path in store.operations.payload_store.root.glob('*.enc')}
    if loss == 'missing':
        store.operations.path.unlink()
    else:
        store.operations.path.write_bytes(b'')
    with pytest.raises(StorageError):
        operations.current()
    assert store.operations.path.exists() == (loss == 'empty')
    assert {path.name: path.read_bytes() for path in store.operations.payload_store.root.glob('*.enc')} == artifacts


@pytest.mark.parametrize('loss', ['missing', 'empty'])
def test_missing_ledger_still_fails_after_content_was_purged(loss):
    from notron import operations, requests
    from notron.securestore import StorageError
    store = requests.current()
    envelope = requests.create('synthetic request')
    store.capture(envelope)
    store.purge_sources(all_content=True)
    if loss == 'missing':
        store.operations.path.unlink()
    else:
        store.operations.path.write_bytes(b'')
    assert list(store.operations.payload_store.root.glob('operation-*.enc')) == []
    with pytest.raises(StorageError):
        operations.current()


def test_v1_migration_preserves_operation_and_can_record_observation(tmp_path, payload_store):
    from notron.operations import OperationStore, S
    path = tmp_path / 'migrate.sqlite3'
    store = OperationStore(path, payload_store)
    store.prepare('request', 'operation', 'digest', target_id='nid', expected_revision='before')
    with store.connection() as db:
        columns = {row[1] for row in db.execute('PRAGMA table_info(operations)')}
        if 'observed_revision' in columns:
            db.execute('ALTER TABLE operations DROP COLUMN observed_revision')
        db.execute('PRAGMA user_version=1')
    reopened = OperationStore(path, payload_store)
    assert reopened.get('operation').expected_revision == 'before'
    reopened.transition('operation', S.PREPARED, S.APPLYING)
    result = reopened.transition('operation', S.APPLYING, S.APPLIED, observed_revision='after')
    assert result.observed_revision == 'after'
    assert OperationStore(path, payload_store).get('operation').observed_revision == 'after'
