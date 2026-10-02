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

import time
from datetime import datetime

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


def _visible(note: notes.Note, snapshot: policy.PolicySnapshot, lib=None) -> bool:
    """`lib` is a library already loaded for this call; a list passes one so the
    policy file is read once, not once per note."""
    hidden = (lib.hides(note.id, note.modified) if lib is not None
              else library.state_of(note.id, note.modified) == library.IGNORE)
    return not _system(note, snapshot) and snapshot.readable(note) and not hidden


def _row(note: notes.Note) -> dict:
    from . import privacy
    return {"id": note.id, "title": privacy.redact(note.title), "folder": privacy.redact(note.folder),
            "modified": note.modified}


def notes_list(limit: int = MAX_LIMIT) -> list[dict]:
    """The user's notes Notron may read: metadata only, no bodies."""
    snapshot = policy.require_ready()
    limit = _clamp(limit, 1, MAX_LIMIT)
    # Newest first, so a cap of 50 is what they are working on now rather than
    # the oldest corner of the first folder. An unparseable date sorts last.
    floor = datetime.min
    lib = library.load()
    ordered = sorted(library.user_notes(lib), key=lambda n: n.modified_at or floor, reverse=True)
    return [_row(n) for n in ordered if _visible(n, snapshot, lib)][:limit]


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
    if not snapshot.can_read(note_id):
        # Refused before Notes is asked anything, as `attachments.on_note` does:
        # an ignored note costs no query and leaves no trace in the call log.
        # Id-only on purpose: `library.state_of` without a date hides every
        # undecided note under a start-from cutoff. The date and title checks
        # run in `_visible` once `get_note` has them.
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
    try:
        hidden = markup.holds_media(body) or bool(attachments.on_note(note.id, note.modified))
    except Exception:
        # Notes busy or the question refused: whether a file hangs off this
        # note is unknown, and unknown must never read as "no file" (inv. 12).
        hidden = True
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

    def export(text: str) -> str:
        # Event titles and reminders are user text, redacted exactly as the
        # graph's Passage(..., "agenda") is before a model sees it.
        return prepare_outbound("export", [Passage(text, "agenda")])[0]

    return {
        "today": export(read("Calendar", calendar.brief, "Nothing in the calendar today.")),
        "week": export(read("Calendar", lambda: calendar.week(days=days), calendar.empty_week(days))),
        "reminders": export(read("Reminders", reminders.summary, "Nothing outstanding in Reminders.")),
    }


#: How long an ask waits for the running listener to get to it. A listener
#: tick can spend 30–74 s on Notes (measured 2026-09-23) before it drains the
#: queue, and Super then thinks for a few seconds more.
LISTENER_WAIT = 180
LISTENER_POLL = 0.5
STILL_WORKING = ("Notron's listener has your request but has not finished it yet. It will "
                 "still run; anything she writes lands in your notes and in 📊 Log. Ask again "
                 "in a minute for the answer here.")
NEEDS_REVIEW = ("Notron's listener could not finish this request safely, so it is waiting for "
                "you to review it (`notron review`).")
FINISHED_UNSEEN = ("Notron's listener finished this request, but its answer did not reach "
                   "here. Anything she wrote is in your notes and in 📊 Log.")
PAUSED_QUEUED = ("Notron was paused while your request waited for her. It is kept, and runs "
                 "when you resume her in the Notron app.")

PAUSED = ("Notron is paused, so she will not act from here either. "
          "Resume her in the Notron app, then ask again.")
NOT_READY = "Notron is not ready to act for you"


def _prepare_to_write() -> None:
    """The startup steps `worker.submit` runs before any CLI write, in its order:
    filer migration, the probe (permissions, key, provider), interrupted jobs,
    then pending requests. A write from an MCP client is no less a write, and
    skipping these would make the client the one path around them. Recovery
    uses the probe's own brain, as submit does: it is the one just proven to
    reach the provider. Raises on failure, after recording it in worker health
    exactly as submit does. The caller holds the Heartbeat."""
    from . import worker
    from .health import HealthStore
    from .watch import Watcher
    from .worker_migration import migrate_filer
    try:
        migrate_filer()
        probed = worker.probe()
        worker.Queue().recover_interrupted()
        recovery = Watcher(probed)
        for _ in range(200):
            if HealthStore().paused:
                raise _Paused()
            if not recovery.recover_pending():
                break
        HealthStore().update(state='ready', reason_code=None)
    except _Paused:
        raise
    except Exception as exc:
        worker.failure(exc)
        raise


class _Paused(Exception):
    pass


def _ask_listener(request: str, *, writes: bool, sleep, wait: float) -> dict:
    """Hand the request to the listener that holds the worker lock, and wait for
    its answer. One executor at a time is the rule: this process never runs the
    graph beside it. The listener runs the same `notron ask` a terminal does,
    with its own startup checks and receipts, so neither is repeated here."""
    from . import credentials, worker
    from .health import HealthStore
    if HealthStore().paused:
        return {"error": PAUSED}
    if credentials._provider is None:
        credentials.startup()
    queue = worker.Queue()
    job_id = queue.enqueue("ask", {"request": [request], "dry_run": not writes,
                                   "reply": True, "source": "mcp"})["job_id"]
    deadline = time.monotonic() + wait
    while True:
        # The status before the answer: execute saves the answer, then marks
        # the job completed, so once 'completed' is read the answer is there.
        # Read the other way round, a job finishing between the two reads
        # looked completed-without-answer and its answer was never collected.
        job = queue.get(job_id)
        if job is not None and job["status"] == "completed":
            answer = queue.take_answer(job_id)
            if answer is None:
                # A listener that died after the run: recover_interrupted
                # completes the job, but nothing saved what she said.
                return {"error": FINISHED_UNSEEN}
            return {**answer, "dry_run": not writes}
        if job is None or job["status"] == "needs_review":
            return {"error": NEEDS_REVIEW}
        if job["status"] == "queued" and HealthStore().paused:
            return {"error": PAUSED_QUEUED}
        if time.monotonic() >= deadline:
            return {"error": STILL_WORKING}
        sleep(LISTENER_POLL)


def _ask_here(request: str, *, writes: bool, brain, after) -> dict:
    """This process holds the worker lock: run the graph directly."""
    from . import graph, requests
    from .health import HealthStore, Heartbeat
    from .policy import PolicyError
    # The user's pause binds every surface, a dry run included: submit
    # refuses to execute anything while paused.
    if HealthStore().paused:
        return {"error": PAUSED}
    if not writes:
        envelope = requests.create(request, source="mcp")
        state = graph.run_request(envelope, brain=brain, dry_run=True, trigger="mcp")
    else:
        # Held across the run, as submit holds it across the job: health
        # must not read "stopped" while a real write is in flight.
        with Heartbeat(HealthStore()):
            try:
                _prepare_to_write()
            except _Paused:
                return {"error": PAUSED}
            except Exception as exc:  # noqa: BLE001 - recorded by worker.failure
                # PolicyError texts are fixed strings; anything else is named
                # by type only, since its message could carry a note title.
                why = str(exc) if isinstance(exc, PolicyError) else type(exc).__name__
                return {"error": f"{NOT_READY}: {why}"}
            envelope = requests.create(request, source="mcp")
            try:
                state = graph.run_request(envelope, brain=brain, dry_run=False, trigger="mcp")
            finally:
                if after is not None:
                    after()
    return {"answer": state.answer, "results": list(state.results), "dry_run": not writes}


def ask(request: str, *, writes: bool, brain, after=None, sleep=time.sleep,
        wait: float = LISTENER_WAIT) -> dict:
    """Run the normal graph on a request from an MCP client. Without `writes`
    it is a dry run: Nemotron answers, nothing is written. `after` runs under
    the same lock once a real run finishes, even one that raised (the CLI's
    receipt delivery).

    `graph.run_request` waits for the worker lock, and the listener holds it for
    its whole life. Running here would hang; replying "busy" refused every
    request in normal use. So while the listener runs, the request is queued
    for it and the listener's answer comes back."""
    from .health import WorkerLock
    if not isinstance(request, str) or not request.strip():
        return {"error": "empty request"}
    with WorkerLock() as lock:
        if lock.acquired:
            return _ask_here(request, writes=writes, brain=brain, after=after)
    # Waited on after the failed attempt is released: the listener needs
    # nothing from this process while it works.
    return _ask_listener(request, writes=writes, sleep=sleep, wait=wait)
