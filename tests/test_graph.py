"""The graph is exercised with a stand-in brain so it can be tested without a key."""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from notron import conversation, graph, nodes, workspace
from notron.state import State


class FakeBrain:
    def __init__(self, intent="question", answer="Because you wrote it down on Tuesday."):
        self.intent = intent
        self.answer = answer
        self.calls = []

    def ask_json(self, **kw):
        self.calls.append(("json", kw.get("tier")))
        return {"intent": self.intent, "needs_context": False, "needs_web": False, "why": "test"}

    def ask(self, **kw):
        self.calls.append(("text", kw.get("tier")))
        return self.answer


def _run(intent, **kw):
    brain = FakeBrain(intent=intent)
    return graph.run("when is my shoot?", brain=brain, dry_run=True, **kw), brain


def test_a_question_produces_an_answer_appended_to_the_ask_note():
    state, _ = _run("question")
    assert state.answer
    assert [w.title for w in state.writes] == [workspace.ASK]
    assert state.writes[0].mode == "append"
    # The light rule must open her turn, before the bold signature, so the
    # question and reply read as visually separate — but conversation.py
    # still has to recognise it as answered (see test_conversation.py).
    body = state.writes[0].markdown
    assert body.index(conversation.QA_RULE) < body.index("**Notron:**")


def test_a_plan_replaces_the_week_note():
    brain = FakeBrain(intent="plan")
    state = graph.run("plan my week", brain=brain, dry_run=True)
    assert [w.title for w in state.writes] == [workspace.WEEK]
    assert state.writes[0].mode == "replace"


def test_a_day_plan_replaces_the_today_note():
    brain = FakeBrain(intent="plan")
    state = graph.run("plan my day", brain=brain, dry_run=True)
    assert [w.title for w in state.writes] == [workspace.TODAY]


def test_a_capture_is_remembered_without_paying_for_the_big_model():
    brain = FakeBrain(intent="capture")
    state = graph.run("my sister's birthday is in March", brain=brain, dry_run=True)
    assert workspace.MEMORY in [w.title for w in state.writes]
    assert ("text", "smart") not in brain.calls, "a note to self needs no reasoning"


def test_an_empty_request_halts_the_graph_before_any_write():
    state = graph.run("", brain=FakeBrain(), dry_run=True)
    assert state.writes == []
    assert "halted" in " ".join(state.trace)


def test_the_router_cannot_silently_ignore_something_typed_at_her():
    """The router once classed 'Reply with the single word: ready' as ignore —
    not addressed to the assistant — in the Ask note, where everything is. The
    user saw dead air, and the listener sent the question back to the model on
    every poll, forever."""
    state, _ = _run("ignore")
    assert state.intent == "question"
    assert state.writes, "a message typed at her must always get a visible reply"


def test_routing_runs_on_the_cheap_tier_and_writing_on_the_smart_one():
    state, brain = _run("question")
    assert ("json", "fast") in brain.calls
    assert ("text", "smart") in brain.calls


def test_the_instruction_note_is_always_loaded_before_the_model_is_called():
    state = State(request="x", about="Never schedule me before 9am.")
    prompt = nodes._prompt(state)
    assert prompt.index("standing instructions") < prompt.index("Their request")


def test_every_declared_edge_points_at_a_real_node():
    names = set(graph.NODES)
    for e in graph.EDGES:
        assert e.frm in names and e.to in names


def test_a_capture_does_not_duplicate_a_fact_already_in_memory():
    """The Memory note once collected the same fact eleven times — every capture
    of it appended a fresh bullet instead of checking what was already there."""
    from notron.state import State

    fact = "I am about to read a book. Stranded by AK Duboff."
    state = State(request=fact, intent="capture", memory=f"- {fact}\n")
    state = nodes.writer(state, brain=FakeBrain(intent="capture"))
    assert workspace.MEMORY not in [w.title for w in state.writes], \
        "already-known fact should not be written again"
    assert workspace.ASK in [w.title for w in state.writes], "still acknowledge it was heard"


def test_a_capture_still_leaves_a_visible_reply_or_she_reads_it_forever():
    """A turn answered without a reply in the note looks unanswered on the next
    pass, and she answers it again, and again."""
    brain = FakeBrain(intent="capture")
    state = graph.run("I'm about to read Stranded by AK Duboff", brain=brain, dry_run=True)
    titles = [w.title for w in state.writes]
    assert workspace.MEMORY in titles, "the fact should be remembered"
    assert workspace.ASK in titles, "and acknowledged where it was said"
    assert state.answer


def test_a_reply_meant_for_a_spot_in_a_note_is_actually_inserted_there():
    """The dispatch once fell through to 'replace' for insert writes, and the
    Guard refused every one of them — silently, from the user's point of view."""
    from notron import nodes
    from notron.state import State, Write

    applied = []

    class FakeExecutor:
        def __init__(self, dry_run=False):
            pass

        def insert(self, title, md, *, after, folder, anchor=""):
            applied.append(("insert", title, after))
            return type("R", (), {"ok": True, "reason": "written"})()

        def append(self, title, md, *, folder):
            applied.append(("append", title, None))
            return type("R", (), {"ok": True, "reason": "written"})()

        def replace(self, title, md, *, folder):
            applied.append(("replace", title, None))
            return type("R", (), {"ok": True, "reason": "written"})()

    nodes.Executor = FakeExecutor
    try:
        state = State(writes=[Write(title="Book idea", markdown="x", mode="insert",
                                    folder="Notes", after=7)])
        nodes.executor(state)
    finally:
        from notron.executor import Executor as Real
        nodes.Executor = Real

    assert applied == [("insert", "Book idea", 7)]


def test_she_is_allowed_to_know_things_that_are_not_in_your_notes():
    """She once refused to compare two novels because neither appeared in the
    user's notes. Notes are for facts about the user, not a limit on what she
    may know."""
    from notron import nodes

    prompt = nodes.WRITER_SYSTEM.lower()
    assert "about the world" in prompt
    assert "do not refuse a general question" in prompt


def test_your_notes_are_never_treated_as_evidence_about_the_world():
    from notron import nodes

    writer = nodes.WRITER_SYSTEM.lower()
    assert "never evidence about a book" in writer
    assert "never quote a note back word for word" in writer

    router = nodes.ROUTER_SYSTEM.lower()
    assert "needs_context is false for questions about the world" in router


class SchedulingBrain(FakeBrain):
    """A brain that routes to `remind` and extracts a structured action."""

    def __init__(self, intent="remind", when=None):
        super().__init__(intent=intent)
        from datetime import datetime, timedelta
        self.when = when or (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT09:00")

    def ask_json(self, **kw):
        self.calls.append(("json", kw.get("tier")))
        if "extract" in kw.get("system", "").lower():
            return {"kind": "reminder", "op": "create",
                    "title": "Call the pharmacy", "when": self.when}
        return {"intent": self.intent, "needs_context": False, "needs_web": False, "why": "t"}


def test_a_reminder_request_produces_an_action_not_a_note_write():
    brain = SchedulingBrain()
    state = graph.run("remind me to call the pharmacy", brain=brain, dry_run=True)
    assert [a.title for a in state.actions] == ["Call the pharmacy"]
    assert state.actions[0].kind == "reminder"


def test_extraction_runs_on_the_cheap_tier():
    brain = SchedulingBrain()
    graph.run("remind me to call the pharmacy", brain=brain, dry_run=True)
    assert ("json", "fast") in brain.calls
    assert ("json", "smart") not in brain.calls


def test_the_reply_says_what_actually_happened_not_what_was_intended():
    """The whole reason `doer` runs before `writer`: if the Guard blocks the
    action, she must not have already claimed she set it."""
    from datetime import datetime, timedelta
    past = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%dT09:00")
    brain = SchedulingBrain(when=past)
    state = graph.run("remind me to call the pharmacy", brain=brain, dry_run=True)
    assert "Reminder set:" not in state.answer, "must not claim success on a blocked action"
    assert "didn't set" in state.answer.lower()
    assert any("✗" in r for r in state.results)


def test_a_scheduling_reply_costs_no_smart_model_call():
    """Confirming a reminder is a fact, not an essay. It is composed in code."""
    brain = SchedulingBrain()
    graph.run("remind me to call the pharmacy", brain=brain, dry_run=True)
    assert ("text", "smart") not in brain.calls


def test_planning_the_day_reads_the_real_calendar_first(monkeypatch):
    from notron import nodes

    monkeypatch.setattr(nodes, "_agenda_text", lambda: "- 09:30 Standup")
    brain = FakeBrain(intent="plan")
    state = graph.run("plan my day", brain=brain, dry_run=True)
    assert "Standup" in state.agenda


def test_a_calendar_that_cannot_be_read_never_stops_the_plan(monkeypatch):
    """Automation approval can be revoked at any time. A missing calendar costs
    her context, not the whole morning."""
    from notron import nodes

    def boom():
        raise RuntimeError("not approved")

    monkeypatch.setattr(nodes, "_agenda_text", boom)
    brain = FakeBrain(intent="plan")
    state = graph.run("plan my day", brain=brain, dry_run=True)
    assert state.writes
    assert any("calendar" in t.lower() for t in state.trace)


def test_the_declared_order_still_matches_the_declared_edges():
    assert set(graph.ORDER) == set(graph.NODES)
    for e in graph.EDGES:
        assert graph.ORDER.index(e.frm) < graph.ORDER.index(e.to)


def _a_note_of_theirs(monkeypatch, body="<div>Parking Garages</div><div>12 Trinity — $18</div>"):
    """Every `notes` lookup in the graph resolves, so the walk can be exercised
    end to end without the Notes app."""
    from notron.notes import Note

    monkeypatch.setattr(nodes.notes, "find_note",
                        lambda folder, title: Note(id="n1", title=title, folder=folder,
                                                   modified="x"))
    monkeypatch.setattr(nodes.notes, "read_body", lambda note_id: body)


def test_undo_walks_the_whole_graph_without_ever_asking_the_model(monkeypatch):
    """Tagged on one of their own notes, "undo" is answered by plain code from
    end to end: the router reads the word, the undoer pops the saved copy, and
    the writer stands aside instead of paying for a second reply."""
    from notron import undo

    _a_note_of_theirs(monkeypatch)
    monkeypatch.setattr(undo, "pop", lambda note_id: "<div>the way it was</div>")
    brain = FakeBrain()
    state = graph.run("undo", brain=brain, trigger="notes", dry_run=True,
                      reply_to=("Parking Garages", "Notes", 3))

    assert [w.mode for w in state.writes] == ["restore"]
    assert brain.calls == [], "nothing here needs a model"


def test_a_tidy_up_lands_below_the_note_until_that_note_is_opted_in(monkeypatch):
    """Invariant #2 end to end: routed in code, cleaned by Super, and still only
    *added* to a note nobody has opted into rewrite-in-place."""
    from notron import rewrite

    _a_note_of_theirs(monkeypatch)
    monkeypatch.setattr(rewrite, "allowed", lambda note_id: False)
    brain = FakeBrain()
    state = graph.run("clean this up", brain=brain, trigger="notes", dry_run=True,
                      reply_to=("Parking Garages", "Notes", 3))

    assert [w.mode for w in state.writes] == ["insert"]
    assert ("json", "fast") not in brain.calls, "the router read the words itself"
    assert brain.calls.count(("text", "smart")) == 1, "cleaned once, not once per node"


def test_the_planner_is_told_not_to_plan_over_a_real_appointment():
    from notron import nodes

    system = nodes.PLANNER_SYSTEM.lower()
    assert "calendar" in system
    assert "already" in system
