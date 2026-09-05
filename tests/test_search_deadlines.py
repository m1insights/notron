from contextlib import nullcontext
import json
from types import SimpleNamespace as NS

import httpx2
import pytest

from notron import brain, credentials, network, research
from notron.outbound import Passage


class Client:
    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def __enter__(self): return self
    def __exit__(self, *args): pass

    def post(self, *args, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        request = httpx2.Request('POST', network.SEARCH_URL)
        return httpx2.Response(reply, request=request) if isinstance(reply, int) else httpx2.Response(
            200, request=request, json=reply)


def passage():
    return [Passage('current answer', 'user_request')]


def test_search_retries_transient_status_inside_one_interactive_deadline(monkeypatch):
    credentials._provider.put(credentials.SEARCH_KEY, b'synthetic-search')
    client = Client([503, {'answer': 'found', 'results': []}])
    monkeypatch.setattr(network, 'provider_client', lambda endpoint: client)
    monkeypatch.setattr(brain, '_deadline_guard', lambda deadline: nullcontext())
    monkeypatch.setattr(brain.time, 'sleep', lambda delay: None)
    clock = iter([10.0, 11.0, 12.0, 13.0])
    monkeypatch.setattr(brain.time, 'monotonic', lambda: next(clock))

    assert research.search(passage()) == ('found', [])
    assert len(client.calls) == 2
    assert client.calls[0]['timeout'] == pytest.approx(29.0)
    assert client.calls[1]['timeout'] < client.calls[0]['timeout']


def test_tavily_and_nebius_cooldowns_are_independent(monkeypatch):
    credentials._provider.put(credentials.SEARCH_KEY, b'synthetic-search')
    monkeypatch.setattr(brain, '_deadline_guard', lambda deadline: nullcontext())
    brain._save_retry_state('nebius', 2, brain.time.time() + 30)
    client = Client([{'answer': 'found', 'results': []}])
    monkeypatch.setattr(network, 'provider_client', lambda endpoint: client)

    assert research.search(passage()) == ('found', [])
    state = json.loads(brain.PROVIDER_STATE.read_text())
    assert state['nebius']['failures'] == 2
    assert state['tavily'] == {'failures': 0, 'retry_after': 0.0}


def test_search_deadline_failure_records_only_tavily_cooldown(monkeypatch):
    credentials._provider.put(credentials.SEARCH_KEY, b'synthetic-search')
    client = Client([503, 503])
    monkeypatch.setattr(network, 'provider_client', lambda endpoint: client)
    monkeypatch.setattr(brain, '_deadline_guard', lambda deadline: nullcontext())
    monkeypatch.setattr(brain.time, 'sleep', lambda delay: None)

    with pytest.raises(network.ProviderConnectivityError):
        research.search(passage())
    state = json.loads(brain.PROVIDER_STATE.read_text())
    assert state['tavily']['failures'] == 1
    assert state['nebius'] == {'failures': 0, 'retry_after': 0.0}
