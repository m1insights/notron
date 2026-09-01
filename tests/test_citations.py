"""Fabricated citations — pinned to real failures from `.notron/listen.log`.

Asked "what's the deal with Cognizin?", Notron cited two papers with DOI
links without ever searching the web: one DOI was wrong (404), the other
paper could not be found at all. Asked about caffeine + theanine, the router
called it "general knowledge", skipped the researcher, and the writer cited
two papers from training memory — real ones, that time, by luck.

Three fixes, one test file: the router always sends a world question to the
researcher; the writer may not cite what it did not read; a link the model
produced from memory is checked before it reaches a note.
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from notron import nodes, research
from notron.state import State


class Brain:
    """Routes like the model that caused the bug: everything is 'general
    knowledge', nothing needs the web."""

    def __init__(self, intent="question", needs_context=False, answer="Fine."):
        self.intent = intent
        self.needs_context = needs_context
        self.answer = answer

    def ask_json(self, **kw):
        return {"intent": self.intent, "needs_context": self.needs_context,
                "needs_web": False, "why": "general knowledge"}

    def ask(self, **kw):
        return self.answer


# ------------------------------------------------- fix 1: the router gate

def test_a_world_question_is_always_sent_to_the_researcher():
    """The router tagged "caffeine and theanine together?" as general
    knowledge and never considered the researcher. Whether the citations
    that followed were real was pure luck of the training data."""
    state = State(request="what's the deal with caffeine and theanine together?")
    state = nodes.router(state, brain=Brain())
    assert state.needs_web, "a question answered from neither notes nor web is where fabrication lives"


def test_a_question_about_their_own_notes_costs_no_web_search():
    state = State(request="when is my shoot?")
    state = nodes.router(state, brain=Brain(needs_context=True))
    assert not state.needs_web


def test_a_scheduling_request_costs_no_web_search():
    state = State(request="remind me to call the pharmacy")
    state = nodes.router(state, brain=Brain(intent="remind"))
    assert not state.needs_web


def test_the_router_prompt_names_checkable_facts_not_just_current_events():
    p = nodes.ROUTER_SYSTEM.lower()
    assert "studies" in p
    assert "doses" in p


# ---------------------------------------------- fix 2: the writer's rules

def test_the_writer_may_not_cite_a_study_it_did_not_read():
    p = nodes.WRITER_SYSTEM.lower()
    assert "never write a doi or url from memory" in p
    assert "unverified" in p


# ------------------------------------------------ fix 3: the link checker

def test_a_link_the_model_made_up_is_checked_and_dropped(monkeypatch):
    """The Cognizin answer carried a DOI one digit off from the real paper's.
    It looked exactly as trustworthy as the correct one next to it."""
    live = "https://doi.org/10.4236/fns.2012.36103"
    dead = "https://doi.org/10.4236/fns.2012.33039"
    monkeypatch.setattr(research, "check_url", lambda u, **k: u == live)
    state = State(request="cognizin?", intent="question")
    state = nodes.writer(state, brain=Brain(
        answer=f"McGlade 2012 is real: {live} — see also {dead} for more."))
    assert live in state.answer
    assert dead not in state.answer
    assert "removed" in state.answer.lower()
    assert any("link" in t for t in state.trace)


def test_links_the_search_already_returned_are_trusted_not_rechecked(monkeypatch):
    """Tavily gave us the URL seconds ago; a second HEAD request per link
    would double the wait for nothing."""
    url = "https://examine.com/supplements/citicoline/"

    def explode(u, **k):
        raise AssertionError("a link from the research context was re-checked")

    monkeypatch.setattr(research, "check_url", explode)
    state = State(request="cognizin?", intent="question",
                  web=[f"### Examine\ncdp-choline overview\n{url}"])
    state = nodes.writer(state, brain=Brain(answer=f"Covered well at {url}."))
    assert url in state.answer


def test_link_checking_never_costs_the_answer(monkeypatch):
    """Offline, or a slow server: the answer still lands, minus the link —
    an unverifiable citation is treated exactly like a dead one."""
    def timeout(u, **k):
        raise TimeoutError()

    monkeypatch.setattr(research, "check_url", timeout)
    state = State(request="cognizin?", intent="question")
    state = nodes.writer(state, brain=Brain(answer="See https://example.com/paper today."))
    assert state.answer
    assert "https://example.com/paper" not in state.answer
    assert state.writes, "the reply must still be written"


def test_a_link_wrapped_in_cjk_brackets_is_still_recognised(monkeypatch):
    """Asked about tongkat ali, the writer cited Tavily's own URLs wrapped in
    【…】 markers. The checker read the closing bracket as part of the URL, so
    every link the search had returned seconds earlier looked both unknown
    and dead — all four real citations were dropped from a correct answer."""
    url = "https://examine.com/supplements/tongkat-ali/research"

    def explode(u, **k):
        raise AssertionError(f"re-checked a link the research returned: {u}")

    monkeypatch.setattr(research, "check_url", explode)
    state = State(request="tongkat ali?", intent="question",
                  web=[f"### Examine\ntongkat overview\n{url}"])
    state = nodes.writer(state, brain=Brain(answer=f"Supported【{url}】, broadly."))
    assert url in state.answer


def test_a_dead_link_in_cjk_brackets_is_dropped_without_its_wrapper(monkeypatch):
    monkeypatch.setattr(research, "check_url", lambda u, **k: False)
    state = State(request="tongkat ali?", intent="question")
    state = nodes.writer(state, brain=Brain(
        answer="Supported【https://made.up/paper】, allegedly."))
    assert "https://made.up/paper" not in state.answer
    assert "【" not in state.answer and "】" not in state.answer


def test_check_url_believes_http_status_not_hope(monkeypatch):
    import urllib.error, urllib.request

    def fake_urlopen(req, timeout=0):
        url = req.full_url
        if "404" in url:
            raise urllib.error.HTTPError(url, 404, "not found", {}, None)
        if "405" in url:
            raise urllib.error.HTTPError(url, 405, "method not allowed", {}, None)

        class R:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False

        return R()

    monkeypatch.setattr(research.urllib.request, "urlopen", fake_urlopen)
    assert research.check_url("https://doi.org/real") is True
    assert research.check_url("https://doi.org/404") is False
    assert research.check_url("https://stuffy.org/405-no-head") is True, \
        "a server that refuses HEAD still proves the page exists"
