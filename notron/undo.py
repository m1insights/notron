"""Encrypted revision-bound undo, with a staged preimage for uncertain writes.

Note IDs remain the top-level keys so ignore/delete retention also removes staged
backups. Legacy bodies are recovery-only: there is no trustworthy post revision.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from uuid import uuid4

from .paths import DATA_DIR
STATE = DATA_DIR / 'undo.json'


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    before_html: str
    after_revision: str | None
    operation_id: str | None


def _load() -> dict:
    from .securestore import read_json, write_json, IntegrityError
    from . import policy
    data = read_json(STATE)
    try:
        for nid, value in data.items():
            if not isinstance(nid, str) or not nid:
                raise ValueError()
            if isinstance(value, str):
                continue
            if not isinstance(value, dict) or set(value) != {'current', 'pending'}:
                raise ValueError()
            for snapshot in value.values():
                if snapshot is None:
                    continue
                s = Snapshot(**snapshot)
                if (not isinstance(s.snapshot_id, str) or not s.snapshot_id
                        or not isinstance(s.before_html, str)
                        or (s.after_revision is not None and (not isinstance(s.after_revision, str)
                            or len(s.after_revision) != 64))
                        or (s.operation_id is not None and not isinstance(s.operation_id, str))):
                    raise ValueError()
    except (ValueError, TypeError):
        raise IntegrityError('Encrypted undo schema invalid; processing paused.') from None
    safe = {nid: value for nid, value in data.items() if policy.current().can_read(nid)}
    if safe != data:
        write_json(STATE, safe)
    return safe


def _write(data: dict) -> None:
    from .securestore import write_json
    write_json(STATE, data)


def _entry(data, note_id):
    value = data.get(note_id)
    if isinstance(value, str):
        # Deterministic identity survives reads without modifying a dry-run store.
        digest = sha256((note_id + '\0' + value).encode()).hexdigest()
        sid = '-'.join(digest[i:i+8] for i in range(0, len(digest), 8))
        return {'current': asdict(Snapshot(sid, value, None, None)), 'pending': None}
    return value or {'current': None, 'pending': None}


def peek(note_id: str) -> Snapshot | None:
    entry = _entry(_load(), note_id)
    value = entry['pending'] or entry['current']
    if value is None:
        return None
    snapshot = Snapshot(**value)
    # An unknown external effect is never evidence authorizing overwrite.
    return replace(snapshot, after_revision=None) if entry['pending'] else snapshot


def save(note_id: str, old_body: str, after_revision=None, operation_id=None) -> None:
    from . import policy
    from .executor import write_transaction
    with write_transaction():
        if not policy.current().can_read(note_id):
            raise policy.PolicyError('Backup target is no longer readable.')
        data = _load()
        entry = _entry(data, note_id)
        if entry['pending']:
            pending = Snapshot(**entry['pending'])
            if (pending.operation_id, pending.before_html, pending.after_revision) == (operation_id, old_body, after_revision):
                return
            raise ValueError('Unresolved backup requires review before another write.')
        snapshot = asdict(Snapshot(str(uuid4()), old_body, after_revision, operation_id))
        entry['pending' if operation_id else 'current'] = snapshot
        data[note_id] = entry
        _write(data)


def _settle(note_id, operation_id, promote):
    from .executor import write_transaction
    with write_transaction():
        data = _load()
        entry = _entry(data, note_id)
        if entry['pending'] and entry['pending']['operation_id'] == operation_id:
            if promote:
                entry['current'] = entry['pending']
            entry['pending'] = None
            if entry['current']:
                data[note_id] = entry
            else:
                data.pop(note_id, None)
            _write(data)


def promote(note_id, operation_id):
    """Called only after the executor has verified the operation's external body."""
    _settle(note_id, operation_id, True)


def discard(note_id, operation_id):
    """Drop a staged backup only when the external write was never attempted."""
    _settle(note_id, operation_id, False)


def consume(note_id: str, snapshot_id: str) -> None:
    from .executor import write_transaction
    with write_transaction():
        data = _load()
        entry = _entry(data, note_id)
        # A delayed receipt cannot delete a newer pending or committed snapshot.
        if not entry['pending'] and entry['current'] and entry['current']['snapshot_id'] == snapshot_id:
            data.pop(note_id, None)
            _write(data)


def restore_body(snapshot: Snapshot, title: str, folder: str, receipt=False) -> str:
    """The only allowed restore transformation is our fixed receipt/tick policy."""
    from . import conversation, markup, notedoc, workspace
    body = snapshot.before_html
    if not receipt:
        return body
    if folder != workspace.FOLDER:
        marks = [(line.strip().removeprefix('• '), q.after, ' → put back')
                 for q in conversation.unanswered(body, ignore=(title,), require_tag=True)
                 for line in q.text.split('\n') if conversation.TAG.search(line)]
        body, _ = notedoc.mark_lines(body, marks)
    return body + markup.to_html(conversation.turn('Done — put it back the way it was.'))


def copy_command(snapshot):
    return 'undo recovery copy ' + snapshot.snapshot_id


def copy_title(title, snapshot):
    return title + ' — Recovery ' + snapshot.snapshot_id[:12]


def copy_markdown(snapshot):
    from . import markup, notedoc
    # Every recovered line is historical, including tags exposed only after
    # Markdown rendering and tags inside mixed list/multiline blocks. Mark each
    # nonblank line so the scanner cannot reactivate it under a fresh note ID.
    # Keep the readable words and existing tag spelling for the user's reference.
    lines = markup.to_text(snapshot.before_html).split('\n')
    inactive = '\n'.join(notedoc.FILED + line if line.strip() else line for line in lines)
    return 'Recovered history — ✓ marks inactive copied text.\n\n' + inactive


ASK_REPLY = ("Tag me with @notron undo on the note you want put back — "
             "from here I can't tell which one.")
COPY_REPLY = "Done — created a separate recovery copy."


def offer_text(snapshot):
    if snapshot is None:
        return 'Nothing to undo here.'
    return ("This note has changed since my write. I can create a separate plain-text "
            "recovery copy of the saved version. To confirm, type @notron "
            + copy_command(snapshot) + ".")
