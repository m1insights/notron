"""The listening loop, exercised without touching Notes or a model."""

import sys, pathlib, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

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


def test_check_ask_forgets_a_deleted_note_and_looks_again(monkeypatch):
    """A cached note id that has been deleted out from under the listener must
    not be retried forever. The old code never reset it, so a deleted-then-
    recreated Ask note was never found again without a restart."""
    w = watch.Watcher(brain=None)
    w._ask_id = "stale-id"
    monkeypatch.setattr(watch.notes, "read_body",
                         lambda note_id: (_ for _ in ()).throw(
                             watch.AppleScriptError("Can't get note id \"stale-id\".")))
    w.check_ask()
    assert w._ask_id is None


def test_check_ask_keeps_the_id_when_notes_is_only_busy(monkeypatch):
    """A timeout isn't a deletion — don't pay for a fresh lookup over a hiccup,
    and let the caller's normal retry-the-whole-loop handling deal with it."""
    w = watch.Watcher(brain=None)
    w._ask_id = "n1"
    monkeypatch.setattr(watch.notes, "read_body",
                         lambda note_id: (_ for _ in ()).throw(watch.NotesBusy("timed out")))
    with pytest.raises(watch.NotesBusy):
        w.check_ask()
    assert w._ask_id == "n1"


def test_check_dump_forgets_a_deleted_note_and_looks_again(monkeypatch):
    w = watch.Watcher(brain=None)
    w._dump_id = "stale-id"
    monkeypatch.setattr(watch.notes, "read_body",
                         lambda note_id: (_ for _ in ()).throw(
                             watch.AppleScriptError("Can't get note id \"stale-id\".")))
    w.check_dump()
    assert w._dump_id is None


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


def test_a_tagged_note_keeps_being_reported_until_it_is_answered(tmp_path, monkeypatch):
    """Change detection reports a note once. Settling needs at least two looks,
    so a report-once scanner and a wait-for-quiet reply rule deadlock."""
    from notron import mentions
    monkeypatch.setattr(mentions, "STATE", tmp_path / "seen.json")

    s = mentions.Scanner()
    s.pending = {"n1"}
    s.seen = {"n1": "monday"}

    class Note:
        id, title, folder, modified = "n1", "Book idea", "Notes", "monday"

    monkeypatch.setattr(mentions.notes, "list_all_notes", lambda: [Note()])
    assert [n.id for n in s.changed()] == ["n1"], "an unanswered note must come back"


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


def test_a_question_that_cannot_be_answered_is_rested_not_retried_forever():
    """A question that produced no write stayed 'unanswered' in the note, so
    every poll sent it to the model again — a quiet API bill for one stuck
    message, at one call every eleven seconds."""
    w = watch.Watcher(brain=None)
    key = "ask:stuck question"
    assert w._worth_trying(key)
    w._attempted(key, wrote=False)
    assert w._worth_trying(key), "one failure deserves a second try"
    w._attempted(key, wrote=False)
    assert not w._worth_trying(key), "two failures earn a rest, not a loop"


def test_the_rest_ends_after_the_cooldown():
    w = watch.Watcher(brain=None)
    key = "ask:stuck question"
    w._failures[key] = (w.MAX_TRIES, time.time() - w.COOLDOWN - 1)
    assert w._worth_trying(key)


def test_a_successful_answer_clears_the_failure_count():
    w = watch.Watcher(brain=None)
    key = "ask:recovered"
    w._attempted(key, wrote=False)
    w._attempted(key, wrote=True)
    assert key not in w._failures


def test_is_running_reads_the_launchctl_exit_code():
    assert watch.is_running(runner=lambda: 0) is True
    assert watch.is_running(runner=lambda: 113) is False  # launchd's "not found"


# 2026-09-05: a question tagged in a 25k-character story bible asked about "my
# first scene idea written at the end of this note". She was handed the first
# 4,000 characters of it, so she answered that she could not find any such idea.
def test_a_note_she_is_tagged_in_arrives_whole():
    body = markup.render("Book idea", "chapter one\n\n@notron what do you think\n\nchapter two")
    seen = watch.here_text(body)
    assert "chapter one" in seen and "chapter two" in seen


def test_a_note_too_big_to_send_whole_still_shows_her_how_it_ends():
    filler = "\n".join(f"middle line {i}" for i in range(9_000))
    body = markup.render("ASCENSION", f"@notron is my ending any good\n\n{filler}\n\nMY FIRST SCENE IDEA")
    seen = watch.here_text(body, "@notron is my ending any good", budget=4_000)
    assert "MY FIRST SCENE IDEA" in seen
    assert "@notron is my ending any good" in seen
    assert len(seen) <= 4_000 + len(watch.ELIDED) * 2


def test_the_lines_around_the_tag_survive_the_trim():
    top = "\n".join(f"opening line {i}" for i in range(3_000))
    bottom = "\n".join(f"closing line {i}" for i in range(3_000))
    body = markup.render("Long note", f"{top}\n\nBURIED QUESTION @notron\n\n{bottom}")
    seen = watch.here_text(body, "BURIED QUESTION @notron", budget=6_000)
    assert "BURIED QUESTION @notron" in seen
    assert "opening line 0" in seen and "closing line 2999" in seen
    assert watch.ELIDED in seen


def test_the_files_hanging_off_a_tagged_note_travel_with_the_question(monkeypatch):
    """She was tagged in a note holding a voice memo. The body says nothing
    about it — Notes keeps attachments out of the HTML — so unless the sweep
    asks the second question, the model never learns the file exists."""
    from notron import attachments, mentions

    w = watch.Watcher(brain=None, settle=0)
    w.scanner.scan = lambda: [mentions.Mention(
        note_id="n1", title="New Recording", folder="Notes",
        question="what's in this recording?", after=0, raw="@notron what's in this recording?",
        modified="Wednesday, 2 September 2026 at 21:30:00")]
    monkeypatch.setattr(watch.notes, "read_body", lambda note_id: "<div>New Recording</div>")
    monkeypatch.setattr(attachments, "on_note", lambda note_id, modified="": [
        attachments.Attachment(id="a1", name="recording.m4a", kind="audio")])

    seen = {}

    def fake_run(question, **kw):
        seen.update(kw)
        from notron.state import State
        return State(request=question)

    monkeypatch.setattr(watch.graph, "run", fake_run)
    w.sweep_mentions()      # first sight — she waits for the typing to settle
    w.sweep_mentions()
    assert seen["carried"] == [("audio", "recording.m4a")]
