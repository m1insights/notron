"""The Apple bridge, as plain functions an MCP client reaches through `mcp_server`.

Every read here leaves the Mac for whatever AI provider the client uses, so each
one goes through the same library and outbound checks as a Nemotron call. The
tests are named after what would leak, or mislead, if a check went missing.
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from notron import bridge, calendar, graph, library, nodes, reminders, retrieval, workspace
from notron.permissions import Check

PARKING = "Notes/Parking Garages"
SUPPS = "Notes/Supps"
SYSTEM = {title: f'{workspace.FOLDER}/{title}' for title in workspace.SYSTEM_NOTES}


def _ignore(*ids):
    library.save(library.Library(homes={'n1', 'note-1'}, decided={'n1', 'note-1'},
                                 ignore=set(ids), allow_new_notes=True, system_notes=SYSTEM))


def test_an_ignored_note_is_indistinguishable_from_a_missing_one():
    """A client that gets "ignored" for one id and "missing" for another has
    learned that the first note exists. Same words for both, and an ignored
    note is never listed, searched or read."""
    _ignore(PARKING)
    ignored = bridge.notes_read(PARKING)
    missing = bridge.notes_read("Notes/No Such Note")
    assert ignored == missing == {"error": "not available"}
    assert PARKING not in [n["id"] for n in bridge.notes_list()]
    assert PARKING not in [h["id"] for h in bridge.notes_search("parking garages")]


def test_an_ignore_made_after_listing_is_honoured_at_read_time():
    """The list a client holds is older than the choice the user just made."""
    assert PARKING in [n["id"] for n in bridge.notes_list()]
    _ignore(PARKING)
    assert bridge.notes_read(PARKING) == {"error": "not available"}


def test_about_me_is_never_listed_or_read():
    """Her instruction note and the rest of 🤖 NOTRON are instructions, not data."""
    listed = bridge.notes_list()
    assert listed and not any(n["title"] in workspace.SYSTEM_NOTES for n in listed)
    assert not any(n["folder"] == workspace.FOLDER for n in listed)
    for title, nid in SYSTEM.items():
        assert bridge.notes_read(nid) == {"error": "not available"}, title
    assert bridge.notes_search("about me") == [] or all(
        h["title"] not in workspace.SYSTEM_NOTES for h in bridge.notes_search("about me"))


def test_about_me_moved_out_of_her_folder_is_still_not_readable(_notes_is_never_the_real_one):
    """The folder is not the only fence: a registered system note or one titled
    like one stays out even if the user dragged it into another folder."""
    app = _notes_is_never_the_real_one
    app.folders[0][1].append(workspace.ABOUT)
    assert bridge.notes_read(f"Notes/{workspace.ABOUT}") == {"error": "not available"}
    assert workspace.ABOUT not in [n["title"] for n in bridge.notes_list()]


def test_read_redacts_secrets_on_the_way_out(_notes_is_never_the_real_one):
    app = _notes_is_never_the_real_one
    app.bodies[PARKING] = ("<div>Parking Garages</div>"
                           "<div>gate code: password ruchimanan</div>"
                           "<div>token sk-abcdefghijklmnop1234</div>")
    out = bridge.notes_read(PARKING)
    assert out["title"] == "Parking Garages"
    assert "Parking Garages" in out["text"]
    assert "ruchimanan" not in out["text"]
    assert "sk-abcdefghijklmnop1234" not in out["text"]


def test_a_vault_titled_note_is_not_listed_or_read(_notes_is_never_the_real_one):
    app = _notes_is_never_the_real_one
    app.folders[0][1].append("Passwords")
    assert "Passwords" not in [n["title"] for n in bridge.notes_list()]
    assert bridge.notes_read("Notes/Passwords") == {"error": "not available"}
    assert bridge.notes_search("passwords") == []


def test_a_note_with_a_picture_says_so_and_returns_no_image(_notes_is_never_the_real_one):
    """Invariant 12: a client must not be able to read silence about a photo as
    "there is no photo"."""
    app = _notes_is_never_the_real_one
    app.bodies[PARKING] = ('<div>Parking Garages</div><div>level 3</div>'
                           '<div><img src="data:image/heic;base64,AAAABBBBCCCC"></div>')
    out = bridge.notes_read(PARKING)
    assert out["has_attachments_not_shown"] is True
    assert "level 3" in out["text"]
    assert "<img" not in out["text"] and "base64" not in out["text"] and "AAAABBBB" not in out["text"]
    plain = bridge.notes_read(SUPPS)
    assert plain["has_attachments_not_shown"] is False


def test_a_voice_memo_with_no_trace_in_the_body_is_still_owned_up_to(_notes_is_never_the_real_one):
    """A note holding a recording reads back as an empty body. Without asking
    for its attachments, the bridge would hand over "" as the whole note."""
    app = _notes_is_never_the_real_one
    app.bodies[PARKING] = "<div><br><br></div>"
    app.attachments[PARKING] = [("memo.m4a", "att-1")]
    out = bridge.notes_read(PARKING)
    assert out["has_attachments_not_shown"] is True
    assert "att-1" not in str(out) and "memo.m4a" not in str(out)


def test_a_long_note_is_cut_with_a_marker_not_silently(_notes_is_never_the_real_one):
    app = _notes_is_never_the_real_one
    app.bodies[PARKING] = "<div>Parking Garages</div><div>" + "x" * 30_000 + "</div>"
    out = bridge.notes_read(PARKING)
    assert len(out["text"]) <= bridge.MAX_TEXT + len(bridge.MORE)
    assert out["text"].endswith(bridge.MORE)


def test_search_uses_no_brain_and_no_network(monkeypatch):
    """Note reads must work with no Nebius key: an embedding call here would
    send the query out and fail for every user without one."""
    from notron import brain, index

    def no(*a, **kw):
        raise AssertionError("search reached a model or the index")
    monkeypatch.setattr(brain, "Brain", no)
    monkeypatch.setattr(index, "search", no)
    hits = bridge.notes_search("parking garages")
    assert hits and hits[0]["id"] == PARKING
    assert set(hits[0]) == {"id", "title", "folder", "modified", "excerpt"}


def test_limits_are_capped_whatever_the_client_asks(monkeypatch):
    seen = {}

    def spy(query, *, limit, **kw):
        seen["limit"] = limit
        return []
    monkeypatch.setattr(retrieval, "search", spy)
    bridge.notes_search("anything", limit=10_000)
    assert seen["limit"] == bridge.MAX_LIMIT
    bridge.notes_search("anything", limit=-3)
    assert seen["limit"] == 1


def test_ask_without_writes_is_a_dry_run(monkeypatch):
    seen = {}

    class S:
        answer = "You parked on level 3."
        results = ["would append to 📥 Ask Notron"]

    def spy(envelope, *, brain, dry_run, trigger):
        seen.update(envelope=envelope, brain=brain, dry_run=dry_run, trigger=trigger)
        return S()
    monkeypatch.setattr(graph, "run_request", spy)
    brain = object()
    out = bridge.ask("where did I park", writes=False, brain=brain)
    assert seen["dry_run"] is True and seen["trigger"] == "mcp" and seen["brain"] is brain
    assert seen["envelope"].source == "mcp" and seen["envelope"].text == "where did I park"
    assert out == {"answer": "You parked on level 3.", "results": ["would append to 📥 Ask Notron"],
                   "dry_run": True}
    monkeypatch.setattr(bridge, "_prepare_to_write", lambda brain: None)
    bridge.ask("where did I park", writes=True, brain=brain)
    assert seen["dry_run"] is False


def test_an_mcp_request_runs_the_real_graph_as_a_dry_run():
    """`mcp` must be a source the envelope accepts and a trigger the graph runs."""
    from tests.test_graph import FakeBrain
    out = bridge.ask("when is my shoot?", writes=False, brain=FakeBrain())
    assert out["answer"] and out["dry_run"] is True


def test_the_router_cannot_ignore_a_request_from_an_mcp_client():
    """Everything an MCP client sends through ask_notron was said to her. An
    `ignore` there is silence the client reads as an empty answer."""
    from notron.state import State
    from tests.test_graph import FakeBrain
    state = nodes.router(State(request="Reply with the single word: ready", trigger="mcp"),
                         brain=FakeBrain(intent="ignore"))
    assert state.intent == "question"


def _blind(app):
    other = "Reminders" if app == "Calendar" else "Calendar"
    return lambda: [Check(app, False, "is denied", "System Settings → Privacy & Security"),
                    Check(other, True, "full access", "")]


def _empty_day(monkeypatch):
    monkeypatch.setattr(calendar, "brief", lambda **kw: "Nothing in the calendar today.")
    monkeypatch.setattr(calendar, "week", lambda **kw: "Nothing in the calendar this week.")
    monkeypatch.setattr(reminders, "summary", lambda **kw: "Nothing outstanding in Reminders.")


def test_blind_calendar_is_reported_not_empty(monkeypatch):
    """Measured 2026-09-06: an ungranted process reads zero events, no error.
    Handed to a client as "Nothing in the calendar", that is a free week."""
    _empty_day(monkeypatch)
    out = bridge.agenda(checker=_blind("Calendar"))
    for part in (out["today"], out["week"]):
        assert "Nothing in the calendar" not in part
        assert "I cannot read your Calendar" in part
    assert out["reminders"] == "Nothing outstanding in Reminders."


def test_blind_reminders_is_reported_not_nothing_to_do(monkeypatch):
    _empty_day(monkeypatch)
    out = bridge.agenda(checker=_blind("Reminders"))
    assert "I cannot read your Reminders" in out["reminders"]
    assert out["today"] == "Nothing in the calendar today."


def test_a_calendar_read_that_fails_is_not_a_free_day(monkeypatch):
    _empty_day(monkeypatch)

    def boom(**kw):
        raise RuntimeError("EventKit wedged")
    monkeypatch.setattr(calendar, "brief", boom)
    ok = lambda: [Check("Calendar", True, "full access", ""), Check("Reminders", True, "full access", "")]
    out = bridge.agenda(checker=ok)
    assert "free" in out["today"].lower() and "Nothing in the calendar" not in out["today"]


def test_agenda_days_are_capped(monkeypatch):
    _empty_day(monkeypatch)
    seen = {}
    monkeypatch.setattr(calendar, "week", lambda **kw: seen.update(kw) or "x")
    ok = lambda: [Check("Calendar", True, "full access", ""), Check("Reminders", True, "full access", "")]
    bridge.agenda(days=400, checker=ok)
    assert seen["days"] == bridge.MAX_DAYS


def test_agenda_redacts_secrets_on_the_way_out(monkeypatch):
    """An event title is user text like any note: the graph redacts it before a
    model sees it, so the bridge must before a client's provider does."""
    _empty_day(monkeypatch)
    monkeypatch.setattr(calendar, "brief", lambda **kw: "- 09:00 Wifi: password hunter2")
    monkeypatch.setattr(calendar, "week", lambda **kw: "- 10:00 Call sk-abcdefghijklmnop1234")
    monkeypatch.setattr(reminders, "summary", lambda **kw: "- reset login (password ruchimanan)")
    ok = lambda: [Check("Calendar", True, "full access", ""), Check("Reminders", True, "full access", "")]
    out = bridge.agenda(checker=ok)
    assert "hunter2" not in out["today"] and "Wifi" in out["today"]
    assert "sk-abcdefghijklmnop1234" not in out["week"]
    assert "ruchimanan" not in out["reminders"]


def test_the_list_is_the_most_recent_notes_not_the_first_folder(_notes_is_never_the_real_one, monkeypatch):
    """Capped at 50 in folder order, a library of hundreds hands a client the
    oldest corner of the first folder and nothing it is working on now."""
    from notron import notes as notes_mod
    listed = [notes_mod.Note(f"Notes/n{i}", f"Note {i}", "Notes",
                             f"Wednesday, {1 + i % 28} September 2026 at 10:00:00") for i in range(80)]
    monkeypatch.setattr(library, "user_notes", lambda *a, **kw: listed)
    out = bridge.notes_list(limit=50)
    stamps = [notes_mod.Note("x", "", "", r["modified"]).modified_at for r in out]
    assert len(out) == 50 and stamps == sorted(stamps, reverse=True)
    newest = max(n.modified_at for n in listed)
    assert stamps[0] == newest


def test_an_attachment_check_that_fails_is_not_reported_as_none(_notes_is_never_the_real_one, monkeypatch):
    """Notes busy on the second question is "unknown", and unknown is not "no file"."""
    from notron import attachments

    def busy(*a, **kw):
        raise RuntimeError("Notes busy")
    monkeypatch.setattr(attachments, "on_note", busy)
    out = bridge.notes_read(SUPPS)
    assert out["has_attachments_not_shown"] is True
    assert "Supps" in out["text"]


def test_an_ignored_note_is_refused_before_notes_is_asked(_notes_is_never_the_real_one):
    """Same pattern as attachments.on_note: an ignored id costs no Notes query."""
    app = _notes_is_never_the_real_one
    _ignore(PARKING)
    app.calls.clear()
    assert bridge.notes_read(PARKING) == {"error": "not available"}
    assert "metadata" not in app.calls and "body" not in app.calls


def test_a_note_after_the_start_from_cutoff_is_readable_not_just_listed():
    """The id-only pre-check has no date. Asked without one, the cutoff hides
    every undecided note, so a note the list just offered came back "not available"."""
    from datetime import datetime
    library.save(library.Library(homes=set(), decided=set(), allow_new_notes=True,
                                 start_from=datetime(2026, 1, 1), system_notes=SYSTEM))
    assert PARKING in [n["id"] for n in bridge.notes_list()]
    out = bridge.notes_read(PARKING)
    assert out.get("error") is None and "Parking Garages" in out["text"]


def test_ask_while_the_listener_runs_says_busy_instead_of_hanging(monkeypatch):
    """run_request blocks on the worker lock the listener holds for its whole
    life. From an MCP client that was an ask_notron call that never returned."""
    from notron.health import WorkerLock
    import threading

    def never(*a, **kw):
        raise AssertionError("ran a second executor beside the listener")
    monkeypatch.setattr(graph, "run_request", never)
    held, done = threading.Event(), threading.Event()

    def listener():
        with WorkerLock() as lock:
            assert lock.acquired
            held.set()
            done.wait(5)
    t = threading.Thread(target=listener)
    t.start()
    held.wait(5)
    try:
        out = bridge.ask("what's on today", writes=True, brain=object())
    finally:
        done.set()
        t.join()
    assert out == {"error": bridge.BUSY}


def test_receipts_are_delivered_only_after_a_real_run(monkeypatch):
    class S:
        answer, results = "ok", []
    monkeypatch.setattr(graph, "run_request", lambda *a, **kw: S())
    monkeypatch.setattr(bridge, "_prepare_to_write", lambda brain: None)
    delivered = []
    bridge.ask("x", writes=False, brain=object(), after=lambda: delivered.append(1))
    assert delivered == []
    bridge.ask("x", writes=True, brain=object(), after=lambda: delivered.append(1))
    assert delivered == [1]



def _nothing_runs(monkeypatch):
    def never(*a, **kw):
        raise AssertionError("the graph ran")
    monkeypatch.setattr(graph, "run_request", never)


def test_ask_honours_a_pause_the_user_set(monkeypatch):
    """worker.submit refuses to run while paused; an MCP client must not be the
    way around the user's own pause switch, dry run or not."""
    from notron.health import HealthStore
    _nothing_runs(monkeypatch)
    HealthStore().set_paused(True)
    for writes in (False, True):
        assert bridge.ask("file this", writes=writes, brain=object()) == {"error": bridge.PAUSED}


def test_a_failed_worker_probe_runs_nothing(monkeypatch):
    """Writes take the same startup checks a CLI write does (permissions, key,
    provider). One that fails is an answer, and the graph never starts."""
    from notron import worker, worker_migration
    from notron.health import HealthStore
    from notron.policy import PolicyError
    _nothing_runs(monkeypatch)
    monkeypatch.setattr(worker_migration, "migrate_filer", lambda: None)

    def denied():
        raise PolicyError("Native app permission requires attention.")
    monkeypatch.setattr(worker, "probe", denied)
    recorded = []
    monkeypatch.setattr(worker, "failure", recorded.append)
    out = bridge.ask("file this", writes=True, brain=object())
    assert out["error"].startswith(bridge.NOT_READY)
    assert "Native app permission" in out["error"]
    assert [type(e) for e in recorded] == [PolicyError], "recorded in worker health, as submit does"


def test_preflight_runs_in_submit_order(monkeypatch):
    from notron import watch, worker, worker_migration
    order = []
    monkeypatch.setattr(worker_migration, "migrate_filer", lambda: order.append("migrate"))
    monkeypatch.setattr(worker, "probe", lambda: order.append("probe") or object())
    monkeypatch.setattr(worker.Queue, "recover_interrupted", lambda self: order.append("interrupted"))

    class W:
        def __init__(self, brain): pass
        def recover_pending(self):
            order.append("pending")
            return False
    monkeypatch.setattr(watch, "Watcher", W)

    class S:
        answer, results = "ok", []
    monkeypatch.setattr(graph, "run_request", lambda *a, **kw: order.append("run") or S())
    bridge.ask("x", writes=True, brain=object(), after=lambda: order.append("receipts"))
    assert order == ["migrate", "probe", "interrupted", "pending", "run", "receipts"]


def test_a_write_that_raises_still_gets_its_receipts_drained(monkeypatch):
    monkeypatch.setattr(bridge, "_prepare_to_write", lambda brain: None)

    def boom(*a, **kw):
        raise RuntimeError("Notes went away mid-write")
    monkeypatch.setattr(graph, "run_request", boom)
    delivered = []
    with pytest.raises(RuntimeError):
        bridge.ask("x", writes=True, brain=object(), after=lambda: delivered.append(1))
    assert delivered == [1]
