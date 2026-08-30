"""The graph is exercised with a stand-in brain so it can be tested without a key."""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from juno import graph, nodes, workspace
from juno.state import State


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


def test_ignore_halts_the_graph_before_any_write():
    state, _ = _run("ignore")
    assert state.writes == []
    assert "halted" in " ".join(state.trace)


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
    from juno import nodes
    from juno.state import State, Write

    applied = []

    class FakeExecutor:
        def __init__(self, dry_run=False):
            pass

        def insert(self, title, md, *, after, folder):
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
        from juno.executor import Executor as Real
        nodes.Executor = Real

    assert applied == [("insert", "Book idea", 7)]


def test_she_is_allowed_to_know_things_that_are_not_in_your_notes():
    """She once refused to compare two novels because neither appeared in the
    user's notes. Notes are for facts about the user, not a limit on what she
    may know."""
    from juno import nodes

    prompt = nodes.WRITER_SYSTEM.lower()
    assert "about the world" in prompt
    assert "do not refuse a general question" in prompt


def test_your_notes_are_never_treated_as_evidence_about_the_world():
    from juno import nodes

    writer = nodes.WRITER_SYSTEM.lower()
    assert "never evidence about a book" in writer
    assert "never quote a note back word for word" in writer

    router = nodes.ROUTER_SYSTEM.lower()
    assert "needs_context is false for questions about the world" in router
