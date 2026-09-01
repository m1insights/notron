"""Tests run with no API key and no network — always.

Every world question now routes to the researcher by design, so a Tavily key
exported in the developer's shell would quietly turn the graph tests into
live web searches. Strip it before every test; a test that wants a key sets
one itself.
"""

import pytest


@pytest.fixture(autouse=True)
def _no_search_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
