import json

import pytest

from notron import migration
from notron.securestore import EncryptedStore, StorageError


def media_source(tmp_path):
    source = tmp_path / 'legacy'
    media = source / 'attachments'
    media.mkdir(parents=True)
    (media / 'att-1.png').write_bytes(b'private-photo')
    (media / 'att-1.png.txt').write_text('private-transcript')
    # Files named .json are original attachment bytes, not migration schemas.
    (media / 'att-2.json').write_bytes(b'original-non-json')
    return source, tmp_path / 'target', bytes(range(32))


def test_media_originals_and_words_are_encrypted_until_explicit_acceptance(tmp_path):
    source, target, key = media_source(tmp_path)
    result = migration.migrate(source, target, key)
    assert result['status'] == 'awaiting_acceptance'
    assert (source / 'attachments/att-1.png').read_bytes() == b'private-photo'
    recovery = EncryptedStore(target / 'migration-backup', key)
    for name in result['files']:
        if name.startswith('attachments/'):
            assert recovery.read(migration._backup_name(name)) == (source / name).read_bytes()
    assert all(b'private' not in p.read_bytes() for p in target.rglob('*.enc'))
    assert json.loads(EncryptedStore(target, key).read('attachments')) == {}
    migration.accept(source, target, key)
    assert not (source / 'attachments').exists()
    assert not list((target / 'migration-backup').iterdir())


def test_media_acceptance_rejects_new_files_without_removing_originals(tmp_path):
    source, target, key = media_source(tmp_path)
    migration.migrate(source, target, key)
    (source / 'attachments/new.png').write_bytes(b'new-photo')
    with pytest.raises(migration.MigrationError):
        migration.accept(source, target, key)
    assert (source / 'attachments/att-1.png').exists()
    assert (source / 'attachments/new.png').exists()


def test_media_acceptance_resumes_after_partial_unlink(tmp_path, monkeypatch):
    source, target, key = media_source(tmp_path)
    migration.migrate(source, target, key)
    unlink = migration.durable_unlink
    def interrupted(path):
        if path == source / 'attachments/att-1.png.txt':
            raise OSError('synthetic interruption')
        unlink(path)
    monkeypatch.setattr(migration, 'durable_unlink', interrupted)
    with pytest.raises(OSError):
        migration.accept(source, target, key)
    monkeypatch.setattr(migration, 'durable_unlink', unlink)
    migration.accept(source, target, key)
    assert not (source / 'attachments').exists()


def test_media_symlink_is_never_followed(tmp_path):
    source, target, key = media_source(tmp_path)
    outside = tmp_path / 'outside'
    outside.write_bytes(b'sensitive')
    (source / 'attachments/link').symlink_to(outside)
    with pytest.raises(StorageError):
        migration.migrate(source, target, key)
    assert not target.exists()


def test_empty_legacy_media_directory_can_be_migrated_and_accepted(tmp_path):
    source, target = tmp_path / 'legacy', tmp_path / 'target'
    (source / 'attachments').mkdir(parents=True)
    key = bytes(range(32))
    migration.migrate(source, target, key)
    migration.accept(source, target, key)
    assert not (source / 'attachments').exists()
