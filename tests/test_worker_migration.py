import json

import pytest

from notron import mentions, notes, requests


def test_legacy_pending_tag_becomes_review_not_fresh_work(monkeypatch):
    note = notes.Note('n1', 'Ideas', 'Notes', 'today')
    raw = json.dumps({'seen': {'n1': 'yesterday'}, 'pending': ['n1']})
    mentions.STATE.write_text(raw)
    monkeypatch.setattr(notes, 'list_all_notes', lambda: [note])
    monkeypatch.setattr(notes, 'read_body', lambda nid: '<div>Ideas</div><div>#notron remind me to call</div>')
    scanner = mentions.Scanner()
    scanner.prime()
    found = scanner.scan()
    assert requests.current().get(found[0].envelope.request_id).status == 'needs_review'
    from notron.securestore import EncryptedStore
    from notron import credentials
    backup = EncryptedStore(mentions.STATE.parent / 'worker-backup', credentials.storage_key())
    assert backup.read('seen-v1') == raw.encode()
    restarted = mentions.Scanner()
    restarted.prime()
    assert restarted.scan()[0].envelope.request_id == found[0].envelope.request_id


def test_legacy_scanner_keeps_existing_durable_pending_identity(monkeypatch):
    note = notes.Note('n1', 'Ideas', 'Notes', 'today')
    body = '<div>Ideas</div><div>#notron hello</div>'
    from notron.conversation import unanswered
    original = requests.current().observe('n1', body, unanswered(body, ignore=('Ideas',), require_tag=True),
                                          source='mention', title='Ideas', folder='Notes')[0]
    mentions.STATE.write_text(json.dumps({'seen': {'n1': 'yesterday'}, 'pending': ['n1']}))
    monkeypatch.setattr(notes, 'list_all_notes', lambda: [note])
    monkeypatch.setattr(notes, 'read_body', lambda nid: body)
    scanner = mentions.Scanner()
    scanner.prime()
    assert scanner.scan()[0].envelope.request_id == original.request_id
    assert requests.current().get(original.request_id).status == 'prepared'


def test_corrupt_seen_state_does_not_reset_the_scanner():
    mentions.STATE.write_text('{broken')
    from notron.securestore import StorageError
    with pytest.raises(StorageError):
        mentions.Scanner().prime()
    assert mentions.STATE.read_text() == '{broken'


def test_offline_migration_carries_unknown_filing_effects_into_review(tmp_path, monkeypatch):
    from notron import migration, filer, worker_migration
    from notron.securestore import StorageError
    source, target = tmp_path / 'old', tmp_path / 'new'
    source.mkdir()
    (source / 'filer.json').write_text(json.dumps({'judged': {'opaque': {'kind': 'note', 'title': 'Old'}}}))
    (source / 'seen.json').write_text(json.dumps({'seen': {'n1': 'old'}, 'pending': ['n1']}))
    migration.migrate(source, target, bytes(range(32)))
    assert (target / 'migration-backup' / 'filer.json.enc').exists()
    migration.accept(source, target, bytes(range(32)))
    monkeypatch.setattr(filer, 'STATE', target / 'filer.json')
    monkeypatch.setattr(mentions, 'STATE', target / 'seen.json')
    with pytest.raises(StorageError):
        worker_migration.require_filing_ready()
    scanner = mentions.Scanner()
    scanner.prime()
    assert scanner.pending == {'n1'}
    assert scanner.review == {'n1'}


def test_existing_filer_cache_is_backed_up_before_review_gate(monkeypatch):
    from notron import filer, worker_migration, credentials
    from notron.health import WorkerLock
    from notron.securestore import EncryptedStore, write_json, StorageError
    old = {'judged': {'opaque': {'kind': 'note', 'title': 'Old'}}}
    write_json(filer.STATE, old)
    with WorkerLock():
        worker_migration.migrate_filer()
        worker_migration.migrate_filer()  # Restart does not clear review.
    backup = EncryptedStore(filer.STATE.parent / 'worker-backup', credentials.storage_key())
    assert json.loads(backup.read('filer-v1')) == old
    with pytest.raises(StorageError):
        filer._state()


def test_legacy_observation_commits_review_before_scanner_can_crash(monkeypatch):
    body = '<div>Ideas</div><div>#notron hello</div>'
    mentions.STATE.write_text(json.dumps({'seen': {'n1': 'old'}, 'pending': ['n1']}))
    monkeypatch.setattr(notes, 'list_all_notes', lambda: [notes.Note('n1', 'Ideas', 'Notes', 'new')])
    monkeypatch.setattr(notes, 'read_body', lambda nid: body)
    scanner = mentions.Scanner()
    scanner.prime()
    original = requests.RequestStore.observe
    def crash_after_observe(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        assert self.get(result[0].request_id).status == 'needs_review'
        raise RuntimeError('crash after occurrence commit')
    monkeypatch.setattr(requests.RequestStore, 'observe', crash_after_observe)
    with pytest.raises(RuntimeError):
        scanner.scan()


def test_failed_body_observation_is_retried_even_after_restart(monkeypatch):
    mentions.STATE.write_text(json.dumps({'version': 2, 'seen': {'n1': 'old'}, 'pending': [], 'review': []}))
    monkeypatch.setattr(notes, 'list_all_notes', lambda: [notes.Note('n1', 'Ideas', 'Notes', 'new')])
    monkeypatch.setattr(notes, 'read_body', lambda nid: '<div>Ideas</div><div>#notron hello</div>')
    scanner = mentions.Scanner()
    scanner.prime()
    original = requests.RequestStore.observe
    monkeypatch.setattr(requests.RequestStore, 'observe', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('ledger unavailable')))
    with pytest.raises(RuntimeError):
        scanner.scan()
    monkeypatch.setattr(requests.RequestStore, 'observe', original)
    restarted = mentions.Scanner()
    restarted.prime()
    assert restarted.scan(), 'changed-note checkpoint must not hide uncommitted requests'


def test_policy_revocation_retires_raw_filing_backup_but_keeps_review_gate():
    from notron import filer, worker_migration, library
    from notron.health import WorkerLock
    from notron.securestore import write_json, StorageError
    write_json(filer.STATE, {'judged': {'opaque': {'kind': 'new', 'title': 'Private title'}}})
    with WorkerLock():
        worker_migration.migrate_filer()
    assert (filer.STATE.parent / 'worker-backup' / 'filer-v1.enc').exists()
    library.save(library.Library(decided={'n1'}))
    assert not (filer.STATE.parent / 'worker-backup' / 'filer-v1.enc').exists()
    with pytest.raises(StorageError):
        worker_migration.require_filing_ready()


def test_legacy_ask_without_receipt_cannot_replay_an_unknown_effect(monkeypatch):
    from notron import watch, workspace
    ask_id = f'{workspace.FOLDER}/{workspace.ASK}'
    mentions.STATE.write_text(json.dumps({'seen': {ask_id: 'old'}, 'pending': []}))
    monkeypatch.setattr(notes, 'read_body', lambda nid: f'<div>{workspace.ASK}</div><div>remind me to call</div>')
    scanner = mentions.Scanner()
    scanner.prime()
    watcher = watch.Watcher(brain=None, scanner=scanner, settle=0)
    watcher.check_ask()
    watcher.check_ask()
    assert requests.current().pending()[0].status == 'needs_review'


def test_status_and_pause_do_not_block_fresh_storage_initialization(_task3_storage):
    from notron import operations, credentials
    from notron.health import HealthStore
    _task3_storage.delete(credentials.STORAGE_KEY)
    store = HealthStore()
    store.set_paused(True)
    store.status()
    credentials.provision_storage_key(operations.PATH.parent)
    assert HealthStore().paused
    assert credentials.storage_key()


def test_control_files_do_not_block_offline_migration(tmp_path):
    from notron import migration, operations
    from notron.health import HealthStore
    source = tmp_path / 'legacy'
    source.mkdir()
    (source / 'seen.json').write_text(json.dumps({'seen': {'n1': 'old'}, 'pending': ['n1']}))
    store = HealthStore()
    store.set_paused(True)
    store.status()
    migration.migrate(source, operations.PATH.parent, bytes(range(32)))
    assert HealthStore().paused


def test_source_deletion_purges_raw_worker_backup(monkeypatch):
    from notron import filer, retention, worker_migration
    from notron.health import WorkerLock
    from notron.securestore import write_json
    monkeypatch.setattr(notes, 'list_all_notes', lambda: [notes.Note('n1', 'Ideas', 'Notes', 'today')])
    retention.reconcile()
    write_json(filer.STATE, {'judged': {'opaque': {'kind': 'new', 'title': 'Private'}}})
    with WorkerLock():
        worker_migration.migrate_filer()
    monkeypatch.setattr(notes, 'list_all_notes', lambda: [])
    retention.reconcile()
    assert not (filer.STATE.parent / 'worker-backup' / 'filer-v1.enc').exists()
