"""Credential injection. No environment/file fallback and no secret diagnostics."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import socket
import subprocess
from typing import Protocol

SERVICE = 'com.m1labs.notron'
STORAGE_KEY = 'storage-key'
NEBIUS_KEY = 'nebius-api-key'
SEARCH_KEY = 'tavily-api-key'
NAMES = frozenset({STORAGE_KEY, NEBIUS_KEY, SEARCH_KEY})


class CredentialUnavailable(RuntimeError):
    pass


class CredentialStore(Protocol):
    def get(self, name: str) -> bytes | None: ...
    def put(self, name: str, value: bytes) -> None: ...
    def delete(self, name: str) -> None: ...


class KeychainStore:
    """One request per helper process over an inherited, dedicated socket pipe.

    The helper path must come from signed bundle integration, never model input
    or an environment override. P06 owns that integration and identity check.
    stdout/stderr are discarded; failures have fixed, payload-free messages.
    """
    def __init__(self, helper: Path):
        self.helper = Path(helper)

    def _request(self, operation: str, name: str, value: bytes | None = None):
        if name not in NAMES:
            raise CredentialUnavailable('Unknown credential name.')
        request = {'operation': operation, 'name': name}
        if value is not None:
            request['value'] = base64.b64encode(value).decode('ascii')
        parent, child = socket.socketpair()
        process = None
        try:
            parent.settimeout(10)
            process = subprocess.Popen(
                [str(self.helper), '--credential-fd', str(child.fileno())],
                pass_fds=(child.fileno(),), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env={'PATH': '/usr/bin:/bin'})
            child.close()
            parent.sendall(json.dumps(request).encode() + b'\n')
            parent.shutdown(socket.SHUT_WR)
            data = bytearray()
            while block := parent.recv(4096):
                data.extend(block)
                if len(data) > 65536:
                    raise ValueError()
            process.wait(timeout=10)
            reply = json.loads(data)
            if process.returncode != 0 or reply.get('status') not in ('ok', 'missing'):
                raise ValueError()
            if operation == 'get':
                return None if reply['status'] == 'missing' else base64.b64decode(reply['value'], validate=True)
            if reply['status'] != 'ok':
                raise ValueError()
        except Exception:
            raise CredentialUnavailable('Keychain unavailable; protected processing paused.') from None
        finally:
            parent.close()
            child.close()
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()

    def get(self, name: str) -> bytes | None:
        return self._request('get', name)

    def put(self, name: str, value: bytes) -> None:
        self._request('put', name, value)

    def delete(self, name: str) -> None:
        self._request('delete', name)


_provider: CredentialStore | None = None


def configure(provider: CredentialStore | None) -> None:
    """Inject once at startup; tests supply an in-memory implementation."""
    global _provider
    _provider = provider


def startup() -> None:
    # Deliberate native release gate, not an environment-controlled opt-out.
    configure(None)
    raise CredentialUnavailable('Secure startup requires the signed Keychain integration in P06.')


def get(name: str) -> bytes | None:
    if name not in NAMES or _provider is None:
        raise CredentialUnavailable('Keychain unavailable; protected processing paused.')
    try:
        value = _provider.get(name)
        if value is not None and (not isinstance(value, bytes) or not value):
            raise ValueError()
        return value
    except Exception:
        raise CredentialUnavailable('Keychain unavailable; protected processing paused.') from None


def require(name: str) -> bytes:
    value = get(name)
    if value is None:
        raise CredentialUnavailable('Required credential missing; protected processing paused.')
    return value


def storage_key() -> bytes:
    key = require(STORAGE_KEY)
    if len(key) != 32:
        raise CredentialUnavailable('Storage key invalid; protected processing paused.')
    return key


def provision_storage_key(root: Path) -> None:
    """Explicit fresh setup only. A lost key must never be silently replaced."""
    from .securestore import EncryptedStore
    if _provider is None or get(STORAGE_KEY) is not None or (root.exists() and any(root.iterdir())):
        raise CredentialUnavailable('Storage setup requires an empty destination and no existing key.')
    key = os.urandom(32)
    _provider.put(STORAGE_KEY, key)
    if require(STORAGE_KEY) != key:
        raise CredentialUnavailable('Storage key could not be verified.')
    EncryptedStore(root, key).write('key-check', b'notron-storage-v1')
