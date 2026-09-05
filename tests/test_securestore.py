"""Synthetic storage regressions; never use the user's cache or Keychain."""
import json
import os
import stat

import pytest

KEY = bytes(range(32))


def test_payload_not_plaintext_and_tampering_fails(tmp_path):
    from notron.securestore import EncryptedStore, IntegrityError
    store = EncryptedStore(tmp_path, KEY)
    store.write('undo', b'synthetic-private-note')
    p = tmp_path / 'undo.enc'
    raw = p.read_bytes()
    assert b'synthetic-private-note' not in raw
    p.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises(IntegrityError, match='integrity'):
        store.read('undo')


def test_fresh_nonces_filename_schema_and_key_are_authenticated(tmp_path):
    from notron.securestore import EncryptedStore, IntegrityError
    store = EncryptedStore(tmp_path, KEY)
    store.write('undo', b'synthetic')
    first = (tmp_path / 'undo.enc').read_bytes()
    store.write('undo', b'synthetic')
    assert first != (tmp_path / 'undo.enc').read_bytes()
    (tmp_path / 'other.enc').write_bytes(first)
    with pytest.raises(IntegrityError):
        store.read('other')
    with pytest.raises(IntegrityError):
        EncryptedStore(tmp_path, b'x' * 32).read('undo')
    changed = bytearray(first)
    changed[0] ^= 1
    (tmp_path / 'undo.enc').write_bytes(changed)
    with pytest.raises(IntegrityError):
        store.read('undo')


def test_private_modes_and_reject_paths_symlinks(tmp_path):
    from notron.securestore import EncryptedStore, StorageError
    root = tmp_path / 'store'
    root.mkdir(mode=0o755)
    store = EncryptedStore(root, KEY)
    store.write('undo', b'synthetic')
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / 'undo.enc').stat().st_mode) == 0o600
    for name in ('../escape', '/absolute', 'nested/name', ''):
        with pytest.raises(StorageError):
            store.write(name, b'x')
    (root / 'link.enc').symlink_to(root / 'undo.enc')
    with pytest.raises(StorageError):
        store.read('link')


def test_interrupted_encrypted_write_keeps_previous(tmp_path, monkeypatch):
    from notron.securestore import EncryptedStore
    from notron import persistence
    store = EncryptedStore(tmp_path, KEY)
    store.write('undo', b'old')
    def interrupt(*args):
        raise OSError('synthetic interruption')
    monkeypatch.setattr(persistence.os, 'replace', interrupt)
    with pytest.raises(OSError):
        store.write('undo', b'new')
    assert store.read('undo') == b'old'


def test_undo_and_request_state_are_encrypted(tmp_path):
    from notron import undo, filer, reflect
    undo.save('n1', '<div>synthetic-private-original</div>')
    filer._save({'judged': {}, 'proposals': {'synthetic-private-request': {'items': []}}, 'shapes': {}}, dry_run=False)
    reflect._record({}, 'digest', {'lessons': ['synthetic-private-lesson']}, False)
    assert undo.peek('n1').before_html == '<div>synthetic-private-original</div>'
    for path in tmp_path.glob('*'):
        if path.is_file():
            assert b'synthetic-private' not in path.read_bytes()
    assert not undo.STATE.exists()
    assert not filer.STATE.exists()
    assert not reflect.STATE.exists()


def test_ignore_purges_index_undo_and_request_caches(outbound_policy):
    from notron import index, undo, filer, reflect
    row = dict(note_id='approved', title='Ordinary', folder='Notes', modified='', text='synthetic', vector=[1., 0.])
    index._save({'approved': [row]})
    undo.save('approved', 'synthetic original')
    filer._save({'judged': {}, 'proposals': {'synthetic': {'items': []}}, 'shapes': {}}, dry_run=False)
    reflect._record({}, 'digest', {'lessons': ['synthetic']}, False)
    outbound_policy(ignored=('approved',))
    assert index._load() == {}
    assert undo.peek('approved') is None
    assert filer._state()['proposals'] == {}
    assert reflect._state() == {}


def test_deleted_cleanup_removes_durable_content(monkeypatch, outbound_policy):
    from notron import index, undo, retention, notes
    index._save({'approved': [dict(note_id='approved', title='Ordinary', folder='Notes', modified='', text='synthetic', vector=[1., 0.])]})
    undo.save('approved', 'synthetic')
    monkeypatch.setattr(notes, 'list_all_notes', lambda: [])
    retention.reconcile()
    assert index._load() == {}
    assert undo.peek('approved') is None


def test_diagnostics_exclude_content_and_expire_after_seven_days(tmp_path):
    from notron.diagnostics import record, prune
    from datetime import datetime, timezone, timedelta
    now = datetime(2026, 9, 4, tzinfo=timezone.utc)
    record('processing_paused', root=tmp_path, now=now - timedelta(days=8))
    record('processing_paused', root=tmp_path, now=now)
    with pytest.raises(ValueError):
        record('password: synthetic-private', root=tmp_path, now=now)
    prune(root=tmp_path, now=now)
    files = list(tmp_path.glob('*.json'))
    assert len(files) == 1
    assert 'synthetic-private' not in files[0].read_text()


def test_corrupt_encrypted_index_schema_pauses_without_disclosure(outbound_transport):
    from notron import index
    from notron.securestore import store_for, IntegrityError
    store_for(index.CACHE).write('index', json.dumps({'outbound_version': 1, 'notes': {'n1': 'synthetic-private'}}).encode())
    with pytest.raises(IntegrityError) as error:
        index._load()
    assert 'synthetic-private' not in str(error.value)


def test_search_drops_deleted_note_before_embedding(monkeypatch, outbound_transport):
    from notron import index, notes
    from notron.outbound import Passage
    index._save({'n1': [dict(note_id='n1', title='Ordinary', folder='Notes', modified='', text='synthetic-deleted', vector=[1., 0.])]})
    monkeypatch.setattr(notes, 'list_all_notes', lambda: [])
    brain, calls = outbound_transport
    assert index.search([Passage('synthetic', 'user_request')], brain) == []
    assert not calls.embed
    assert index._load() == {}


def test_atomic_metadata_hardens_existing_parent_permissions(tmp_path):
    from notron.persistence import atomic_write_json
    root = tmp_path / 'state'
    root.mkdir(mode=0o755)
    atomic_write_json(root / 'metadata.json', {'count': 1})
    assert stat.S_IMODE(root.stat().st_mode) == 0o700


def test_usage_retention_keeps_only_seven_days_and_numeric_metadata(tmp_path):
    from notron import diagnostics
    from datetime import date
    path = tmp_path / 'usage.json'
    path.write_text(json.dumps({'2026-01-01': {'fast': {'calls': 1}},
                               '2026-09-04': {'fast': {'calls': 2, 'in': 3, 'out': 4, 'secret': 'synthetic'},
                                              'synthetic-private': {'calls': 1}}}))
    diagnostics.prune_usage(path, today=date(2026, 9, 4))
    assert json.loads(path.read_text()) == {'2026-09-04': {'fast': {'calls': 2, 'in': 3, 'out': 4}}}


def test_current_sensitive_title_purges_previously_ordinary_cached_note(monkeypatch):
    from notron import index, undo, retention, notes
    index._save({'n1': [dict(note_id='n1', title='Ordinary', folder='Notes', modified='', text='synthetic', vector=[1., 0.])]})
    undo.save('n1', 'synthetic original')
    monkeypatch.setattr(notes, 'list_all_notes', lambda: [notes.Note('n1', 'Passwords', 'Notes', '')])
    retention.reconcile()
    assert index._load() == {}
    assert undo.peek('n1') is None


def test_listener_releases_ignored_in_memory_requests(monkeypatch):
    from notron import watch, notes
    from types import SimpleNamespace
    listener = watch.Watcher(None, scanner=SimpleNamespace(scan=lambda: []))
    listener._pending = {'tag:n1': ('synthetic-private', 0), 'ask:synthetic': ('synthetic-private', 0)}
    listener._failures = {'tag:n1': (1, 0)}
    listener._ask_id = 'n1'
    monkeypatch.setattr(notes, 'list_all_notes', lambda: [])
    listener.sweep_mentions()
    assert listener._pending == {}
    assert listener._failures == {}
    assert listener._ask_id is None


def test_encrypted_index_roundtrip_reuses_vectors_and_searches_in_memory(monkeypatch, outbound_transport):
    from notron import index, notes, library
    from notron.outbound import Passage
    import numpy as np
    note = notes.Note('n1', 'Ordinary', 'Notes', '2026-09-04')
    monkeypatch.setattr(notes, 'list_all_notes', lambda: [note])
    monkeypatch.setattr(library, 'user_notes', lambda: [note])
    monkeypatch.setattr(notes, 'read_body', lambda _: '<div>synthetic password: synthetic-value</div>')
    monkeypatch.setattr(np, 'load', lambda *a, **kw: pytest.fail('persistent NumPy loading is forbidden'))
    brain, calls = outbound_transport
    assert index.build(brain)['embedded'] == 1
    assert index.build(brain)['reused'] == 1
    assert len(calls.embed) == 1
    hits = index.search([Passage('synthetic', 'user_request')], brain)
    assert [hit.note_id for hit in hits] == ['n1']
    assert 'synthetic-value' not in json.dumps(index._load())
    assert not index.CACHE.exists() and not index.VECTORS.exists()
    assert (index.CACHE.with_suffix('.enc')).exists()
