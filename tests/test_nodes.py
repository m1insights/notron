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
    monkeypatch.setattr(nodes.notes, "read_body", lambda note_id: live["body"])
    return live


@pytest.fixture
def no_such_note(monkeypatch):
    monkeypatch.setattr(nodes.notes, "find_note", lambda folder, title: None)
    monkeypatch.setattr(nodes.notes, "read_body",
                        lambda note_id: pytest.fail("read a note that does not exist"))


def _state(request="", **kw):
    return State(request=request, reply_to=(NOTE, FOLDER, 3), **kw)


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
    state = State(request="clean this up", reply_to=(workspace.ASK, workspace.FOLDER, 2))
    assert nodes.router(state, brain=brain).intent == "question"


def test_a_yes_in_her_own_ask_note_is_not_consent_to_rewrite_it():
    brain = FakeBrain()
    state = State(request="yes", reply_to=(workspace.ASK, workspace.FOLDER, 2))
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
    monkeypatch.setattr(nodes.undo, "pop", lambda note_id: "<div>the user's own words</div>")
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
    monkeypatch.setattr(nodes.undo, "pop", lambda note_id: "<div>old</div>")
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
    monkeypatch.setattr(nodes.undo, "pop", lambda note_id: old_body)
    state = nodes.undoer(_state("undo", intent="undo"))

    landed = state.writes[0].markdown
    assert conversation.unanswered(landed, ignore=(NOTE,), require_tag=True) == []
    assert "✓ " in landed and "put back" in landed


def test_the_undoer_says_nothing_to_undo_when_the_slot_is_empty(monkeypatch, note_in_notes):
    """One level, consumed on use: undo twice in a row is a plain sentence, not
    an error and not a bounce between two versions."""
    monkeypatch.setattr(nodes.undo, "pop", lambda note_id: None)
    state = nodes.undoer(_state("undo", intent="undo"))

    assert "nothing to undo" in state.answer.lower()
    assert [w.mode for w in state.writes] == ["insert"]


def test_the_undoer_leaves_a_note_that_is_gone_completely_alone(monkeypatch, no_such_note):
    """No note means nothing to put back and nowhere to say so — `_reply`
    anchors inside the very note that no longer exists. Popping the slot here
    would spend the one step back on a write that could never land."""
    popped = []
    monkeypatch.setattr(nodes.undo, "pop", lambda note_id: popped.append(note_id))
    state = nodes.undoer(_state("undo", intent="undo"))

    assert state.writes == []
    assert popped == []
    assert "nothing to undo" in state.answer.lower()


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
    assert nodes.ORGANIZE_ASK_MARKER in nodes._her_last_turn(landed)


def test_a_yes_under_the_ask_opts_that_one_note_in(monkeypatch, tmp_path, note_in_notes):
    monkeypatch.setattr(rewrite, "STATE", tmp_path / "rewrite.json")
    note_in_notes["body"] += markup.to_html(conversation.turn(f"Here you go.\n\n{nodes.ORGANIZE_ASK}"))

    state = nodes.organizer(_state("yes", intent="organize"), brain=NoBrain())

    assert rewrite.allowed("n1") is True
    assert [w.mode for w in state.writes] == ["insert"], "a receipt, not a rewrite"
    assert "keep this note clean" in state.answer.lower()


def test_saying_yes_does_not_also_rewrite_the_note_on_the_spot(monkeypatch, tmp_path, note_in_notes):
    """The offer is about *next* time. Rewriting the moment they agree would be
    a rewrite they never actually saw the before/after of."""
    monkeypatch.setattr(rewrite, "STATE", tmp_path / "rewrite.json")
    note_in_notes["body"] += markup.to_html(conversation.turn(nodes.ORGANIZE_ASK))

    state = nodes.organizer(_state("yes", intent="organize"), brain=NoBrain())

    assert not any(w.mode == "replace" for w in state.writes)


def test_a_yes_that_answers_something_else_is_not_consent_to_rewrite(monkeypatch, tmp_path,
                                                                     note_in_notes):
    """She never offered on this note, so a `yes` here is answering something
    else entirely — the router's guess was wrong, and it is handed back to be
    answered normally rather than turned into a rewrite proposal."""
    monkeypatch.setattr(rewrite, "STATE", tmp_path / "rewrite.json")
    note_in_notes["body"] += markup.to_html(conversation.turn("Want me to book the 6pm one?"))

    state = nodes.organizer(_state("yes", intent="organize"), brain=NoBrain())

    assert rewrite.allowed("n1") is False
    assert state.writes == []
    assert state.intent == "question", "the writer should answer it like any other turn"


def test_an_old_offer_further_up_the_note_is_not_a_live_one(monkeypatch, tmp_path, note_in_notes):
    """Only the last thing she said counts. An offer from weeks ago, already
    answered and buried, must not turn today's unrelated `yes` into consent."""
    monkeypatch.setattr(rewrite, "STATE", tmp_path / "rewrite.json")
    note_in_notes["body"] += markup.to_html(conversation.turn(nodes.ORGANIZE_ASK))
    note_in_notes["body"] += markup.to_html(conversation.turn("Want me to book the 6pm one?"))

    state = nodes.organizer(_state("yes", intent="organize"), brain=NoBrain())

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
    monkeypatch.setattr(nodes.undo, "pop", lambda note_id: "<div>old</div>")

    nodes.organizer(_state("clean this up", intent="organize"), brain=FakeBrain())
    nodes.undoer(_state("undo", intent="undo"))


# ------------------------------------------------------- executor dispatch

class FakeExecutor:
    """Records what the graph asked for, the way test_graph.py does."""

    applied: list = []

    def __init__(self, dry_run=False):
        pass

    def insert(self, title, md, *, after, folder, anchor=""):
        FakeExecutor.applied.append(("insert", title, None))
        return type("R", (), {"ok": True, "reason": "written"})()

    def append(self, title, md, *, folder):
        FakeExecutor.applied.append(("append", title, None))
        return type("R", (), {"ok": True, "reason": "written"})()

    def replace(self, title, md, *, folder, rewrite_allowed=False):
        FakeExecutor.applied.append(("replace", title, rewrite_allowed))
        return type("R", (), {"ok": True, "reason": "written"})()

    def restore(self, title, body, *, folder):
        FakeExecutor.applied.append(("restore", title, body))
        return type("R", (), {"ok": True, "reason": "written"})()


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
