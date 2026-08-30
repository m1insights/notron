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


def test_a_capture_is_appended_to_memory_and_costs_no_writer_call():
    brain = FakeBrain(intent="capture")
    state = graph.run("my sister's birthday is in March", brain=brain, dry_run=True)
    assert [w.title for w in state.writes] == [workspace.MEMORY]
    assert ("text", "smart") not in brain.calls


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
