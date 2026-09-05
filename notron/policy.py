"""Validated, fail-closed note policy and ephemeral watcher authorization.

Capabilities live only in the current Python call context, never in model
output or persisted JSON. P02 will supply durable request identity.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping
from uuid import uuid4

from .persistence import atomic_write_bytes, atomic_write_json


class PolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class PolicySnapshot:
    status: Literal['unconfigured', 'ready', 'corrupt']
    homes: frozenset[str] = frozenset()
    ignore: frozenset[str] = frozenset()
    decided: frozenset[str] = frozenset()
    chosen_at: str = ''
    start_from: datetime | None = None
    allow_new_notes: bool = False
    system_notes: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def can_read(self, note_id: str) -> bool:
        return bool(self.status == 'ready' and note_id and note_id not in self.ignore
                    and (note_id in self.homes or note_id in self.decided
                         or note_id in self.system_notes.values() or self.allow_new_notes))

    def can_file(self, note_id: str) -> bool:
        return self.can_read(note_id) and note_id in self.homes

    def can_reply(self, note_id: str, explicit_request_id: str | None) -> bool:
        cap = _reply.get()
        return bool(self.can_read(note_id) and cap and not cap.used
                    and cap.note_id == note_id and cap.request_id == explicit_request_id)

    def readable(self, note) -> bool:
        from . import privacy
        if not self.can_read(note.id) or privacy.is_vault(note.title) or privacy.is_private(note.title):
            return False
        if note.id in self.homes | self.decided or note.id in self.system_notes.values():
            return True
        # Unknown dates cannot defeat a configured cutoff.
        return self.start_from is None or (note.modified_at is not None
                                          and note.modified_at >= self.start_from)

    def system_role(self, note_id: str) -> str | None:
        return next((role for role, nid in self.system_notes.items() if nid == note_id), None)


def decode_policy(raw) -> PolicySnapshot:
    from .library import parse_start
    from . import workspace
    if not isinstance(raw, dict):
        raise ValueError('policy must be an object')
    if 'version' in raw and (type(raw['version']) is not int or raw['version'] != 1):
        raise ValueError('unsupported policy version')
    for key in ('homes', 'ignore', 'decided'):
        value = raw.get(key)
        if not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value):
            raise ValueError('invalid note IDs')
    chosen = raw.get('chosen_at')
    if not isinstance(chosen, str):
        raise ValueError('invalid setup timestamp')
    if chosen:
        datetime.fromisoformat(chosen)
    if type(raw.get('allow_new_notes', False)) is not bool:
        raise ValueError('invalid new-note setting')
    start = raw.get('start_from')
    if start is not None and not isinstance(start, str):
        raise ValueError('invalid cutoff')
    system = raw.get('system_notes', {})
    if (not isinstance(system, dict)
            or any(k not in workspace.SYSTEM_NOTES or not isinstance(v, str) or not v
                   for k, v in system.items())
            or len(set(system.values())) != len(system)):
        raise ValueError('invalid system note IDs')
    # An empty file written by setup carries system IDs, but no user selection.
    ready = bool(chosen or raw['homes'] or raw['ignore'] or raw['decided'])
    return PolicySnapshot(
        status='ready' if ready else 'unconfigured',
        homes=frozenset(raw['homes']), ignore=frozenset(raw['ignore']),
        decided=frozenset(raw['decided']), chosen_at=chosen,
        start_from=parse_start(start) if start else None,
        allow_new_notes=raw.get('allow_new_notes', False),
        system_notes=MappingProxyType(dict(system)))


def load_policy(path: Path) -> PolicySnapshot:
    try:
        raw = json.loads(path.read_text())
        return decode_policy(raw)
    except FileNotFoundError:
        return PolicySnapshot('unconfigured')
    except (OSError, ValueError, TypeError, OverflowError):
        return PolicySnapshot('corrupt')


def current() -> PolicySnapshot:
    from . import library
    return load_policy(library.STATE)


def require_ready() -> PolicySnapshot:
    snapshot = current()
    if snapshot.status != 'ready':
        raise PolicyError(f'Note policy {snapshot.status}; AI paused. Run library setup or explicit recovery.')
    return snapshot


def save_policy(path: Path, payload, *, reset: bool = False) -> None:
    decode_policy(payload)
    previous = load_policy(path)
    if previous.status == 'corrupt' and not reset:
        raise PolicyError('Policy corrupt; explicitly recover or reset before changing permissions.')
    if previous.status == 'corrupt':
        atomic_write_bytes(path.with_name(path.name + '.corrupt'), path.read_bytes())
    if previous.status == 'ready':
        # Validate the exact bytes copied, not a second potentially changed read.
        data = path.read_bytes()
        decode_policy(json.loads(data))
        atomic_write_bytes(path.with_name(path.name + '.bak'), data)
    atomic_write_json(path, payload)


def restore_policy(path: Path) -> None:
    """Explicit recovery only; the caller must visibly report the restored policy."""
    backup = path.with_name(path.name + '.bak')
    try:
        data = backup.read_bytes()
        if decode_policy(json.loads(data)).status != 'ready':
            raise ValueError('backup is not configured')
    except (OSError, ValueError, TypeError) as exc:
        raise PolicyError('No validated policy backup available.') from exc
    if path.exists():
        atomic_write_bytes(path.with_name(path.name + '.corrupt'), path.read_bytes())
    atomic_write_bytes(path, data)


@dataclass
class _ReplyCapability:
    note_id: str
    request_id: str
    used: bool = False


_reply: ContextVar[_ReplyCapability | None] = ContextVar('notron_explicit_reply', default=None)


@contextmanager
def explicit_reply(note_id: str):
    """Called by the watcher after observing a request in an approved note."""
    if not require_ready().can_read(note_id):
        raise PolicyError('Request note is not readable.')
    cap = _ReplyCapability(note_id, uuid4().hex)
    token = _reply.set(cap)
    try:
        yield cap.request_id
    finally:
        _reply.reset(token)


def request_id() -> str | None:
    cap = _reply.get()
    return cap.request_id if cap else None


def consume_reply() -> None:
    cap = _reply.get()
    if cap:
        cap.used = True


def can_mark_source(note_id: str) -> bool:
    cap = _reply.get()
    return bool(cap and cap.note_id == note_id and current().can_read(note_id))


def request_note_id() -> str | None:
    cap = _reply.get()
    return cap.note_id if cap else None
