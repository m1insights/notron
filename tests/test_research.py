import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from notron import graph, nodes, research
from notron.state import State


def test_no_search_happens_unless_the_router_asked_for_one():
    state = State(request="what's on today?", needs_web=False)
    assert nodes.researcher(state) is state
    assert state.web == []


def test_a_missing_key_costs_the_web_not_the_answer(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    state = State(request="who won last night?", needs_web=True)
    nodes.researcher(state)
    assert state.web == []
    assert "Tavily key" in state.trace[-1]


def test_a_failed_search_costs_the_web_not_the_answer(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "x")
    monkeypatch.setattr(research, "search",
                        lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
    state = State(request="who won last night?", needs_web=True)
    nodes.researcher(state)
    assert state.web == []
    assert "answering without it" in state.trace[-1]


def test_findings_reach_the_writer_as_context(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "x")
    monkeypatch.setattr(research, "search", lambda *a, **k: (
        "Fonda Lee won the Aurora Award.",
        [research.Finding("Aurora Awards", "https://example.com", "the 2024 winners were…")],
    ))
    state = State(request="did Fonda Lee win anything?", needs_web=True)
    nodes.researcher(state)
    assert any("Aurora" in w for w in state.web)
    assert "https://example.com" in nodes._prompt(state)


def test_the_researcher_sits_in_the_declared_graph():
    assert "researcher" in graph.NODES
    assert "researcher" in graph.ORDER
    assert any(e.frm == "retriever" and e.to == "researcher" for e in graph.EDGES)
