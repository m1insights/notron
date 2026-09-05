"""Locally authoritative provenance checks at every model/search boundary.

Passages are data, never permission grants. Only application code assigns origin;
model output cannot construct a policy, reply capability, or operation.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, Sequence, get_args

from . import policy, privacy, workspace
from .policy import PolicyError, PolicySnapshot

Purpose = Literal['route', 'write', 'schedule', 'organize', 'reflect', 'embed', 'search']
Origin = Literal['user_request', 'note', 'standing', 'memory', 'lesson', 'web',
                 'history', 'agenda', 'model', 'diagnostic']
NOTE_ORIGINS = {'note', 'standing', 'memory', 'lesson', 'history'}
SYSTEM_ROLES = {'standing': workspace.ABOUT, 'memory': workspace.MEMORY,
                'lesson': workspace.LESSONS}


@dataclass(frozen=True)
class Passage:
    text: str
    origin: Origin
    note_id: str | None = None
    title: str = ''
    modified: str = ''

    @classmethod
    def from_note(cls, text: str, note, origin: Origin = 'note') -> Passage:
        return cls(text, origin, note.id, note.title, note.modified)


def prepare_outbound(purpose: Purpose, passages: Sequence[Passage]) -> list[str]:
    """Validate the entire batch before transport; never echo rejected content."""
    from .notes import Note

    snapshot: PolicySnapshot = policy.require_ready()
    if not isinstance(purpose, str) or purpose not in get_args(Purpose):
        raise PolicyError('Unknown outbound purpose.')
    if not isinstance(passages, (list, tuple)):
        raise PolicyError('Outbound input requires tagged passages.')
    safe = []
    for p in passages:
        if (not isinstance(p, Passage) or not isinstance(p.text, str)
                or not isinstance(p.origin, str) or p.origin not in get_args(Origin)
                or not isinstance(p.title, str) or not isinstance(p.modified, str)
                or (p.note_id is not None and (not isinstance(p.note_id, str) or not p.note_id))):
            raise PolicyError('Invalid outbound provenance.')
        if p.origin in NOTE_ORIGINS and not p.note_id:
            raise PolicyError('Note-derived passage requires a note ID.')
        if p.note_id:
            if not snapshot.readable(Note(p.note_id, p.title, '', p.modified)):
                raise PolicyError('Outbound note is not readable.')
            role = SYSTEM_ROLES.get(p.origin)
            if role and snapshot.system_role(p.note_id) != role:
                raise PolicyError('Standing context requires its registered system note.')
        safe.append(privacy.redact(p.text))
    return safe


def sanitized(purpose: Purpose, passages: Sequence[Passage]) -> list[Passage]:
    """For local truncation/formatting/storage while retaining source metadata."""
    return [replace(p, text=text, title=privacy.redact(p.title))
            for p, text in zip(passages, prepare_outbound(purpose, passages))]
