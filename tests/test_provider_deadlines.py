from contextlib import nullcontext
import json
import os
import threading
from types import SimpleNamespace as NS

import pytest

from notron import brain as brain_module, network
from notron.brain import Brain
from notron.outbound import Passage


@pytest.fixture(autouse=True)
def isolated_retry_state(monkeypatch, tmp_path):
    monkeypatch.setattr(brain_module, "PROVIDER_STATE", tmp_path / "provider.json")


def fake_brain(replies):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return NS(choices=[NS(message=NS(content=reply, reasoning=None))], usage=None)

    instance = Brain.__new__(Brain)
    instance.base_url = network.NEBIUS_URL
    instance._endpoint = network.ProviderEndpoint(network.NEBIUS_URL, "nebius", False)
    instance._client = NS(
        api_key="synthetic",
        chat=NS(completions=NS(create=create)),
        embeddings=NS(create=create),
    )
    instance._check_credentials = lambda: None
    return instance, calls


def ask(instance):
    return instance.ask(system="static", user=[Passage("question", "user_request")], purpose="write")


def test_interactive_and_batch_calls_use_one_absolute_deadline(monkeypatch):
    instance, calls = fake_brain(["interactive", "batch"])
    clock = iter([100.0, 101.0, 200.0, 202.0])
    monkeypatch.setattr(brain_module.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(brain_module, "_deadline_guard", lambda deadline: nullcontext())

    assert ask(instance) == "interactive"
    with brain_module.batch_deadline():
        assert ask(instance) == "batch"

    assert calls[0]["timeout"] == pytest.approx(29.0)
    assert calls[1]["timeout"] == pytest.approx(58.0)


def test_transport_failure_retries_with_jitter_inside_original_deadline(monkeypatch):
    instance, calls = fake_brain([
        network.ProviderConnectivityError("offline"),
        "recovered",
    ])
    values = iter([10.0, 10.1, 10.2, 10.5])
    monkeypatch.setattr(brain_module.time, "monotonic", lambda: next(values))
    monkeypatch.setattr(brain_module.time, "sleep", lambda delay: None)
    monkeypatch.setattr(brain_module.random, "uniform", lambda low, high: 0.25)
    monkeypatch.setattr(brain_module, "_deadline_guard", lambda deadline: nullcontext())

    assert ask(instance) == "recovered"
    assert len(calls) == 2
    assert calls[1]["timeout"] < calls[0]["timeout"]


def test_empty_reasoning_retry_does_not_get_a_fresh_deadline(monkeypatch):
    instance, calls = fake_brain(["", "answer"])
    calls_left = iter([0.0, 1.0, 8.0])
    monkeypatch.setattr(brain_module.time, "monotonic", lambda: next(calls_left))
    monkeypatch.setattr(brain_module, "_deadline_guard", lambda deadline: nullcontext())
    first = NS(content="", reasoning="spent budget")
    second = NS(content="answer", reasoning=None)
    replies = iter([first, second])
    instance._client.chat.completions.create = lambda **kw: (
        calls.append(kw), NS(choices=[NS(message=next(replies))], usage=None))[1]

    assert ask(instance) == "answer"
    assert calls[0]["timeout"] == pytest.approx(29.0)
    assert calls[1]["timeout"] == pytest.approx(22.0)


def test_failed_call_persists_cooldown_without_provider_or_user_text(monkeypatch):
    instance, calls = fake_brain([
        network.ProviderConnectivityError("sensitive provider detail"),
        network.ProviderConnectivityError("different sensitive detail"),
    ])
    monkeypatch.setattr(brain_module.time, "monotonic", lambda: 1.0)
    monkeypatch.setattr(brain_module.time, "time", lambda: 1000.0)
    monkeypatch.setattr(brain_module.time, "sleep", lambda delay: None)
    monkeypatch.setattr(brain_module.random, "uniform", lambda low, high: 0.0)
    monkeypatch.setattr(brain_module, "_deadline_guard", lambda deadline: nullcontext())

    with pytest.raises(network.ProviderConnectivityError):
        ask(instance)

    state = json.loads(brain_module.PROVIDER_STATE.read_text())
    raw = brain_module.PROVIDER_STATE.read_text()
    assert state["nebius"]["failures"] == 1 and state["nebius"]["retry_after"] > 1000.0
    assert state["tavily"] == {"failures": 0, "retry_after": 0.0}
    assert "sensitive" not in raw
    with pytest.raises(network.ProviderCooldownError):
        ask(instance)
    assert len(calls) == 2


def test_policy_and_deadline_failures_are_never_retried(monkeypatch):
    monkeypatch.setattr(brain_module, "_deadline_guard", lambda deadline: nullcontext())
    for failure in (network.NetworkPolicyError("denied"), network.ProviderDeadlineError("late")):
        instance, calls = fake_brain([failure])
        with pytest.raises(type(failure)):
            ask(instance)
        assert len(calls) == 1


def test_embeddings_default_to_batch_budget(monkeypatch):
    instance, calls = fake_brain([])
    instance._client.embeddings.create = lambda **kw: (calls.append(kw), NS(data=[], usage=None))[1]
    clock = iter([10.0, 11.0])
    monkeypatch.setattr(brain_module.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(brain_module, "_deadline_guard", lambda deadline: nullcontext())

    assert instance.embed([Passage("one", "user_request")]) == []
    assert calls[0]["timeout"] == pytest.approx(59.0)


def test_deadline_failure_persists_cooldown(monkeypatch):
    instance, _ = fake_brain([network.ProviderDeadlineError("late")])
    monkeypatch.setattr(brain_module, "_deadline_guard", lambda deadline: nullcontext())
    monkeypatch.setattr(brain_module.time, "time", lambda: 100.0)

    with pytest.raises(network.ProviderDeadlineError):
        ask(instance)

    assert json.loads(brain_module.PROVIDER_STATE.read_text()) == {
        "nebius": {"failures": 1, "retry_after": 101.0},
        "tavily": {"failures": 0, "retry_after": 0.0}}


@pytest.mark.parametrize("status", [408, 429, 500, 503])
def test_transient_http_status_retries_then_cools_down(monkeypatch, status):
    instance, calls = fake_brain([status_error(status), status_error(status)])
    monkeypatch.setattr(brain_module, "_deadline_guard", lambda deadline: nullcontext())
    monkeypatch.setattr(brain_module.time, "sleep", lambda delay: None)

    with pytest.raises(network.ProviderConnectivityError):
        ask(instance)

    assert len(calls) == 2
    assert json.loads(brain_module.PROVIDER_STATE.read_text())["nebius"]["failures"] == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_fixed_http_status_is_classified_without_retry_or_cooldown(monkeypatch, status):
    instance, calls = fake_brain([status_error(status)])
    monkeypatch.setattr(brain_module, "_deadline_guard", lambda deadline: nullcontext())

    with pytest.raises(network.ProviderRejectedError) as error:
        ask(instance)

    assert error.value.status_code == status
    assert len(calls) == 1
    assert not brain_module.PROVIDER_STATE.exists()


def status_error(status):
    import httpx2
    from openai import APIStatusError
    request = httpx2.Request("POST", network.NEBIUS_URL + "chat/completions")
    response = httpx2.Response(status, request=request)
    return APIStatusError("sensitive provider body", response=response, body={"secret": "text"})


@pytest.mark.parametrize("payload", [
    "not-json",
    '{"nebius":{"failures":NaN,"retry_after":0},"tavily":{"failures":0,"retry_after":0}}',
    '{"nebius":{"failures":-1,"retry_after":0},"tavily":{"failures":0,"retry_after":0}}',
    '{}'])
def test_invalid_retry_state_fails_closed(monkeypatch, payload):
    brain_module.PROVIDER_STATE.write_text(payload)
    instance, calls = fake_brain(["must not run"])
    monkeypatch.setattr(brain_module, "_deadline_guard", lambda deadline: nullcontext())

    with pytest.raises(network.ProviderStateError):
        ask(instance)
    assert calls == []


def test_symlink_retry_state_fails_closed_without_reading_target(monkeypatch, tmp_path):
    target = tmp_path / "user-file"
    target.write_text('{"failures": 0, "retry_after": 0, "user": "private"}')
    os.symlink(target, brain_module.PROVIDER_STATE)
    instance, calls = fake_brain(["must not run"])
    monkeypatch.setattr(brain_module, "_deadline_guard", lambda deadline: nullcontext())

    with pytest.raises(network.ProviderStateError) as error:
        ask(instance)
    assert "private" not in str(error.value)
    assert calls == []


def test_models_probe_records_failure_but_success_does_not_clear_history(monkeypatch):
    instance, _ = fake_brain([])
    monkeypatch.setattr(brain_module, "_deadline_guard", lambda deadline: nullcontext())
    instance._client.models = NS(list=lambda **kw: (_ for _ in ()).throw(status_error(503)))
    with pytest.raises(network.ProviderConnectivityError):
        instance.available_models()
    state = json.loads(brain_module.PROVIDER_STATE.read_text())
    monkeypatch.setattr(brain_module.time, "time", lambda: state["nebius"]["retry_after"] + 1)
    instance._client.models = NS(list=lambda **kw: NS(data=[NS(id="model")]))
    assert instance.available_models() == ["model"]
    assert json.loads(brain_module.PROVIDER_STATE.read_text()) == state


def test_background_thread_fails_before_provider_transport():
    instance, calls = fake_brain(["must not run"])
    errors = []
    thread = threading.Thread(target=lambda: capture_error(errors, lambda: ask(instance)))
    thread.start()
    thread.join(1)
    assert not thread.is_alive()
    assert isinstance(errors[0], network.ProviderDeadlineError)
    assert calls == []


def capture_error(errors, operation):
    try:
        operation()
    except Exception as error:
        errors.append(error)
