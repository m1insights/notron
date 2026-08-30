"""The listening loop, exercised without touching Notes or a model."""

import sys, pathlib, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from notron import conversation, markup, watch, workspace


def ask_note(md):
    return markup.render(workspace.ASK, md)


HEADER = "Type anything below this line and Notron will answer underneath it."


def test_the_message_that_was_lost_is_now_found():
    """A real message went unanswered: it was typed directly under the note's own
    'type below this line' header, at the top, while the reader looked at the end."""
    body = ask_note(f"{HEADER}\n\nHi Notron. How are you? I am about to read a book.\n\n"
                    "———\n\n**Notron:** an older answer\n\n———\n")
    qs = conversation.unanswered(body, ignore=watch.ASK_FURNITURE)
    assert len(qs) == 1 and "How are you?" in qs[0].text


def test_the_standing_header_is_never_mistaken_for_a_question():
    body = ask_note(f"{HEADER}\n\n———\n")
    assert conversation.unanswered(body, ignore=watch.ASK_FURNITURE) == []


def test_she_waits_for_your_typing_to_stop():
    w = watch.Watcher(brain=None, settle=10)
    assert not w._settled("ask:x", "what's on to")       # first sight
    assert not w._settled("ask:x", "what's on today?")   # you kept typing
    assert not w._settled("ask:x", "what's on today?")   # still inside the window


def test_she_answers_once_the_text_stops_changing():
    w = watch.Watcher(brain=None, settle=0)
    w._settled("ask:x", "what's on today?")
    assert w._settled("ask:x", "what's on today?")


def test_a_scanner_that_has_been_primed_ignores_old_tags():
    s = watch.mentions.Scanner()
    s.seen = {"n1": "yesterday"}
    s.primed = True
    assert s.seen["n1"] == "yesterday"


def test_the_listener_survives_notes_going_away(monkeypatch):
    """Notes quitting or a request timing out must not end the day."""
    w = watch.Watcher(brain=None)
    calls = []

    def boom():
        calls.append(1)
        raise TimeoutError("osascript timed out")

    w.check_ask = boom
    w.scanner.prime = lambda: 0
    monkeypatch.setattr(watch.notes, "warm_up", lambda: 0.0)
    w.scanner.primed = False
    w.ask_poll = 0.01
    w.on_event = lambda m: None

    import threading
    t = threading.Thread(target=w.run_forever, daemon=True)
    t.start()
    time.sleep(0.3)
    assert len(calls) > 1, "loop stopped after the first failure"


def test_a_restart_does_not_lose_a_tag(tmp_path, monkeypatch):
    """If you write #notron and the Mac reboots before she gets to it, she still
    owes you an answer."""
    from notron import mentions
    monkeypatch.setattr(mentions, "STATE", tmp_path / "seen.json")

    first = mentions.Scanner()
    first.seen = {"n1": "monday"}
    first.pending = {"n1"}
    first._save()

    after_restart = mentions.Scanner()
    after_restart.prime()
    assert after_restart.seen == {"n1": "monday"}, "it forgot what it had already seen"
    assert after_restart.pending == {"n1"}, "it forgot it still owed an answer"


def test_a_tagged_note_keeps_being_reported_until_it_is_answered():
    """Change detection reports a note once. Settling needs at least two looks,
    so a report-once scanner and a wait-for-quiet reply rule deadlock."""
    from notron import mentions

    s = mentions.Scanner()
    s.pending = {"n1"}
    s.seen = {"n1": "monday"}

    class Note:
        id, title, folder, modified = "n1", "Book idea", "Notes", "monday"

    mentions.notes.list_all_notes = lambda: [Note()]
    try:
        assert [n.id for n in s.changed()] == ["n1"], "an unanswered note must come back"
    finally:
        import importlib
        importlib.reload(mentions.notes)


def test_a_note_it_could_not_read_is_kept_not_dropped(tmp_path, monkeypatch):
    """Notes is busy sometimes. Dropping the note there means the tag is never
    answered until it happens to change again — which may be never."""
    from notron import mentions
    monkeypatch.setattr(mentions, "STATE", tmp_path / "seen.json")

    class Note:
        id, title, folder, modified = "n1", "Book idea", "Notes", "monday"

    s = mentions.Scanner()
    monkeypatch.setattr(mentions.notes, "list_all_notes", lambda: [Note()])
    monkeypatch.setattr(mentions.notes, "read_body", lambda i: (_ for _ in ()).throw(TimeoutError()))

    s.scan()
    assert "n1" in s.pending, "a note it could not read must be tried again"


def test_what_is_still_owed_survives_a_restart(tmp_path, monkeypatch):
    """State was written before the pending list was worked out, so a restart
    saw fresh timestamps, an empty to-do list, and never answered the tag."""
    from notron import markup, mentions
    monkeypatch.setattr(mentions, "STATE", tmp_path / "seen.json")

    class Before:
        id, title, folder, modified = "n1", "Book idea", "Notes", "monday"

    class After:
        id, title, folder, modified = "n1", "Book idea", "Notes", "tuesday"

    body = markup.render("Book idea", "a lighthouse story\n\n#notron is act two weak?")
    monkeypatch.setattr(mentions.notes, "list_all_notes", lambda: [Before()])
    monkeypatch.setattr(mentions.notes, "read_body", lambda i: body)

    first = mentions.Scanner()
    first.prime()                       # tags written before she was watching
    assert not first.scan(), "an old tag should not suddenly get a reply"

    monkeypatch.setattr(mentions.notes, "list_all_notes", lambda: [After()])
    assert first.scan(), "but a note you just touched should be picked up"

    after_restart = mentions.Scanner()
    after_restart.prime()
    assert after_restart.scan(), "the unanswered tag must survive the restart"
