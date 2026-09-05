import json
import pytest


def legacy(root, suffix=''):
    import numpy as np
    root.mkdir()
    payload = {'n1': [dict(note_id='n1', title='Ordinary', folder='Notes', modified='date', text='password: synthetic-secret', row=0)]}
    (root / ('index.json' + suffix)).write_text(json.dumps(payload))
    with (root / ('vectors.npy' + suffix)).open('wb') as f:
        np.save(f, np.array([[1., 0.]], dtype='float32'))
    (root / 'undo.json').write_text(json.dumps({'n1': '<div>synthetic original</div>'}))


@pytest.mark.parametrize('suffix', ['', '.unprepared'])
def test_offline_migration_validates_preserves_backup_and_does_not_reuse_raw_vectors(tmp_path, suffix):
    from notron.migration import migrate, accept
    from notron.securestore import EncryptedStore
    source = tmp_path / 'legacy'
    legacy(source, suffix)
    target = tmp_path / 'target'
    report = migrate(source, target, bytes(range(32)))
    assert report['status'] == 'awaiting_acceptance'
    assert (source / ('index.json' + suffix)).exists()
    store = EncryptedStore(target, bytes(range(32)))
    index = json.loads(store.read('index'))
    assert index['notes'] == {}  # rebuild from selected Notes, never stale raw chunks
    assert json.loads(store.read('undo'))['n1'] == '<div>synthetic original</div>'
    backup = target / 'migration-backup' / ('index.json' + suffix + '.enc')
    assert EncryptedStore(backup.parent, bytes(range(32))).read('index.json' + suffix) == (source / ('index.json' + suffix)).read_bytes()
    assert b'synthetic-secret' not in backup.read_bytes()
    accept(source, target, bytes(range(32)))
    assert not (source / ('index.json' + suffix)).exists()
    assert not backup.exists()  # explicit acceptance retires recovery content


def test_interrupted_migration_is_resumable_and_originals_survive(tmp_path, monkeypatch):
    from notron.migration import migrate
    from notron.securestore import EncryptedStore
    source = tmp_path / 'legacy'
    legacy(source)
    target = tmp_path / 'target'
    original = EncryptedStore.write
    def fail(self, name, data):
        if name == 'undo': raise OSError('synthetic interruption')
        return original(self, name, data)
    monkeypatch.setattr(EncryptedStore, 'write', fail)
    with pytest.raises(OSError): migrate(source, target, bytes(range(32)))
    assert (source / 'undo.json').exists()
    assert not (target / 'migration.json').exists()
    monkeypatch.setattr(EncryptedStore, 'write', original)
    assert migrate(source, target, bytes(range(32)))['status'] == 'awaiting_acceptance'


def test_invalid_legacy_does_not_publish_migration(tmp_path):
    from notron.migration import migrate, MigrationError
    source = tmp_path / 'legacy'
    legacy(source)
    (source / 'undo.json').write_text('{')
    with pytest.raises(MigrationError): migrate(source, tmp_path / 'target', bytes(range(32)))
    assert (source / 'undo.json').read_text() == '{'
    assert not (tmp_path / 'target' / 'migration.json').exists()


def test_accept_refuses_changed_source_and_corrupt_backup(tmp_path):
    from notron.migration import migrate, accept, MigrationError
    source = tmp_path / 'legacy'
    legacy(source)
    target = tmp_path / 'target'
    key = bytes(range(32))
    migrate(source, target, key)
    original = (source / 'undo.json').read_bytes()
    (source / 'undo.json').write_text('{}')
    with pytest.raises(MigrationError): accept(source, target, key)
    assert (source / 'index.json').exists()
    (source / 'undo.json').write_bytes(original)
    (target / 'migration-backup' / 'undo.json.enc').write_bytes(b'corrupt')
    from notron.securestore import StorageError
    with pytest.raises(StorageError): accept(source, target, key)
    assert (source / 'undo.json').exists()


def test_migrated_store_cannot_process_before_acceptance(tmp_path, monkeypatch, outbound_transport):
    from notron import index, undo, filer, reflect
    from notron.migration import migrate
    from notron.securestore import StorageError
    from notron.outbound import Passage
    source = tmp_path / 'legacy'
    legacy(source)
    target = tmp_path / 'target'
    migrate(source, target, bytes(range(32)))
    for module, attr, filename in ((index, 'CACHE', 'index.json'), (undo, 'STATE', 'undo.json'),
                                   (filer, 'STATE', 'filer.json'), (reflect, 'STATE', 'reflect.json')):
        monkeypatch.setattr(module, attr, target / filename)
    brain, calls = outbound_transport
    with pytest.raises(StorageError): brain.embed([Passage('synthetic', 'user_request')])
    assert calls.embed == []


def test_deleted_note_without_index_or_undo_clears_reflection(monkeypatch):
    from notron import retention, notes, reflect
    from notron.notes import Note
    monkeypatch.setattr(notes, 'list_all_notes', lambda: [Note('n1', 'Ordinary', 'Notes', '')])
    retention.reconcile()
    reflect._record({}, 'digest', {'lessons': ['synthetic private']}, False)
    monkeypatch.setattr(notes, 'list_all_notes', lambda: [])
    retention.reconcile()
    assert reflect._state() == {}


def test_resume_after_manifest_written_finishes_journal(tmp_path, monkeypatch):
    from notron import migration
    source = tmp_path / 'legacy'
    legacy(source)
    target = tmp_path / 'target'
    write = migration.atomic_write_json
    def interrupted(path, data): raise OSError('synthetic interruption')
    monkeypatch.setattr(migration, 'atomic_write_json', interrupted)
    with pytest.raises(OSError): migration.migrate(source, target, bytes(range(32)))
    monkeypatch.setattr(migration, 'atomic_write_json', write)
    migration.migrate(source, target, bytes(range(32)))
    assert not (target / 'migration-incomplete.enc').exists()
    migration.accept(source, target, bytes(range(32)))


def test_missing_sidecar_does_not_bypass_migration_acceptance(tmp_path, monkeypatch, outbound_transport):
    from notron import migration, index
    from notron.securestore import StorageError
    from notron.outbound import Passage
    source = tmp_path / 'legacy'
    legacy(source)
    target = tmp_path / 'target'
    migration.migrate(source, target, bytes(range(32)))
    (target / 'migration.json').unlink()
    monkeypatch.setattr(index, 'CACHE', target / 'index.json')
    brain, calls = outbound_transport
    with pytest.raises(StorageError): brain.embed([Passage('synthetic', 'user_request')])
    assert calls.embed == []


def test_direct_payload_api_cannot_consume_unaccepted_migration(tmp_path):
    from notron import migration
    from notron.securestore import read_json, StorageError
    source = tmp_path / 'legacy'
    legacy(source)
    target = tmp_path / 'target'
    migration.migrate(source, target, bytes(range(32)))
    with pytest.raises(StorageError): read_json(target / 'undo.json')


def test_acceptance_interruption_preserves_recovery_until_cleanup_finishes(tmp_path, monkeypatch):
    from pathlib import Path
    from notron import migration
    from notron.securestore import read_json, StorageError
    source = tmp_path / 'legacy'
    legacy(source)
    target = tmp_path / 'target'
    migration.migrate(source, target, bytes(range(32)))
    unlink = Path.unlink
    def interrupted(path, *args, **kwargs):
        if path.parent.name == 'migration-backup': raise OSError('synthetic interruption')
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', interrupted)
    with pytest.raises(OSError): migration.accept(source, target, bytes(range(32)))
    with pytest.raises(StorageError): read_json(target / 'undo.json')
    monkeypatch.setattr(Path, 'unlink', unlink)
    migration.accept(source, target, bytes(range(32)))
    assert read_json(target / 'undo.json')['n1'] == '<div>synthetic original</div>'


def test_explicit_initialize_then_migrate_then_accept(tmp_path, _task3_storage):
    from notron import credentials, cli
    from types import SimpleNamespace
    source = tmp_path / 'legacy'
    legacy(source)
    target = tmp_path / 'target'
    _task3_storage.delete(credentials.STORAGE_KEY)
    args = SimpleNamespace(action='initialize', source=str(source), target=str(target))
    cli.cmd_storage(args)
    args.action = 'migrate'
    cli.cmd_storage(args)
    args.action = 'accept'
    cli.cmd_storage(args)
    assert (target / 'undo.enc').exists()
    assert not (source / 'undo.json').exists()


def test_corrupt_active_output_during_acceptance_never_retires_backup(tmp_path, monkeypatch):
    from pathlib import Path
    from notron import migration
    from notron.securestore import StorageError
    source = tmp_path / 'legacy'
    legacy(source)
    target = tmp_path / 'target'
    key = bytes(range(32))
    migration.migrate(source, target, key)
    unlink = Path.unlink
    def interrupted(path, *args, **kwargs):
        if path.parent.name == 'migration-backup': raise OSError('synthetic interruption')
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', interrupted)
    with pytest.raises(OSError): migration.accept(source, target, key)
    monkeypatch.setattr(Path, 'unlink', unlink)
    (target / 'undo.enc').write_bytes(b'corrupt')
    with pytest.raises(StorageError): migration.accept(source, target, key)
    assert (target / 'migration-backup' / 'undo.json.enc').exists()
