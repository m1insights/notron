"""Notron's Apple bridge, as plain functions an MCP client can call.

Plain dicts in and out, no SDK: `mcp_server` is a thin shell over these. Every
read leaves the Mac for whichever AI provider the client uses, so each one goes
through the same library and outbound checks a Nemotron call does, and no more
is returned than a model would have seen:

- an ignored, vault-titled or unknown note answers with the same words, so a
  client cannot probe which notes exist;
- 📌 About Me and the rest of her own notes are instructions, not data: never
  listed, searched or read, wherever the user has moved them;
- an attachment is never returned, and a note that holds one says so
  (invariant 12: never imply she has seen something she has not);
- a Calendar or Reminders read she is not allowed to make says "I cannot read
  your Calendar", never an empty free week.

Note reads need no API key. Only `ask` runs a model, through the normal graph,
so Nemotron still decides and the Guard still authorizes.
"""

from __future__ import annotations

from . import library, markup, notes, policy, workspace
from .outbound import Passage, prepare_outbound

MAX_LIMIT = 50
MAX_TEXT = 20_000
MAX_DAYS = 31
MORE = "\n[… more not shown]"
NOT_AVAILABLE = {"error": "not available"}


def _clamp(value, low: int, high: int) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = high
    return max(low, min(high, value))


def _system(note: notes.Note, snapshot: policy.PolicySnapshot) -> bool:
    """Her own notes, by any of the three things that can say so. A title
    alone grants nothing, but it is enough to withhold."""
    return (note.folder == workspace.FOLDER or note.title in workspace.SYSTEM_NOTES
            or snapshot.system_role(note.id) is not None)


def _visible(note: notes.Note, snapshot: policy.PolicySnapshot) -> bool:
    return (not _system(note, snapshot) and snapshot.readable(note)
            and library.state_of(note.id, note.modified) != library.IGNORE)


def _row(note: notes.Note) -> dict:
    from . import privacy
    return {"id": note.id, "title": privacy.redact(note.title), "folder": note.folder,
            "modified": note.modified}


def notes_list(limit: int = MAX_LIMIT) -> list[dict]:
    """The user's notes Notron may read: metadata only, no bodies."""
    snapshot = policy.require_ready()
    limit = _clamp(limit, 1, MAX_LIMIT)
    return [_row(n) for n in library.user_notes() if _visible(n, snapshot)][:limit]


def notes_search(query: str, limit: int = 8) -> list[dict]:
    """Keyword search over readable notes. No model, no index, no network."""
    from . import retrieval
    snapshot = policy.require_ready()
    if not isinstance(query, str) or not query.strip():
        return []
    out = []
    for hit in retrieval.search(query, limit=_clamp(limit, 1, MAX_LIMIT)):
        note = notes.Note(hit.note_id, hit.title, hit.folder, hit.modified)
        if not _visible(note, snapshot):
            continue
        excerpt = prepare_outbound("export", [Passage.from_note(hit.excerpt, note)])[0]
        out.append({**_row(note), "excerpt": excerpt})
    return out


def notes_read(note_id: str) -> dict:
    """One note's text, policy-checked again now, not when it was listed."""
    from . import attachments
    snapshot = policy.require_ready()
    if not isinstance(note_id, str) or not note_id.strip():
        return dict(NOT_AVAILABLE)
    note = notes.get_note(note_id)
    if note is None or not _visible(note, snapshot):
        return dict(NOT_AVAILABLE)
    body = notes.read_body(note.id)
    text = prepare_outbound("export", [Passage.from_note(markup.to_text(body), note)])[0]
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + MORE
    # A recording or a file leaves no trace in the body at all: a voice memo
    # reads back as an empty note. Ask Notes the second question so silence
    # about a file is never mistaken for "there is no file".
    hidden = markup.holds_media(body) or bool(attachments.on_note(note.id, note.modified))
    return {**_row(note), "text": text, "has_attachments_not_shown": hidden}


def agenda(days: int = 7, *, checker=None) -> dict:
    """Today, the next `days`, and open reminders, with an honest word about any
    app she cannot read. The same words `nodes.agenda` gives the model."""
    from . import calendar, nodes, reminders
    days = _clamp(days, 1, MAX_DAYS)
    unreadable = nodes.blind_apps(checker=checker)

    def read(app: str, fetch, empty: str) -> str:
        try:
            body = fetch()
        except Exception:
            # EventKit can wedge or be revoked mid-session. An unread app is
            # unknown, and must never come back looking like an empty one.
            return f"{app} could not be read just now; do not assume it is free or empty."
        return nodes.honest(body, app, empty, unreadable)

    return {
        "today": read("Calendar", calendar.brief, "Nothing in the calendar today."),
        "week": read("Calendar", lambda: calendar.week(days=days), calendar.empty_week(days)),
        "reminders": read("Reminders", reminders.summary, "Nothing outstanding in Reminders."),
    }


def ask(request: str, *, writes: bool, brain) -> dict:
    """Run the normal graph on a request from an MCP client. Without `writes`
    it is a dry run: Nemotron answers, nothing is written."""
    from . import graph, requests
    if not isinstance(request, str) or not request.strip():
        return {"error": "empty request"}
    envelope = requests.create(request, source="mcp")
    state = graph.run_request(envelope, brain=brain, dry_run=not writes, trigger="mcp")
    return {"answer": state.answer, "results": list(state.results), "dry_run": not writes}
