"""Explicit offline import. No Apple, provider, or credential-file access.

Originals and private recovery copies survive until acceptance. Raw/prepared
index vectors are validated, then discarded from the active generation: only a
fresh, approved-note rebuild may embed content after migration.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import os

from . import policy
from .persistence import atomic_write_json, durable_unlink
from .securestore import EncryptedStore, StorageError, private_directory

FILES = ('index.json', 'vectors.npy', 'index.json.unprepared', 'vectors.npy.unprepared',
         'undo.json', 'filer.json', 'reflect.json', 'usage.json', 'seen.json',
         'mood.json', 'listen.log', 'morning.log')


class MigrationError(StorageError):
    pass


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _roots(source: Path, target: Path):
    source, target = Path(source).absolute(), Path(target).absolute()
    if source == target or source in target.parents or target in source.parents:
        raise MigrationError('Migration requires separate source and destination directories.')
    for path in (source, target):
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise MigrationError('Unsafe migration directory.')
    return source, target


def _validate(raw: dict[str, bytes]) -> dict:
    import numpy as np
    parsed = {}
    try:
        for name, content in raw.items():
            if '.json' in name:
                value = json.loads(content)
                if not isinstance(value, dict): raise ValueError()
                parsed[name] = value
        for suffix in ('', '.unprepared'):
            name = 'index.json' + suffix
            vector_name = 'vectors.npy' + suffix
            if vector_name in raw and name not in raw: raise ValueError()
            if name not in parsed: continue
            payload = parsed[name]
            if 'outbound_version' in payload:
                if payload['outbound_version'] != 1: raise ValueError()
                payload = payload['notes']
            if not isinstance(payload, dict): raise ValueError()
            vectors = None
            if vector_name in raw:
                vectors = np.load(io.BytesIO(raw[vector_name]), allow_pickle=False)
                if not isinstance(vectors, np.ndarray) or vectors.dtype.kind not in 'fi' or not np.isfinite(vectors).all():
                    raise ValueError()
                if vectors.size and vectors.ndim != 2: raise ValueError()
            for nid, rows in payload.items():
                if not isinstance(nid, str) or not nid or not isinstance(rows, list): raise ValueError()
                for row in rows:
                    if not isinstance(row, dict) or row.get('note_id') != nid: raise ValueError()
                    if not all(isinstance(row.get(k), str) for k in ('title', 'folder', 'modified', 'text')): raise ValueError()
                    slot = row.get('row')
                    if slot is not None and (type(slot) is not int or slot < 0 or vectors is None or slot >= len(vectors)):
                        raise ValueError()
        undo = parsed.get('undo.json', {})
        if not all(isinstance(nid, str) and nid and isinstance(body, str) for nid, body in undo.items()):
            raise ValueError()
        return {nid: body for nid, body in undo.items() if policy.current().can_read(nid)}
    except (ValueError, TypeError, KeyError, IndexError, UnicodeError):
        raise MigrationError('Legacy cache validation failed; originals preserved.') from None


def migrate(source: Path, target: Path, key: bytes) -> dict:
    source, target = _roots(source, target)
    policy.require_ready()
    raw = {}
    for name in FILES:
        path = source / name
        if path.is_symlink(): raise MigrationError('Unsafe legacy cache path.')
        if path.exists(): raw[name] = path.read_bytes()
    if not raw: raise MigrationError('No recognized legacy caches found.')
    undo = _validate(raw)  # validate everything before publishing any content
    hashes = {name: _digest(data) for name, data in raw.items()}
    private_directory(target)
    store = EncryptedStore(target, key)
    journal = target / 'migration-incomplete.enc'
    if (target / 'migration-manifest.enc').exists():
        manifest = json.loads(store.read('migration-manifest'))
        if manifest.get('files') != hashes or manifest.get('source') != str(source):
            raise MigrationError('Migration source changed; originals preserved.')
        if manifest['status'] == 'accepted':
            raise MigrationError('Migration already accepted.')
        for name, digest in manifest['outputs'].items():
            if _digest(store.read(name)) != digest:
                raise MigrationError('Interrupted migration output validation failed.')
        atomic_write_json(target / 'migration.json', {'version': 1, 'status': manifest['status']})
        durable_unlink(journal)
        return manifest
    if journal.exists():
        pending = json.loads(store.read('migration-incomplete'))
        if pending != {'files': hashes, 'source': str(source)}:
            raise MigrationError('Interrupted migration source changed; originals preserved.')
    else:
        names = {path.name for path in target.iterdir()}
        if names and (names != {'key-check.enc'} or store.read('key-check') != b'notron-storage-v1'):
            raise MigrationError('Migration destination must be empty or contain only its verified initialization marker.')
    store.write('migration-incomplete', json.dumps({'files': hashes, 'source': str(source)}).encode())
    backup = target / 'migration-backup'
    private_directory(backup)
    recovery = EncryptedStore(backup, key)
    for name, data in raw.items():
        path = backup / (name + ".enc")
        if path.exists():
            if path.is_symlink() or recovery.read(name) != data:
                raise MigrationError('Recovery backup differs; originals preserved.')
        else:
            recovery.write(name, data)
        os.chmod(source / name, 0o600)
    private_directory(source)
    payloads = {'index': {'outbound_version': 1, 'notes': {}}, 'undo': undo, 'filer': {}, 'reflect': {}}
    outputs = {}
    for name, value in payloads.items():
        data = json.dumps(value).encode()
        store.write(name, data)
        if store.read(name) != data: raise MigrationError('Migration round-trip validation failed.')
        outputs[name] = _digest(data)
    store.write('key-check', b'notron-storage-v1')
    manifest = {'version': 1, 'status': 'awaiting_acceptance', 'source': str(source), 'files': hashes, 'outputs': outputs}
    store.write('migration-manifest', json.dumps(manifest).encode())
    atomic_write_json(target / 'migration.json', {'version': 1, 'status': 'awaiting_acceptance'})
    durable_unlink(journal)
    return manifest


def accept(source: Path, target: Path, key: bytes) -> None:
    """Separate, explicit user acceptance; verifies backups before removing originals.

    Acceptance retires the encrypted backups. Interruption during acceptance is safe to
    resume; no automatic restoration or plaintext fallback is permitted.
    """
    source, target = _roots(source, target)
    store = EncryptedStore(target, key)
    manifest = json.loads(store.read('migration-manifest'))
    if manifest.get('source') != str(source) or manifest.get('status') not in ('awaiting_acceptance', 'accepting', 'accepted'):
        raise MigrationError('Migration is not awaiting acceptance.')
    if manifest['status'] == 'accepted':
        if any((target / 'migration-backup').glob('*.enc')):
            for name, digest in manifest['outputs'].items():
                if _digest(store.read(name)) != digest:
                    raise MigrationError('Migrated content changed; recovery copies preserved.')
        _finish_acceptance(target, manifest)
        return
    recovery = EncryptedStore(target / 'migration-backup', key)
    for name, digest in manifest['files'].items():
        if name not in FILES: raise MigrationError('Invalid migration inventory.')
        backup = target / 'migration-backup' / (name + '.enc')
        original = source / name
        if backup.is_symlink() or _digest(recovery.read(name)) != digest:
            raise MigrationError('Recovery backup validation failed.')
        if original.exists():
            if original.is_symlink() or _digest(original.read_bytes()) != digest:
                raise MigrationError('Legacy cache changed since migration; originals preserved.')
        elif manifest['status'] != 'accepting':
            raise MigrationError('Legacy cache disappeared before acceptance.')
    for name, digest in manifest['outputs'].items():
        if _digest(store.read(name)) != digest:
            raise MigrationError('Migrated content changed before acceptance.')
    manifest['status'] = 'accepting'
    store.write('migration-manifest', json.dumps(manifest).encode())
    for name in manifest['files']:
        durable_unlink(source / name)
    manifest['status'] = 'accepted'
    store.write('migration-manifest', json.dumps(manifest).encode())
    _finish_acceptance(target, manifest)


def _finish_acceptance(target: Path, manifest: dict) -> None:
    # Acceptance explicitly retires recovery content as well as plaintext originals.
    # The authenticated accepted marker makes interruption during cleanup resumable.
    for name in manifest['files']:
        if name not in FILES: raise MigrationError('Invalid migration inventory.')
        durable_unlink(target / 'migration-backup' / (name + '.enc'))
    durable_unlink(target / 'migration-incomplete.enc')
    atomic_write_json(target / 'migration.json', {'version': 1, 'status': 'accepted'})
