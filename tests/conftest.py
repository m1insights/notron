"""Tests run with no API key and no network — always.

Every world question now routes to the researcher by design, so a Tavily key
exported in the developer's shell would quietly turn the graph tests into
live web searches. Strip it before every test; a test that wants a key sets
one itself.
"""

import pytest

from notron import rewrite, undo


@pytest.fixture(autouse=True)
def _no_search_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def _undo_state_is_disposable(monkeypatch, tmp_path):
    """Every successful write now saves an undo slot (`Executor._apply`), so any
    test that lets Executor perform a real write — not just tests/test_executor.py,
    also the Filer and action tests — touches `undo.STATE` whether it means to or
    not. Redirect it everywhere, the same way `_no_search_key` strips a live key
    everywhere, so no test run pollutes the real .notron/undo.json with fake
    note ids and fake bodies. A test that specifically exercises undo.py itself
    still overrides this per-test, same as before."""
    monkeypatch.setattr(undo, "STATE", tmp_path / "undo.json")


@pytest.fixture(autouse=True)
def _rewrite_state_is_disposable(monkeypatch, tmp_path):
    """`nodes.organizer` calls `rewrite.allow(note.id)` when it reads a `yes`
    under its own offer, so a node-level or graph-level test can now write to
    the real .notron/rewrite.json — and a fake note id left in there is a
    standing permission to rewrite a real note in place. Redirect it
    everywhere, same as undo above."""
    monkeypatch.setattr(rewrite, "STATE", tmp_path / "rewrite.json")
