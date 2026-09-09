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
    monkeypatch.setattr('notron.worker.probe', lambda: None)
    calls = []

    class StopListener(BaseException):
        pass

    def boom():
        if len(calls) >= 2:
            raise StopListener()
        calls.append(1)
        raise TimeoutError("osascript timed out")

    w.check_ask = boom
    w.recover_pending = lambda: False
    w.scanner.prime = lambda: 0
    monkeypatch.setattr(watch.notes, "warm_up", lambda: 0.0)
    w.scanner.primed = False
    w.ask_poll = 0.01
    w.on_event = lambda m: None

    import threading
    def run():
        try:
            w.run_forever()
        except StopListener:
            pass
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=3)
    assert not t.is_alive()
    assert len(calls) > 1, "loop stopped after the first failure"


def test_check_ask_forgets_a_deleted_note_and_looks_again(monkeypatch):
    from notron import library, workspace
    lib = library.load()
    lib.system_notes[workspace.ASK] = 'stale-id'
    library.save(lib)
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
    from notron import library, workspace
    lib = library.load()
    lib.system_notes[workspace.ASK] = 'n1'
    library.save(lib)
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
    from notron import library, workspace
    lib = library.load()
    lib.system_notes[workspace.DUMP] = 'stale-id'
    library.save(lib)
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


def test_ask_persists_before_settling_and_restart_reuses_identity(monkeypatch):
    from notron import requests
    body = ask_note('remind me to call')
    monkeypatch.setattr(watch.notes, 'read_body', lambda nid: body)
    watcher = watch.Watcher(brain=None, settle=100)
    watcher.check_ask()
    first = requests.current().pending()
    assert len(first) == 1
    assert first[0].envelope.source == 'ask'
    restarted = watch.Watcher(brain=None, settle=100)
    restarted.check_ask()
    assert [r.request_id for r in requests.current().pending()] == [first[0].request_id]


def test_scanner_attaches_persisted_occurrence_ids(monkeypatch):
    from notron import mentions, requests, notes
    note = notes.Note('n1', 'Ideas', 'Notes', 'today')
    body = '<div>Ideas</div><div>#notron remind me to call</div>'
    monkeypatch.setattr(mentions.notes, 'list_all_notes', lambda: [note])
    monkeypatch.setattr(mentions.notes, 'read_body', lambda nid: body)
    scanner = mentions.Scanner()
    found = scanner.scan()
    assert len(found) == 1
    first = found[0].envelope
    assert first.source == 'mention' and first.note_id == 'n1'
    assert requests.current().get(first.request_id).status == 'prepared'
    assert scanner.scan()[0].envelope.request_id == first.request_id


def test_watcher_uses_entire_occurrence_id_instead_of_text_prefix(monkeypatch):
    from notron import requests
    prefix = 'remind me to call a very long shared prefix '
    body = f'<div>{workspace.ASK}</div><div>{prefix}one</div><div></div><div></div><div>{prefix}two</div>'
    monkeypatch.setattr(watch.notes, 'read_body', lambda nid: body)
    watcher = watch.Watcher(brain=None, settle=100)
    watcher.check_ask()
    pending = requests.current().pending()
    assert len(pending) == 2
    assert len(watcher._pending) == 2
    assert all(any(r.request_id in key for key in watcher._pending) for r in pending)


def test_dump_commits_batch_before_inference_and_restart_does_not_replay(monkeypatch):
    from notron import requests
    body = f'<div>{workspace.DUMP}</div><div>synthetic new thought</div>'
    monkeypatch.setattr(watch.notes, 'read_body', lambda nid: body)
    monkeypatch.setattr(watch.filer, 'worth_a_pass', lambda body: True)
    seen = []
    def run(*a, **kw):
        records = requests.current().pending()
        assert len(records) == 1 and records[0].status == 'running'
        seen.append(records[0].request_id)
        raise RuntimeError('interruption')
    monkeypatch.setattr(watch.filer, 'run', run)
    watcher = watch.Watcher(brain=None, dump_settle=0)
    watcher.check_dump()
    assert len(requests.current().pending()) == 1
    with pytest.raises(RuntimeError):
        watcher.check_dump()
    restarted = watch.Watcher(brain=None, dump_settle=0)
    restarted.check_dump()
    restarted.check_dump()
    assert len(seen) == 1


def test_dump_changed_after_settling_does_not_run(monkeypatch):
    from notron import requests
    body = f'<div>{workspace.DUMP}</div><div>synthetic thought</div>'
    changed = body.replace('thought', 'changed thought')
    monkeypatch.setattr(watch.notes, 'read_body', lambda nid: body)
    monkeypatch.setattr(watch.filer, 'worth_a_pass', lambda body: True)
    def forbidden(*a, **kw):
        pytest.fail('Filing ran after source changed')
    monkeypatch.setattr(watch.filer, 'run', forbidden)
    watcher = watch.Watcher(brain=None, dump_settle=0)
    watcher.check_dump()
    reads = iter([body, changed])
    monkeypatch.setattr(watch.notes, 'read_body', lambda nid: next(reads))
    watcher.check_dump()
    assert requests.current().pending()[0].status == 'prepared'
# 2026-09-05: a question tagged in a 25k-character story bible asked about "my
# first scene idea written at the end of this note". She was handed the first
# 4,000 characters of it, so she answered that she could not find any such idea.
def test_a_note_she_is_tagged_in_arrives_whole():
    body = markup.render("Book idea", "chapter one\n\n@notron what do you think\n\nchapter two")
    seen = watch.here_text(body)
    assert "chapter one" in seen and "chapter two" in seen


def test_scanner_persists_the_full_note_context_with_its_revision(monkeypatch):
    from notron import mentions, notes, requests
    note = notes.Note('n1', 'Story', 'Notes', 'today')
    body = markup.render(note.title, 'opening ' * 2000 + '\n\n@notron review this\n\nMY ENDING')
    monkeypatch.setattr(notes, 'list_all_notes', lambda: [note])
    monkeypatch.setattr(notes, 'read_body', lambda nid: body)
    envelope = mentions.Scanner().scan()[0].envelope
    persisted = requests.current().get(envelope.request_id).envelope
    assert 'MY ENDING' in persisted.here
    assert len(persisted.here) > 4000
    assert persisted.source_revision == requests.revision(body)


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
    from notron import attachments, mentions, requests

    w = watch.Watcher(brain=None, settle=0)
    from notron.notes import Note
    note = Note('n1', 'New Recording', 'Notes', 'Wednesday, 2 September 2026 at 21:30:00')
    body = markup.render(note.title, "@notron what's in this recording?")
    monkeypatch.setattr(watch.notes, 'list_all_notes', lambda: [note])
    monkeypatch.setattr(watch.notes, "read_body", lambda note_id: body)
    monkeypatch.setattr(attachments, "on_note", lambda note_id, modified="": [
        attachments.Attachment(id="a1", name="recording.m4a", kind="audio")])

    seen = {}

    def fake_run(envelope, **kw):
        seen.update(kw)
        assert requests.current().get(envelope.request_id).status == 'prepared'
        from notron.state import State
        return State(request=envelope.text)

    monkeypatch.setattr(watch.graph, "run_request", fake_run)
    w.sweep_mentions()      # first sight — she waits for the typing to settle
    w.sweep_mentions()
    assert [(a.kind, a.name) for a in seen["carried"]] == [("audio", "recording.m4a")]


# ------------------- a question answered somewhere else is still answered

def _picture_note(monkeypatch, mentions, question="what is in this picture?",
                  modified="monday"):
    from notron import markup, library
    lib = library.load()
    lib.decided.add('p1')
    library.save(lib)

    class Note:
        id, title, folder = "p1", "Holiday", "Notes"

    Note.modified = modified        # typing in a note moves its timestamp
    body = markup.render("Holiday", f"@notron {question}")
    monkeypatch.setattr(mentions.notes, "list_all_notes", lambda: [Note()])
    monkeypatch.setattr(mentions.notes, "read_body", lambda i: body)
    return Note


def _complete_occurrence(mention):
    from notron import requests
    store = requests.current()
    assert store.claim(mention.envelope.request_id)
    store.finish(mention.envelope.request_id)


def test_a_note_she_answered_elsewhere_stops_being_re_read(tmp_path, monkeypatch):
    """A note holding a picture can never carry her receipt, so it stayed
    `pending` forever — and `scan` re-reads every pending note on every sweep.
    Live on 2026-09-06 that was a 1.8MB AppleScript read every twenty seconds,
    for the life of the note, against the one app that serves one request at a
    time."""
    from notron import mentions
    monkeypatch.setattr(mentions, "STATE", tmp_path / "seen.json")
    _picture_note(monkeypatch, mentions)

    s = mentions.Scanner()
    found = s.scan()
    assert len(found) == 1 and s.pending == {"p1"}

    _complete_occurrence(found[0])
    assert s.scan() == [], "she has already answered this one"
    assert s.pending == set(), "so the note must stop being read every sweep"
    assert found[0].question not in mentions.STATE.read_text()


def test_a_new_question_in_that_same_note_is_still_answered(tmp_path, monkeypatch):
    """The first version of this remembered the *note*, not the question, so
    every later question in a note holding a picture was silently ignored for
    good."""
    from notron import mentions
    monkeypatch.setattr(mentions, "STATE", tmp_path / "seen.json")
    _picture_note(monkeypatch, mentions, "what is in this picture?")

    s = mentions.Scanner()
    _complete_occurrence(s.scan()[0])
    assert s.scan() == []

    _picture_note(monkeypatch, mentions, "and when was it taken?", modified="tuesday")
    assert [m.question for m in s.scan()] == ["and when was it taken?"]


def test_identical_question_after_observed_removal_is_a_new_occurrence(monkeypatch):
    from notron import mentions
    _picture_note(monkeypatch, mentions)
    scanner = mentions.Scanner()
    first = scanner.scan()[0]
    _complete_occurrence(first)
    scanner.scan()
    _picture_note(monkeypatch, mentions, modified='tuesday')
    monkeypatch.setattr(mentions.notes, 'read_body', lambda nid: '<div>Holiday</div>')
    assert scanner.scan() == []
    _picture_note(monkeypatch, mentions, modified='wednesday')
    second = scanner.scan()[0]
    assert second.question == first.question
    assert second.envelope.request_id != first.envelope.request_id


@pytest.mark.parametrize('completed', [False, True])
def test_answer_success_requires_a_complete_receipt(monkeypatch, completed):
    from notron import requests
    from notron.state import State
    envelope = requests.create('question', source='mention', note_id='n1')
    state = State(request='question', receipt_complete=completed)
    seen = []
    watcher = watch.Watcher(brain=None)
    monkeypatch.setattr(watcher, '_carried', lambda nid, modified: seen.append(nid) or [])
    monkeypatch.setattr(watch.graph, 'run_request', lambda *args, **kwargs: state)
    assert watcher._answer('question', title='Ideas', folder='Notes', after=0,
                           note_id='n1', envelope=envelope) is completed
    assert seen == ['n1']


def test_she_does_not_answer_it_again_after_a_restart(tmp_path, monkeypatch):
    """In-memory only, every restart appended the same answer to 📥 Ask Notron
    again — and paid for a graph run to do it."""
    from notron import mentions
    monkeypatch.setattr(mentions, "STATE", tmp_path / "seen.json")
    _picture_note(monkeypatch, mentions)

    s = mentions.Scanner()
    _complete_occurrence(s.scan()[0])

    after_restart = mentions.Scanner()
    after_restart.prime()
    assert after_restart.scan() == []


def test_a_note_that_no_longer_exists_is_dropped_from_the_to_do_list(tmp_path, monkeypatch):
    """`pending` only ever discarded ids it saw again, so an id for a deleted
    note — or one left by an old test — stayed in .notron/seen.json for ever."""
    from notron import mentions
    monkeypatch.setattr(mentions, "STATE", tmp_path / "seen.json")
    _picture_note(monkeypatch, mentions)

    s = mentions.Scanner()
    s.pending = {"p1", "a-note-that-was-deleted"}
    s.changed()
    assert s.pending == {"p1"}


# --- the log answers "why is she blind" ------------------------------------

def test_the_listener_log_names_an_app_it_cannot_read(monkeypatch):
    """`notron permissions` reports the terminal's grant. The listener is a
    different process with a different one, and on 2026-09-06 it had none —
    zero calendars, zero events, no error, four hundred log lines saying
    "125 chars of real commitments"."""
    from notron import watch as watch_mod
    from notron.permissions import Check

    said = []
    w = watch_mod.Watcher.__new__(watch_mod.Watcher)
    w.on_event = said.append
    w._report_blind_spots(checker=lambda: [
        Check("Calendar", False, "has not been asked yet", "System Settings → Calendars"),
        Check("Reminders", True, "full access", ""),
    ])
    joined = "\n".join(said)
    assert "Calendar" in joined and "has not been asked yet" in joined
    assert "System Settings → Calendars" in joined
    assert "Reminders" not in joined, "a working app is not worth a warning"


def test_a_fully_permitted_listener_says_nothing_at_startup(monkeypatch):
    from notron import watch as watch_mod
    from notron.permissions import Check

    said = []
    w = watch_mod.Watcher.__new__(watch_mod.Watcher)
    w.on_event = said.append
    w._report_blind_spots(checker=lambda: [Check("Calendar", True, "full access", ""),
                                           Check("Reminders", True, "full access", "")])
    assert said == []


def test_a_permission_check_that_explodes_does_not_stop_the_listener_starting():
    from notron import watch as watch_mod

    def boom():
        raise RuntimeError("no osascript")

    said = []
    w = watch_mod.Watcher.__new__(watch_mod.Watcher)
    w.on_event = said.append
    w._report_blind_spots(checker=boom)
    assert any("couldn't check permissions" in m for m in said)
