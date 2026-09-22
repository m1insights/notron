"""The two nodes behind "clean this note up" and "put it back".

Both are answers about one specific note, so both are routed in plain code from
the words the user typed — the model is never asked to judge whether "undo"
means undo. And both are bounded by invariant #2: outside 🤖 NOTRON the cleaned
version goes *underneath* what the user wrote, never over it, until that one
note has been opted into rewrite-in-place.
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from notron import conversation, markup, nodes, rewrite, undo, workspace
from notron.notes import Note
from notron.state import State

NOTE = "Parking Garages"
FOLDER = "Notes"
BODY_MD = "12 Trinity — $18 after 6pm\nBeacon St — $22 all day\n@notron clean this up"


class FakeBrain:
    """Same stand-in as tests/test_graph.py: records the tier, returns a fixed answer."""

    def __init__(self, intent="question", answer="## Garages\n- 12 Trinity — $18 after 6pm"):
        self.intent = intent
        self.answer = answer
        self.calls = []

    def ask_json(self, **kw):
        self.calls.append(("json", kw.get("tier")))
        return {"intent": self.intent, "needs_context": False, "needs_web": False, "why": "test"}

    def ask(self, **kw):
        self.calls.append(("text", kw.get("tier")))
        self.last = kw
        return self.answer


class NoBrain:
    """Any model call on these paths is the bug. Reading "undo" as undo, and a
    `yes` under her own offer as a yes, are facts — nobody should be paying a
    model to have an opinion about them."""

    def ask_json(self, **kw):
        raise AssertionError("the router asked the model something it can read itself")

    def ask(self, **kw):
        raise AssertionError("a model was called on a path that must be plain code")


def _note(id="n1", title=NOTE, folder=FOLDER):
    return Note(id=id, title=title, folder=folder, modified="x")


@pytest.fixture
def note_in_notes(monkeypatch):
    """One of the user's own notes behind `state.reply_to`, with a body she can
    read. Returns a dict so a test can change the body it will find."""
    live = {"body": markup.render(NOTE, BODY_MD)}
    monkeypatch.setattr(nodes.notes, "find_note", lambda folder, title: _note())
    monkeypatch.setattr(nodes.notes, "get_note", lambda nid: _note() if nid == "n1" else None)
    monkeypatch.setattr(nodes.notes, "list_notes", lambda folder: [_note()] if folder == FOLDER else [])
    monkeypatch.setattr(nodes.notes, "read_body", lambda note_id: live["body"])
    return live


@pytest.fixture
def no_such_note(monkeypatch):
    monkeypatch.setattr(nodes.notes, "find_note", lambda folder, title: None)
    monkeypatch.setattr(nodes.notes, "get_note", lambda nid: None)
    monkeypatch.setattr(nodes.notes, "list_notes", lambda folder: [])
    monkeypatch.setattr(nodes.notes, "read_body",
                        lambda note_id: pytest.fail("read a note that does not exist"))


def _snapshot(body):
    from notron.requests import revision
    return undo.Snapshot('synthetic-snapshot', body, revision(nodes.notes.read_body('n1')), 'written')


def _state(request="", after=3, **kw):
    return State(request=request, source_note_id="n1", reply_to=(NOTE, FOLDER, after), **kw)


# ----------------------------------------------------------------- the router

def test_undo_is_matched_in_code_and_never_costs_a_model_call():
    state = nodes.router(_state("undo"), brain=NoBrain())
    assert state.intent == "undo"


def test_revert_that_is_undo_too():
    assert nodes.router(_state("revert that please"), brain=NoBrain()).intent == "undo"


def test_asking_for_a_tidy_up_is_matched_in_code_too():
    for said in ("organize this", "clean this up", "tidy this note up", "clean it up"):
        state = nodes.router(_state(said), brain=NoBrain())
        assert state.intent == "organize", said


def test_a_bare_yes_on_one_of_their_notes_routes_to_the_organizer():
    """The `yes` under her offer has to reach the node that made the offer, and
    it is one word — there is nothing for a classifier to add."""
    for said in ("yes", "Yep", "go ahead", "do it."):
        state = nodes.router(_state(said), brain=NoBrain())
        assert state.intent == "organize", said


def test_undo_said_with_no_note_behind_it_still_goes_to_the_model():
    """`notron ask "undo my last change"` names no note. Guessing which one she
    touched last is exactly what decision 4 left out of scope."""
    brain = FakeBrain()
    state = nodes.router(State(request="undo my last change"), brain=brain)
    assert state.intent == "question"
    assert ("json", "fast") in brain.calls


def test_tidying_up_in_her_own_ask_note_is_an_ordinary_question():
    """`reply_to` is set for 📥 Ask Notron as well — the listener answers there
    the same way it answers a tag anywhere else. Rewriting in place is only ever
    a question about the user's own notes, and the Guard refuses a replace on
    the shared Ask note anyway."""
    brain = FakeBrain()
    state = State(request="clean this up", reply_to=(workspace.ASK, workspace.FOLDER, 2), source_note_id=f"{workspace.FOLDER}/{workspace.ASK}")
    assert nodes.router(state, brain=brain).intent == "question"


def test_a_yes_in_her_own_ask_note_is_not_consent_to_rewrite_it():
    brain = FakeBrain()
    state = State(request="yes", reply_to=(workspace.ASK, workspace.FOLDER, 2), source_note_id=f"{workspace.FOLDER}/{workspace.ASK}")
    assert nodes.router(state, brain=brain).intent == "question"


def test_the_model_may_not_pick_undo_or_organize_itself():
    """Both intents are decided above, from the words. The router prompt never
    mentions them, but a hallucinated one would otherwise reach a node that
    expects a real note behind `state.reply_to`."""
    for made_up in ("undo", "organize"):
        state = nodes.router(_state("what did I park where"), brain=FakeBrain(intent=made_up))
        assert state.intent == "question", made_up


def test_filing_still_wins_over_a_tidy_up():
    state = nodes.router(_state("file this: clean it up later"), brain=NoBrain())
    assert state.intent == "file"


# ------------------------------------------------------------------ the undoer

def test_the_undoer_restores_what_she_wrote_over(monkeypatch, note_in_notes):
    monkeypatch.setattr(nodes.undo, "peek", lambda note_id: _snapshot("<div>the user's own words</div>"))
    state = nodes.undoer(_state("undo", intent="undo"))

    assert state.writes[0].mode == "restore"
    assert state.writes[0].title == NOTE and state.writes[0].folder == FOLDER
    assert state.writes[0].markdown.startswith("<div>the user's own words</div>")
    assert "put it back" in state.answer.lower()


def test_the_receipt_rides_along_inside_the_restore(monkeypatch, note_in_notes):
    """One write, at the end of the note it put back. Two writes would refill the
    undo slot with the body she just restored; and a receipt anchored under the
    line that asked would land in the middle, leaving whatever the user had said
    before her last write unanswered — so the watcher hands it back and she
    redoes the very write that was just undone."""
    monkeypatch.setattr(nodes.undo, "peek", lambda note_id: _snapshot("<div>old</div>"))
    state = nodes.undoer(_state("undo", intent="undo"))

    assert [w.mode for w in state.writes] == ["restore"]
    landed = state.writes[0].markdown
    assert conversation.SIGNATURE in markup.to_text(landed)
    assert landed.index("old") < landed.index(conversation.SIGNATURE)


def test_the_undoer_ticks_every_open_tagged_turn_not_just_the_last(monkeypatch, note_in_notes):
    """A trailing turn only closes the turn right next to it. A tagged ask
    separated from the rest of the note by a real gap (more than
    conversation.MAX_GAP) is still unanswered after the restore, and the
    watcher hands it straight back — redoing the very write that was just
    undone — unless it gets ticked, the same way a filed line does."""
    old_body = (
        "<div>Parking Garages</div>"
        "<div>@notron clean this up</div>"
        "<div><br></div><div><br></div><div><br></div>"   # a real break: > MAX_GAP
        "<div>12 Trinity — $18</div>"
    )
    monkeypatch.setattr(nodes.undo, "peek", lambda note_id: _snapshot(old_body))
    state = nodes.undoer(_state("undo", intent="undo"))

    landed = state.writes[0].markdown
    assert conversation.unanswered(landed, ignore=(NOTE,), require_tag=True) == []
    assert "✓ " in landed and "put back" in landed


def test_the_undoer_ticks_a_tagged_line_written_as_its_own_bullet(monkeypatch, note_in_notes):
    """`notedoc.texts` (what `conversation.unanswered` reads) prefixes every
    list item with "• " for display; `notedoc.find_line` (what `mark_lines`
    matches against) compares the bare <li> text. Without stripping that
    prefix first, a tagged line written as a bullet never matches, never gets
    ticked, and the re-ask/redo loop the tick fix closes stays open for
    anything written as a list.

    NOTE — a real, narrower gap this does not close: when the tagged line
    shares a <ul> with untagged siblings, `notedoc.blocks()` treats the whole
    list as one block, and `conversation.unanswered`'s filed-check tests that
    whole block's text, not the one line inside it that got ticked — so a
    partially-ticked list block still reads as unanswered. Pre-existing,
    deeper than this diff (a block-vs-line granularity mismatch between
    notedoc and conversation.py), and not fixed here."""
    old_body = (
        "<div>Parking Garages</div>"
        "<ul><li>@notron clean this up</li></ul>"
        "<div><br></div><div><br></div><div><br></div>"
        "<div>Beacon St — $22</div>"
    )
    monkeypatch.setattr(nodes.undo, "peek", lambda note_id: _snapshot(old_body))
    state = nodes.undoer(_state("undo", intent="undo"))

    landed = state.writes[0].markdown
    assert conversation.unanswered(landed, ignore=(NOTE,), require_tag=True) == []
    assert "✓ " in landed and "put back" in landed


def test_the_undoer_says_nothing_to_undo_when_the_slot_is_empty(monkeypatch, note_in_notes):
    """One level, consumed on use: undo twice in a row is a plain sentence, not
    an error and not a bounce between two versions."""
    monkeypatch.setattr(nodes.undo, "peek", lambda note_id: None)
    state = nodes.undoer(_state("undo", intent="undo"))

    assert "nothing to undo" in state.answer.lower()
    assert [w.mode for w in state.writes] == ["insert"]


def test_the_undoer_leaves_a_note_that_is_gone_completely_alone(monkeypatch, no_such_note):
    """No note means nothing to put back and nowhere to say so — `_reply`
    anchors inside the very note that no longer exists. Popping the slot here
    would spend the one step back on a write that could never land."""
    popped = []
    monkeypatch.setattr(nodes.undo, "peek", lambda note_id: popped.append(note_id))
    state = nodes.undoer(_state("undo", intent="undo"))

    assert state.writes == []
    assert popped == []
    assert "nothing to undo" in state.answer.lower()


def test_undo_in_the_ask_note_asks_which_note_instead_of_guessing(monkeypatch, note_in_notes):
    """The Ask note is the conversation itself, not a thing that was written —
    restoring it would discard the whole conversation and report a "done"
    that has nothing to do with what the user meant. Design doc: ask which
    note rather than guess."""
    popped = []
    monkeypatch.setattr(nodes.undo, "peek", lambda note_id: popped.append(note_id))
    state = State(request="undo", reply_to=(workspace.ASK, workspace.FOLDER, 3), intent="undo")
    out = nodes.undoer(state)

    assert popped == []
    assert out.writes and out.writes[0].mode != "restore"
    assert "which" in out.answer.lower() or "tag me" in out.answer.lower()


def test_the_undoer_declines_anything_that_is_not_an_undo(note_in_notes):
    state = nodes.undoer(_state("what did I park where", intent="question"))
    assert state.writes == [] and state.answer == ""


# --------------------------------------------------------------- the organizer

def test_the_organizer_rewrites_a_note_that_has_earned_it(monkeypatch, note_in_notes):
    monkeypatch.setattr(nodes.rewrite, "allowed", lambda note_id: True)
    brain = FakeBrain()
    state = nodes.organizer(_state("clean this up", intent="organize"), brain=brain)

    assert state.writes[0].mode == "replace"
    assert state.writes[0].rewrite_allowed is True
    assert state.writes[0].folder == FOLDER
    assert ("text", "smart") in brain.calls, "a whole note is Super's job, not Nano's"


def test_an_empty_answer_never_becomes_the_note(monkeypatch, note_in_notes):
    """`brain.ask` can come back empty after its one retry, with no exception —
    see brain.py. That must never overwrite a note outright; undo can bring
    the original back, but it must never be the only thing standing between a
    bad answer and a wiped note."""
    monkeypatch.setattr(nodes.rewrite, "allowed", lambda note_id: True)
    state = nodes.organizer(_state("clean this up", intent="organize"), brain=FakeBrain(answer=""))

    assert not any(w.mode == "replace" for w in state.writes)
    assert "couldn't tidy" in state.answer.lower() or "left it alone" in state.answer.lower()


def test_a_suspiciously_short_answer_never_becomes_the_note(monkeypatch, note_in_notes):
    """Not just empty — a model that summarised the note away instead of
    tidying it is just as dangerous on the one path that can overwrite a
    user's own words outright."""
    monkeypatch.setattr(nodes.rewrite, "allowed", lambda note_id: True)
    state = nodes.organizer(_state("clean this up", intent="organize"),
                            brain=FakeBrain(answer="Garages."))

    assert not any(w.mode == "replace" for w in state.writes)


def test_a_rewrite_is_one_write_so_undo_still_holds_the_original(monkeypatch, note_in_notes):
    """Every successful write saves the note's prior body, and there is only one
    slot per note. A separate reply write straight after the rewrite would
    overwrite it with the *cleaned* body, and "undo that" would hand back her
    own version — the safety net gone exactly where it is needed most. So her
    turn rides along inside the rewrite."""
    monkeypatch.setattr(nodes.rewrite, "allowed", lambda note_id: True)
    state = nodes.organizer(_state("clean this up", intent="organize"), brain=FakeBrain())

    assert len(state.writes) == 1
    assert conversation.SIGNATURE in state.writes[0].markdown


def test_a_rewrite_never_carries_the_tag_line_through(monkeypatch, note_in_notes):
    """A rewrite replaces the whole note, so a `@notron clean this up` copied
    through by the model would sit there unanswered — and the scanner would
    hand it straight back on the next poll, forever."""
    monkeypatch.setattr(nodes.rewrite, "allowed", lambda note_id: True)
    brain = FakeBrain(answer="## Garages\n@notron clean this up\n- 12 Trinity")
    state = nodes.organizer(_state("clean this up", intent="organize"), brain=brain)

    assert "@notron" not in state.writes[0].markdown


def test_the_organizer_adds_below_and_asks_when_the_note_has_not(monkeypatch, note_in_notes):
    """Invariant #2 by default: her cleaned version goes under what they wrote,
    never over it, plus the one line that offers the other way."""
    monkeypatch.setattr(nodes.rewrite, "allowed", lambda note_id: False)
    state = nodes.organizer(_state("clean this up", intent="organize"), brain=FakeBrain())

    assert [w.mode for w in state.writes] == ["insert"]
    assert nodes.ORGANIZE_ASK_MARKER in state.answer
    assert nodes.ORGANIZE_ASK_MARKER in state.writes[0].markdown


def test_the_ask_is_recognisable_again_once_it_is_in_the_note(monkeypatch, note_in_notes):
    """The marker is how a bare `yes` next pass is told apart from a `yes` about
    anything else, so it has to survive being written into the note and read
    back out of it."""
    monkeypatch.setattr(nodes.rewrite, "allowed", lambda note_id: False)
    state = nodes.organizer(_state("clean this up", intent="organize"), brain=FakeBrain())

    landed = markup.render(NOTE, BODY_MD) + markup.to_html(state.writes[0].markdown)
    assert nodes._offer_precedes(landed, len(nodes.notedoc.texts(landed)))


def _after(note_in_notes) -> int:
    """The block index a `yes` typed right now would sit at — the end of the
    note's body exactly as it stands, since a test builds `note_in_notes` up
    to but not including the `yes` line itself (that's `state.request`, not
    stored in the body): the note's body never gets ahead of what she has
    actually read."""
    return len(nodes.notedoc.texts(note_in_notes["body"]))


def test_a_yes_under_the_ask_opts_that_one_note_in(monkeypatch, tmp_path, note_in_notes):
    monkeypatch.setattr(rewrite, "STATE", tmp_path / "rewrite.json")
    note_in_notes["body"] += markup.to_html(conversation.turn(f"Here you go.\n\n{nodes.ORGANIZE_ASK}"))

    state = nodes.organizer(_state("yes", intent="organize", after=_after(note_in_notes)),
                            brain=NoBrain())

    assert rewrite.allowed("n1") is True
    assert [w.mode for w in state.writes] == ["insert"], "a receipt, not a rewrite"
    assert "keep this note clean" in state.answer.lower()


def test_saying_yes_does_not_also_rewrite_the_note_on_the_spot(monkeypatch, tmp_path, note_in_notes):
    """The offer is about *next* time. Rewriting the moment they agree would be
    a rewrite they never actually saw the before/after of."""
    monkeypatch.setattr(rewrite, "STATE", tmp_path / "rewrite.json")
    note_in_notes["body"] += markup.to_html(conversation.turn(nodes.ORGANIZE_ASK))

    state = nodes.organizer(_state("yes", intent="organize", after=_after(note_in_notes)),
                            brain=NoBrain())

    assert not any(w.mode == "replace" for w in state.writes)


def test_a_yes_that_answers_something_else_is_not_consent_to_rewrite(monkeypatch, tmp_path,
                                                                     note_in_notes):
    """She never offered on this note, so a `yes` here is answering something
    else entirely — the router's guess was wrong, and it is handed back to be
    answered normally rather than turned into a rewrite proposal."""
    monkeypatch.setattr(rewrite, "STATE", tmp_path / "rewrite.json")
    note_in_notes["body"] += markup.to_html(conversation.turn("Want me to book the 6pm one?"))

    state = nodes.organizer(_state("yes", intent="organize", after=_after(note_in_notes)),
                            brain=NoBrain())

    assert rewrite.allowed("n1") is False
    assert state.writes == []
    assert state.intent == "question", "the writer should answer it like any other turn"


def test_an_old_offer_further_up_the_note_is_not_a_live_one(monkeypatch, tmp_path, note_in_notes):
    """Only the turn right above the `yes` counts. An offer from weeks ago,
    buried under something she asked since, must not turn today's unrelated
    `yes` into consent — even though she did genuinely offer, earlier."""
    monkeypatch.setattr(rewrite, "STATE", tmp_path / "rewrite.json")
    note_in_notes["body"] += markup.to_html(conversation.turn(nodes.ORGANIZE_ASK))
    note_in_notes["body"] += markup.to_html(conversation.turn("Want me to book the 6pm one?"))

    state = nodes.organizer(_state("yes", intent="organize", after=_after(note_in_notes)),
                            brain=NoBrain())

    assert rewrite.allowed("n1") is False


def test_the_organizer_leaves_a_note_that_is_gone_alone(no_such_note):
    state = nodes.organizer(_state("clean this up", intent="organize"), brain=NoBrain())
    assert state.writes == []
    assert state.answer


def test_the_organizer_declines_anything_that_is_not_an_organize(note_in_notes):
    state = nodes.organizer(_state("what did I park where", intent="question"), brain=NoBrain())
    assert state.writes == [] and state.answer == ""


def test_a_long_note_is_given_room_to_come_back_whole(monkeypatch, note_in_notes):
    """Every other node asks the model for a reply; here the reply *is* the
    note. `brain.ask` cannot tell an answer that finished from one that ran out
    of room, so a fixed budget would hand back a long note quietly missing its
    tail — and on an opted-in note that lands straight over the original."""
    monkeypatch.setattr(nodes.rewrite, "allowed", lambda note_id: False)
    brain = FakeBrain()

    nodes.organizer(_state("clean this up", intent="organize"), brain=brain)
    assert brain.last["max_tokens"] == nodes.MIN_CLEAN_TOKENS, "a short note, a floor"

    note_in_notes["body"] = markup.render(NOTE, "another garage on Trinity Place\n" * 400)
    nodes.organizer(_state("clean this up", intent="organize"), brain=brain)
    assert nodes.MIN_CLEAN_TOKENS < brain.last["max_tokens"] <= nodes.MAX_CLEAN_TOKENS


def test_the_organizer_is_told_to_keep_every_fact_and_add_none():
    system = nodes.ORGANIZER_SYSTEM.lower()
    assert "keep every fact" in system
    assert "invent" in system
    assert "title" in system, "the title is re-added on write — a repeat would double it"


# ------------------------------------------------------- neither node is a writer

def test_the_writer_stands_aside_for_both_new_nodes():
    """Both already said their piece where it was asked. A second reply from the
    writer would double up — and cost a smart model call to do it."""
    for intent in ("undo", "organize"):
        state = State(request="x", intent=intent, answer="already said")
        out = nodes.writer(state, brain=NoBrain())
        assert out.writes == [], intent


def test_neither_node_writes_to_notes_itself(monkeypatch, note_in_notes):
    """Invariant #3: the model proposes, the Guard judges, the Executor applies.
    Nothing here may touch a note directly."""
    monkeypatch.setattr(nodes.notes, "write_body",
                        lambda note_id, body: pytest.fail("a node wrote to Notes directly"))
    monkeypatch.setattr(nodes.notes, "create_note",
                        lambda folder, body: pytest.fail("a node created a note directly"))
    monkeypatch.setattr(nodes.rewrite, "allowed", lambda note_id: True)
    monkeypatch.setattr(nodes.undo, "peek", lambda note_id: _snapshot("<div>old</div>"))

    nodes.organizer(_state("clean this up", intent="organize"), brain=FakeBrain())
    nodes.undoer(_state("undo", intent="undo"))


# ------------------------------------------------------- executor dispatch

class FakeExecutor:
    """Records what the graph asked for, the way test_graph.py does."""

    applied: list = []

    def __init__(self, dry_run=False):
        pass

    def apply_write(self, write):
        detail = write.markdown if write.mode == 'restore' else (
            write.rewrite_allowed if write.mode == 'replace' else None)
        FakeExecutor.applied.append((write.mode, write.title, detail))
        from notron.executor import WriteResult
        return WriteResult(True, 'written')


@pytest.fixture
def dispatched(monkeypatch):
    FakeExecutor.applied = []
    monkeypatch.setattr(nodes, "Executor", FakeExecutor)
    return FakeExecutor.applied


def test_a_restore_write_reaches_the_executors_restore(dispatched):
    from notron.state import Write

    nodes.executor(State(writes=[Write(title=NOTE, folder=FOLDER, mode="restore",
                                       markdown="<div>old</div>")]))
    assert dispatched == [("restore", NOTE, "<div>old</div>")]


def test_the_opt_in_reaches_the_guard_and_only_when_it_was_set(dispatched):
    """`rewrite_allowed` is the one thing standing between invariant #2 and a
    rewritten note, so it has to arrive exactly as the organizer decided it."""
    from notron.state import Write

    nodes.executor(State(writes=[
        Write(title=NOTE, folder=FOLDER, mode="replace", markdown="x", rewrite_allowed=True),
        Write(title=workspace.TODAY, mode="replace", markdown="y"),
    ]))
    assert dispatched == [("replace", NOTE, True), ("replace", workspace.TODAY, False)]


def test_new_undo_command_offers_recovery_without_consuming_snapshot(note_in_notes):
    from notron.requests import revision
    undo.save('n1', '<div>previous words</div>', revision(note_in_notes['body']), 'written')
    undo.promote('n1', 'written')
    snapshot = undo.peek('n1')
    note_in_notes['body'] += '<div>@notron undo</div>'
    state = nodes.undoer(_state('undo', intent='undo'))
    assert [w.mode for w in state.writes] == ['insert']
    assert 'recovery copy' in state.answer
    assert snapshot.snapshot_id in state.answer
    assert state.writes[0].undo_reply
    assert undo.peek('n1') == snapshot


def test_copy_confirmation_binds_exact_snapshot_and_source(note_in_notes):
    undo.save('n1', '<div>previous words</div>')
    snapshot = undo.peek('n1')
    command = 'undo recovery copy ' + snapshot.snapshot_id
    note_in_notes['body'] += '<div>@notron ' + command + '</div>'
    state = nodes.undoer(_state(command, intent='undo'))
    assert state.writes[0].recovery_note_id == 'n1'
    assert state.writes[0].snapshot_id == snapshot.snapshot_id
    assert state.writes[0].note_id is None
    assert 'previous words' in state.writes[0].markdown
    assert undo.peek('n1') == snapshot


def test_wrong_copy_confirmation_does_not_create(note_in_notes):
    undo.save('n1', 'earlier')
    state = nodes.undoer(_state('undo recovery copy wrong-snapshot', intent='undo'))
    assert all(not w.recovery_note_id for w in state.writes)
# ------------------------------------------ what she has not seen (Stage A)

def test_she_is_told_about_a_file_she_cannot_read_yet():
    """Apple Notes keeps an attachment out of the body entirely, so without
    this she answers about a photo as if the photo were not there — which
    sounds exactly like having looked."""
    from notron.attachments import Attachment
    s = State(request="what does this say?", here="see attached", source_note_id="n1",
              carried=[Attachment(id="a1", name="whiteboard.png", kind="image")])
    p = '\n'.join(part.text for part in nodes._prompt(s))
    assert "whiteboard.png" in p
    assert "cannot read" in p.lower()


def test_a_note_with_no_files_says_nothing_about_files():
    p = '\n'.join(part.text for part in nodes._prompt(State(request="what's on today?")))
    assert "cannot read" not in p.lower()


def test_a_text_file_in_the_note_is_read_and_stops_being_a_file_she_cannot_read(monkeypatch):
    """Once she can actually read the thing, it must leave the "you have NOT
    seen these" list — or she tells the user she is blind to a file she just
    quoted."""
    from notron import attachments
    att = attachments.Attachment(id="a1", name="log.txt", kind="text")
    monkeypatch.setattr(attachments, "read_text", lambda a: "line one\nline two")

    s = State(request="what does the log say?", carried=[att],
              reply_to=("Deploys", "Notes", 3), source_note_id="n1")
    s = nodes.retriever(s, brain=None)

    assert any("log.txt" in c.text and "line two" in c.text for c in s.context)
    assert s.carried == []
    assert "cannot read" not in '\n'.join(part.text for part in nodes._prompt(s)).lower()


def test_a_file_she_still_cannot_read_stays_named(monkeypatch):
    from notron import attachments
    s = State(request="what is in the photo?",
              carried=[attachments.Attachment(id="a1", name="board.png", kind="image")])
    s = nodes.retriever(s, brain=None)
    assert s.context == []
    assert [a.name for a in s.carried] == ["board.png"]


def test_a_file_that_will_not_open_is_named_rather_than_silently_dropped(monkeypatch):
    """Notes busy, a deleted attachment, a bad decode — she keeps saying the
    file is there. Losing it from the prompt is the one outcome that reads as
    having looked."""
    from notron import attachments
    att = attachments.Attachment(id="a1", name="log.txt", kind="text")
    monkeypatch.setattr(attachments, "read_text",
                        lambda a: (_ for _ in ()).throw(RuntimeError("Notes is busy")))
    s = nodes.retriever(State(request="what does it say?", carried=[att]), brain=None)
    assert [a.name for a in s.carried] == ["log.txt"]


def test_a_picture_in_the_note_is_looked_at_and_quoted(monkeypatch):
    from notron import attachments
    att = attachments.Attachment(id="a1", name="board.png", kind="image")
    monkeypatch.setattr(attachments, "describe",
                        lambda a, brain, **kw: "Text: BUY MILK. A whiteboard.")

    s = State(request="what's on the board?", carried=[att],
              reply_to=("Kitchen", "Notes", 1))
    s = nodes.retriever(s, brain=FakeBrain())

    assert any("board.png" in c.text and "BUY MILK" in c.text for c in s.context)
    assert s.carried == []


def test_with_no_brain_behind_her_a_picture_is_named_not_guessed_at():
    from notron import attachments
    s = nodes.retriever(
        State(request="what's on the board?",
              carried=[attachments.Attachment(id="a1", name="board.png", kind="image")]),
        brain=None)
    assert [a.name for a in s.carried] == ["board.png"]


def test_only_so_many_pictures_are_looked_at_for_one_question(monkeypatch):
    """A note with fifteen screenshots would otherwise be fifteen vision calls
    at ~3.5s each inside a listener poll — a minute with Notes blocked and a
    bill to match. The rest stay named as unseen, which is the honest answer."""
    from notron import attachments
    looked = []
    monkeypatch.setattr(attachments, "describe",
                        lambda a, brain, **kw: looked.append(a.name) or f"a picture of {a.name}")

    shots = [attachments.Attachment(id=f"a{i}", name=f"shot{i}.png", kind="image")
             for i in range(nodes.MAX_LOOKS + 3)]
    s = nodes.retriever(State(request="what do these show?", carried=shots),
                        brain=FakeBrain())

    assert len(looked) == nodes.MAX_LOOKS
    assert len(s.context) == nodes.MAX_LOOKS
    assert len(s.carried) == 3
    assert "cannot read" in '\n'.join(part.text for part in nodes._prompt(s)).lower()


# --- a blind calendar is not a free day ------------------------------------
#
# Measured 2026-09-06: from the background listener EventKit reports zero
# calendars and zero events, with no error, because that process has never
# been granted access and cannot ask for it (docs/spikes/2026-09-06-eventkit-
# request-under-osascript.md). Every `agenda:` line in .notron/listen.log read
# "125 chars of real commitments" — the exact length of "Nothing in the
# calendar today" plus "Nothing outstanding in Reminders". She planned around
# a day she could not see, and said nothing.

def _blind(app="Calendar"):
    from notron.permissions import Check
    return lambda: [
        Check(app, False, "is denied", "System Settings → Privacy & Security"),
        Check("Reminders" if app == "Calendar" else "Calendar", True, "full access", ""),
    ]


def _sighted():
    from notron.permissions import Check
    return lambda: [Check("Calendar", True, "full access", ""),
                    Check("Reminders", True, "full access", "")]


def test_an_unreadable_calendar_is_not_reported_as_a_free_day():
    state = State(intent="schedule")
    nodes.agenda(state, checker=_blind("Calendar"),
                 reader=lambda: ("Nothing in the calendar today.",
                                 "Nothing outstanding in Reminders."))
    assert "Nothing in the calendar today." not in state.agenda
    assert "cannot read" in state.agenda.lower()
    assert "is denied" in state.agenda


def test_an_unreadable_reminders_list_is_not_reported_as_nothing_to_do():
    state = State(intent="plan")
    nodes.agenda(state, checker=_blind("Reminders"),
                 reader=lambda: ("Nothing in the calendar today.",
                                 "Nothing outstanding in Reminders."))
    assert "Nothing outstanding in Reminders." not in state.agenda
    assert "cannot read" in state.agenda.lower()


def test_a_readable_but_genuinely_empty_day_still_says_nothing():
    """The honest empty day must survive. Only an unreadable one changes."""
    state = State(intent="schedule")
    nodes.agenda(state, checker=_sighted(),
                 reader=lambda: ("Nothing in the calendar today.",
                                 "Nothing outstanding in Reminders."))
    assert "Nothing in the calendar today." in state.agenda
    assert "cannot read" not in state.agenda.lower()


def test_what_she_did_manage_to_see_is_still_shown_alongside_the_warning():
    state = State(intent="schedule")
    nodes.agenda(state, checker=_blind("Calendar"),
                 reader=lambda: ("- 09:00 Standup", "Nothing outstanding in Reminders."))
    assert "Standup" in state.agenda
    assert "cannot read" in state.agenda.lower()


def test_a_permission_check_that_itself_fails_does_not_lose_the_agenda():
    def boom():
        raise RuntimeError("no osascript here")
    state = State(intent="schedule")
    nodes.agenda(state, checker=boom,
                 reader=lambda: ("- 09:00 Standup", "Nothing outstanding in Reminders."))
    assert "Standup" in state.agenda
    assert 'access could not be verified' in state.agenda
    assert 'Nothing outstanding' not in state.agenda


def test_a_question_about_herself_is_not_sent_to_the_web():
    """"what can you do" was classified as a world question, so a safety net
    forced a search, and she answered with vendor marketing about AI assistants
    in general — Norton, Jamf, SAP — while her own instructions sat unused.

    The gate is plain code, like FILE_WORDS: what she is does not need looking up.
    `brain=None` is safe here because a matched request returns before any model
    is consulted, which is the point of the gate.
    """
    from notron import nodes, state as state_module

    for question in ('what can you do', 'Who are you?', 'what are your capabilities',
                     'tell me about yourself', 'so what can you do exactly?'):
        result = nodes.router(state_module.State(request=question), brain=None)
        assert result.about_self is True, question
        assert result.needs_web is False, f'{question} must not be searched'
        assert result.intent == 'question'


def test_questions_that_merely_start_like_a_self_question_are_left_alone():
    """The gate is anchored to the whole request, because "what can you do about
    my reminder" is about the user's reminder, not about her. Checked on the
    pattern rather than through `router`, which would go on to consult a model."""
    from notron.nodes import SELF_WORDS

    for question in ('what can you do about my reminder',
                     'who are you meeting tomorrow',
                     'what are your reminders today'):
        assert not SELF_WORDS.search(question), question


def test_her_own_surfaces_reach_the_writer_without_a_search():
    """She answers from what she is, not from a citation."""
    from notron import nodes, state as state_module, workspace

    result = state_module.State(request='what can you do', about_self=True)
    text = ' '.join(p.text for p in nodes._prompt(result))
    assert workspace.ASK in text
    assert 'not about AI assistants in general' in text
