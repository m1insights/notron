"""Talking to Nebius — the shapes of the requests, never a real one.

`notron/brain.py` had no tests of its own until vision arrived. It earns them
now because `see` is the first call in NOTRON that is not plain text, and its
one interesting behaviour — a reply that is 100% reasoning and 0% answer — is
invisible unless something asserts on it.
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from notron import brain as brainmod


class FakeCompletions:
    """Records what was sent, replies with whatever it was handed."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def create(self, **kw):
        self.sent.append(kw)
        content, reasoning = self.replies.pop(0)
        message = type("Msg", (), {"content": content, "reasoning": reasoning})()
        choice = type("Choice", (), {"message": message})()
        return type("Resp", (), {"choices": [choice], "usage": None})()


@pytest.fixture
def brain(monkeypatch, tmp_path):
    monkeypatch.setattr(brainmod, "USAGE_LOG", tmp_path / "usage.json")
    b = brainmod.Brain(api_key="not-a-real-key")

    def use(replies):
        fake = FakeCompletions(replies)
        b._client = type("Client", (), {"chat": type("Chat", (), {"completions": fake})()})()
        return fake

    b.use = use
    return b


def test_a_picture_is_sent_the_way_the_endpoint_expects(brain):
    """One user message, a text part then an image part carrying a data URI —
    the OpenAI multimodal form, which is what Nebius speaks."""
    fake = brain.use([("a whiteboard with three columns", None)])
    out = brain.see(image=b"\x89PNG", mime="image/png", question="what is this?")

    assert out == "a whiteboard with three columns"
    parts = fake.sent[0]["messages"][0]["content"]
    assert parts[0] == {"type": "text", "text": "what is this?"}
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert fake.sent[0]["model"] == brainmod.VISION_MODEL


def test_a_reply_that_was_all_thinking_is_asked_again_with_more_room(brain):
    """MiniCPM has Nemotron's disease: it emits a <think> block billed against
    max_tokens, and a 300-token budget came back 100% reasoning and 0% answer
    on the first live test. Measured 2026-09-05."""
    fake = brain.use([("", "let me look at the columns..."), ("three columns", None)])
    out = brain.see(image=b"x", mime="image/png", question="what is this?", max_tokens=300)

    assert out == "three columns"
    assert len(fake.sent) == 2
    assert fake.sent[1]["max_tokens"] == fake.sent[0]["max_tokens"] * 2


def test_an_empty_reply_with_no_thinking_behind_it_is_not_paid_for_twice(brain):
    """Nothing to think its way out of — a second call would buy the same
    silence at twice the price."""
    fake = brain.use([("", None)])
    assert brain.see(image=b"x", mime="image/png", question="?") == ""
    assert len(fake.sent) == 1


def test_the_thinking_budget_is_added_on_top_of_the_answer(brain):
    fake = brain.use([("ok", None)])
    brain.see(image=b"x", mime="image/png", question="?", max_tokens=500)
    assert fake.sent[0]["max_tokens"] == 500 + brainmod.REASONING_HEADROOM


def test_the_vision_model_can_be_overridden_like_every_other_tier(brain, monkeypatch):
    monkeypatch.setenv("NOTRON_MODEL_VISION", "google/gemma-3-27b-it")
    fake = brain.use([("ok", None)])
    brain.see(image=b"x", mime="image/png", question="?")
    assert fake.sent[0]["model"] == "google/gemma-3-27b-it"
