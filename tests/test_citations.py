"""Fabricated citations — pinned to real failures from `.notron/listen.log`.

Asked "what's the deal with Cognizin?", Notron cited two papers with DOI
links without ever searching the web: one DOI was wrong (404), the other
paper could not be found at all. Asked about caffeine + theanine, the router
called it "general knowledge", skipped the researcher, and the writer cited
two papers from training memory — real ones, that time, by luck.

Three fixes, one test file: the router always sends a world question to the
researcher; the writer may not cite what it did not read; unsupported citations are marked unverifiable without fetching them.
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


# ------------------------------------------------ fix 3: local grounding only

import pytest
from notron.outbound import Passage
from notron.policy import PolicyError


@pytest.mark.parametrize("url", [
    "https://doi.org/invented", "http://127.0.0.1/admin",
    "https://169.254.169.254/latest/meta-data", "https://[::1]/private",
])
def test_model_generated_urls_cause_zero_dns_or_http_calls(monkeypatch, url):
    import socket, urllib.request
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("citation made a network call")
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    state = State(request="synthetic question", intent="question")
    nodes.writer(state, brain=Brain(answer=f"See {url}."))
    assert url not in state.answer
    assert "unverifiable" in state.answer.lower()
    assert state.writes and calls == []
    assert url not in " ".join(state.trace)


def test_every_unsupported_citation_is_removed_without_five_link_limit():
    urls = [f"https://invented.example/paper{i}" for i in range(8)]
    state = State(request="synthetic", intent="question")
    nodes.writer(state, brain=Brain(answer=" ".join(urls)))
    assert all(url not in state.answer for url in urls)
    assert "unverifiable" in state.answer


@pytest.mark.parametrize("source", ["web", "request", "context", "here"])
def test_exact_prepared_source_links_survive_with_cjk_or_markdown_wrappers(source):
    url = "https://examine.com/supplements/citicoline/"
    state = State(request="synthetic", intent="question")
    if source == "web": state.web = [f"### Study\n{url}"]
    elif source == "request": state.request = f"Summarize {url}"
    elif source == "context": state.context = [Passage(url, "note", "n1")]
    else:
        state.here = url
        state.source_note_id = "n1"
    nodes.writer(state, brain=Brain(answer=f"Supported【{url}】. [Source]({url})"))
    assert url in state.answer
    assert "unverifiable" not in state.answer


def test_substring_of_known_url_is_not_grounding():
    url = "https://example.org/paper"
    state = State(request=f"Look at {url}-different", intent="question")
    nodes.writer(state, brain=Brain(answer=f"See {url}."))
    assert url not in state.answer
    assert "unverifiable" in state.answer


def test_redacted_url_is_not_restored_from_raw_context():
    url = "https://example.org/private"
    state = State(request=f"password: {url}", intent="question")
    nodes.writer(state, brain=Brain(answer=f"See {url}."))
    assert url not in state.answer


def test_denied_context_cannot_ground_citations(outbound_policy):
    url = "https://example.org/paper"
    outbound_policy(ignored=("excluded",))
    state = State(request="synthetic", intent="question", context=[Passage(url, "note", "excluded")])
    with pytest.raises(PolicyError):
        nodes.writer(state, brain=Brain(answer=url))


def test_model_origin_in_context_cannot_ground_its_own_link():
    url = "https://invented.example/paper"
    state = State(request="synthetic", intent="question", context=[Passage(url, "model")])
    nodes.writer(state, brain=Brain(answer=url))
    assert url not in state.answer


def test_cjk_wrapper_removed_for_unsupported_link():
    state = State(request="synthetic", intent="question")
    nodes.writer(state, brain=Brain(answer="Supported【https://invented.example/paper】."))
    assert "【" not in state.answer and "】" not in state.answer


def test_unsupported_prefix_does_not_corrupt_a_grounded_longer_url():
    short = 'https://example.org/paper'
    long = short + '/correct'
    state = State(request=f'Use {long}', intent='question')
    nodes.writer(state, brain=Brain(answer=f'Unsupported {short}. Supported {long}.'))
    assert long in state.answer
    assert f'{short}.' not in state.answer


def test_adjacent_markdown_citations_are_matched_individually():
    known = 'https://example.org/known'
    unknown = 'https://example.org/invented'
    state = State(request=known, intent='question')
    nodes.writer(state, brain=Brain(answer=f'[one]({known})[two]({unknown})'))
    assert known in state.answer
    assert unknown not in state.answer


def test_planner_citations_use_the_same_local_grounding_rule():
    known = 'https://example.org/supplied'
    unknown = 'https://example.org/invented'
    state = State(request=f'Plan using {known}', intent='plan')
    nodes.planner(state, brain=Brain(answer=f'See {known} and {unknown}'))
    assert known in state.answer
    assert unknown not in state.answer
    assert 'unverifiable' in state.writes[0].markdown
