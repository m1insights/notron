import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from notron import graph, nodes, research
from notron.state import State
from notron.outbound import prepare_outbound


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
    assert "https://example.com" in "\n".join(prepare_outbound("write", nodes._prompt(state)))


def test_the_researcher_sits_in_the_declared_graph():
    assert "researcher" in graph.NODES
    assert "researcher" in graph.ORDER
    assert any(e.frm == "retriever" and e.to == "researcher" for e in graph.EDGES)


def test_journals_outrank_institutions_outrank_content_farms():
    """A Cialis answer once cited ubiehealth.com — a thin AI-content site —
    with the same visual weight as the European Urology trial next to it."""
    assert research.quality("https://pubmed.ncbi.nlm.nih.gov/15661417") == 1
    assert research.quality("https://www.sciencedirect.com/science/article/abs/pii/S03") == 1
    assert research.quality("https://link.springer.com/article/10.1186/x") == 1
    assert research.quality("https://health.clevelandclinic.org/what-is-ashwagandha") == 2
    assert research.quality("https://examine.com/supplements/tongkat-ali/") == 2
    assert research.quality("https://ubiehealth.com/doctors-note/tadalafil") == 3
    assert research.quality("https://random-wellness-blog.io/cialis") == 3


def test_quality_never_raises_on_junk_input():
    assert research.quality("") == 3
    assert research.quality("not a url at all") == 3


def _finding(url):
    return research.Finding(title=url, url=url, snippet="s")


def test_content_farms_are_dropped_when_better_sources_exist(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "x")
    monkeypatch.setattr(research, "search", lambda *a, **k: ("", [
        _finding("https://ubiehealth.com/a"),
        _finding("https://pubmed.ncbi.nlm.nih.gov/1"),
        _finding("https://random-blog.io/b"),
        _finding("https://clevelandclinic.org/c"),
    ]))
    state = State(request="cialis?", needs_web=True)
    nodes.researcher(state)
    joined = "\n".join(state.web)
    assert "pubmed" in joined and "clevelandclinic" in joined
    assert "ubiehealth" not in joined and "random-blog" not in joined
    assert any("dropped" in t for t in state.trace)


def test_a_content_farm_is_kept_when_it_is_most_of_what_came_back(monkeypatch):
    """Filtering must never starve the writer: one journal + one blog is a
    thin answer, not a reason to throw half of it away."""
    monkeypatch.setenv("TAVILY_API_KEY", "x")
    monkeypatch.setattr(research, "search", lambda *a, **k: ("", [
        _finding("https://random-blog.io/only"),
        _finding("https://pubmed.ncbi.nlm.nih.gov/1"),
    ]))
    state = State(request="obscure supplement?", needs_web=True)
    nodes.researcher(state)
    assert "random-blog" in "\n".join(state.web)


def test_journals_come_first_and_at_most_five_findings_pass(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "x")
    monkeypatch.setattr(research, "search", lambda *a, **k: ("", [
        _finding(f"https://blog{i}.io/x") for i in range(4)
    ] + [
        _finding("https://pubmed.ncbi.nlm.nih.gov/1"),
        _finding("https://sciencedirect.com/2"),
        _finding("https://examine.com/3"),
    ]))
    state = State(request="cialis?", needs_web=True)
    nodes.researcher(state)
    findings = [w for w in state.web if not w.startswith("### What the web says")]
    assert len(findings) <= 5
    assert "pubmed" in findings[0] or "sciencedirect" in findings[0]
