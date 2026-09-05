"""Authenticated local payloads. Plaintext exists only in process memory."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import persistence

HEADER = b'NOTRON-AESGCM\x01'


class StorageError(RuntimeError):
    pass


class IntegrityError(StorageError):
    pass


def private_directory(root: Path) -> None:
    if root.is_symlink() or any(p.is_symlink() for p in root.parents):
        raise StorageError('Unsafe storage directory.')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)


class EncryptedStore:
    def __init__(self, root: Path, key: bytes):
        self.root = Path(root)
        if not isinstance(key, bytes) or len(key) != 32:
            raise StorageError('Invalid storage key.')
        self._cipher = AESGCM(key)

    def _path(self, name: str) -> Path:
        if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', name):
            raise StorageError('Invalid payload name.')
        private_directory(self.root)
        path = self.root / (name + '.enc')
        if path.is_symlink():
            raise StorageError('Unsafe payload path.')
        return path

    def write(self, name: str, data: bytes) -> None:
        path = self._path(name)
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, data, HEADER + name.encode('utf-8'))
        persistence.atomic_write_bytes(path, HEADER + nonce + ciphertext)

    def read(self, name: str) -> bytes:
        path = self._path(name)
        try:
            raw = path.read_bytes()
            os.chmod(path, 0o600)
            if not raw.startswith(HEADER) or len(raw) < len(HEADER) + 28:
                raise InvalidTag()
            at = len(HEADER)
            return self._cipher.decrypt(raw[at:at + 12], raw[at + 12:], HEADER + name.encode('utf-8'))
        except (InvalidTag, ValueError):
            raise IntegrityError('Encrypted payload integrity check failed; processing paused.') from None


def store_for(path: Path) -> EncryptedStore:
    from . import credentials
    store = EncryptedStore(path.parent, credentials.storage_key())
    verify_generation(store)
    return store



def verify_generation(store: EncryptedStore) -> None:
    root = store.root
    if (root / 'migration-incomplete.enc').exists():
        raise StorageError('Interrupted migration requires explicit recovery.')
    if (root / 'migration.json').exists() or (root / 'migration-manifest.enc').exists():
        try:
            manifest = json.loads(store.read('migration-manifest'))
            if manifest.get('version') != 1 or manifest.get('status') != 'accepted':
                raise ValueError()
            if any((root / 'migration-backup').glob('*.enc')):
                raise ValueError()
        except (ValueError, TypeError, AttributeError, FileNotFoundError):
            raise IntegrityError('Migration requires explicit acceptance or recovery before processing.') from None
    if (root / 'key-check.enc').exists() and store.read('key-check') != b'notron-storage-v1':
        raise IntegrityError('Storage key validation failed; processing paused.')

def reject_legacy(path: Path) -> None:
    if path.exists() or path.with_name(path.name + '.unprepared').exists():
        raise StorageError('Legacy content requires explicit offline migration and acceptance.')


def read_json(path: Path) -> dict:
    store = store_for(path)
    reject_legacy(path)
    try:
        data = json.loads(store.read(path.stem))
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except FileNotFoundError:
        return {}
    except (ValueError, UnicodeError):
        raise IntegrityError('Encrypted payload schema invalid; processing paused.') from None


def write_json(path: Path, data: dict) -> None:
    store = store_for(path)
    reject_legacy(path)
    store.write(path.stem, json.dumps(data, allow_nan=False).encode())
