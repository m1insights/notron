"""Credential injection. No environment/file fallback and no secret diagnostics."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import threading
import time
from typing import Protocol

SERVICE = 'com.m1labs.notron'
STORAGE_KEY = 'storage-key'
NEBIUS_KEY = 'nebius-api-key'
SEARCH_KEY = 'tavily-api-key'
DEV_NEBIUS_KEY = 'development-nebius-api-key'
NAMES = frozenset({STORAGE_KEY, NEBIUS_KEY, SEARCH_KEY, DEV_NEBIUS_KEY})
#: A token an MCP server needs (`connectors.py`), stored per server and variable.
#: A pattern rather than a list because the user names the servers, but a
#: narrow one: no dot in the server part, so `connector.a.b.C` cannot address
#: another server's item, and nothing it matches collides with a fixed name.
#: `fullmatch`, not `match` with `$`, which would also accept a trailing newline.
CONNECTOR_SECRET = re.compile(r"connector\.[A-Za-z0-9-]{1,40}\.[A-Z][A-Z0-9_]{0,63}")


def _known(name) -> bool:
    return isinstance(name, str) and (name in NAMES or CONNECTOR_SECRET.fullmatch(name) is not None)


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
    # Every encrypted read asks for the storage key, and each ask spawns the
    # helper: measured 2026-09-23, 89 helper processes in one idle listener
    # tick, 2.15 s of its 3.6 s. A key is held for TTL seconds, so removing or
    # rotating it in Keychain still pauses processing within half a minute; a
    # missing answer is never held, so a key set a moment ago is seen at once.
    TTL = 30.0

    def __init__(self, helper: Path):
        self.helper = Path(helper)
        self._held: dict[str, tuple[bytes, float]] = {}
        self._generation = 0
        self._lock = threading.Lock()

    def _request(self, operation: str, name: str, value: bytes | None = None):
        if not _known(name):
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
        with self._lock:
            held = self._held.get(name)
            if held is not None and time.monotonic() - held[1] < self.TTL:
                return held[0]
            generation = self._generation
        value = self._request('get', name)
        with self._lock:
            if generation != self._generation:
                return value  # a put/delete overlapped this read: never hold it
            if value is None:
                self._held.pop(name, None)
            else:
                self._held[name] = (value, time.monotonic())
        return value

    def forget(self) -> None:
        with self._lock:
            self._held.clear()
            self._generation += 1

    def put(self, name: str, value: bytes) -> None:
        self.forget()
        try:
            self._request('put', name, value)
        finally:
            self.forget()

    def delete(self, name: str) -> None:
        self.forget()
        try:
            self._request('delete', name)
        finally:
            self.forget()


_provider: CredentialStore | None = None


def configure(provider: CredentialStore | None) -> None:
    """Inject once at startup; tests supply an in-memory implementation."""
    global _provider
    _provider = provider


def startup() -> None:
    """Inject the verified Keychain helper.

    Replaces the P06 placeholder gate, which raised unconditionally because there
    was nothing to point at. The helper now exists and ships inside the app
    bundle; `bundle.py` locates that bundle and verifies its identity and
    signature before returning a path. There is still no environment, file or
    model-supplied override for the helper path, and a bundle that is missing,
    unsigned or signed by another team leaves the provider unset so every
    protected command stays paused.
    """
    from . import bundle
    try:
        helper = bundle.keychain_helper()
    except bundle.BundleUnavailable:
        configure(None)
        raise CredentialUnavailable(
            'Secure startup requires a validly signed Notron bundle.') from None
    configure(KeychainStore(helper))


def get(name: str) -> bytes | None:
    if not _known(name) or _provider is None:
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
    from .health import control_artifacts
    # Name what is in the way. The refusal itself is correct -- a lost key must
    # never be silently replaced -- but "requires an empty destination" leaves a
    # user staring at a directory with no idea which of its files is the problem,
    # and no way to tell an orphaned leftover from real data.
    blocking = sorted({p.name for p in root.iterdir()} - control_artifacts(root)) if root.exists() else []
    if _provider is None or get(STORAGE_KEY) is not None or blocking:
        detail = f' Blocking files: {", ".join(blocking)}.' if blocking else ''
        raise CredentialUnavailable(
            'Storage setup requires an empty destination and no existing key.' + detail)
    key = os.urandom(32)
    _provider.put(STORAGE_KEY, key)
    if require(STORAGE_KEY) != key:
        raise CredentialUnavailable('Storage key could not be verified.')
    EncryptedStore(root, key).write('key-check', b'notron-storage-v1')

#: Credential names a user may set from the command line. `storage-key` is
#: provisioned by `storage initialize` against an empty destination, where it is
#: generated rather than supplied; `managed-refresh` is native-only by design and
#: the helper refuses it. Neither belongs in a general "store a key" command, and
#: allowing them would quietly widen a deliberate boundary.
PROVISIONABLE = (NEBIUS_KEY, SEARCH_KEY, DEV_NEBIUS_KEY)


def _provisionable(name) -> bool:
    # Connector secrets are user-supplied tokens, like the API keys above; the
    # pattern cannot match `storage-key` or `managed-refresh`.
    return name in PROVISIONABLE or (isinstance(name, str)
                                     and CONNECTOR_SECRET.fullmatch(name) is not None)

#: A pasted key is one line. Anything longer or multi-line is a paste accident,
#: and storing it writes a credential that fails much later, somewhere far from
#: the cause -- the failure mode this whole module exists to avoid.
MAX_SECRET_BYTES = 4096


def provision_api_key(name: str, secret: str) -> None:
    """Store one API key, then read it back.

    The value never appears in an argument, a log line, or a diagnostic: callers
    pass text they read from stdin, and this function is the only path to
    `put` for anything other than the storage key.
    """
    if not _provisionable(name):
        raise CredentialUnavailable('That credential cannot be set here.')
    if _provider is None:
        raise CredentialUnavailable('Keychain unavailable; protected processing paused.')
    if not isinstance(secret, str):
        raise CredentialUnavailable('Secret must be text.')
    value = secret.strip()
    if (not value or len(value.encode('utf-8')) > MAX_SECRET_BYTES
            or any(character.isspace() for character in value)):
        raise CredentialUnavailable('That does not look like a single-line key.')
    encoded = value.encode('utf-8')
    _provider.put(name, encoded)
    if get(name) != encoded:
        raise CredentialUnavailable('Stored key could not be verified.')


def provisioned() -> list[tuple[str, bool]]:
    """Which provisionable names are present. Presence only, never a value.

    A Keychain failure propagates rather than reporting `False`: "absent" and
    "unreadable" are different problems and only one of them is fixed by pasting
    a key.
    """
    return [(name, get(name) is not None) for name in PROVISIONABLE]


def forget_api_key(name: str) -> None:
    if not _provisionable(name):
        raise CredentialUnavailable('That credential cannot be removed here.')
    if _provider is None:
        raise CredentialUnavailable('Keychain unavailable; protected processing paused.')
    _provider.delete(name)


#: An OAuth sign-in (tokens or a client registration, base64 JSON) is written
#: by code, not pasted, and a long-lived token pair can pass the paste limit.
MAX_TOKEN_BYTES = 16384


def store_connector_token(name: str, value: bytes) -> None:
    """Store a connector's OAuth state, written by `mcp_client.TokenStore`.

    Only `connector.<server>.OAUTH_*` names: the general secrets above stay
    paste-only, and this never writes a key a person typed.
    """
    if (CONNECTOR_SECRET.fullmatch(name or '') is None
            or not name.rsplit('.', 1)[-1].startswith('OAUTH_')):
        raise CredentialUnavailable('That credential cannot be set here.')
    if _provider is None:
        raise CredentialUnavailable('Keychain unavailable; protected processing paused.')
    if not isinstance(value, bytes) or not value or len(value) > MAX_TOKEN_BYTES:
        raise CredentialUnavailable('Sign-in token could not be stored.')
    _provider.put(name, value)
