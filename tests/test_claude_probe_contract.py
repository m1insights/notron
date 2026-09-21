"""R00 Task 2 — the injected-client lifecycle contract.

What these tests are for, and what they are deliberately not for:

They are **not** a claim that the Claude Agent SDK works. Nothing here touches an
SDK, a credential or a network. They pin the rule that makes the eventual live
probe trustworthy — *a capability is only reported when the provider affirmed
it* — so that when the SDK is qualified against a real account, a false
`cancel_acknowledged` is a bug in the probe rather than a silent lie in the
evidence table.

The live half of R00 Task 2 (real start → resume → cancel on a synthetic project,
with Bash/edits/writes denied) remains **pending an owner credential decision**,
which is recorded as pending in the integration matrix rather than filled in.
"""
import json

from scripts.probes.claude_session_probe import PROBE_KEYS, run_probe


class RefusingClient:
    """Resumes fine, refuses to acknowledge the cancel. The plan's fixture."""

    def start(self):
        return {'session_id': 'owned-1'}

    def resume(self, session_id):
        assert session_id == 'owned-1'
        return {'resumed': True}

    def cancel(self, session_id):
        return {'acknowledged': False}


class WorkingClient:
    def __init__(self):
        self.calls = []

    def start(self):
        self.calls.append('start')
        return {'session_id': 'owned-2'}

    def resume(self, session_id):
        self.calls.append(('resume', session_id))
        return {'resumed': True}

    def cancel(self, session_id):
        self.calls.append(('cancel', session_id))
        return {'acknowledged': True}


class NoSessionClient:
    def start(self):
        return {}

    def resume(self, session_id):  # pragma: no cover - must never be reached
        raise AssertionError('resume must not run without a session id')

    def cancel(self, session_id):  # pragma: no cover - must never be reached
        raise AssertionError('cancel must not run without a session id')


class TruthyStringClient:
    """The provider that answers `{"acknowledged": "true"}`. A string is not an
    observation, and a probe that accepts one reports a stop that may not have
    happened."""

    def start(self):
        return {'session_id': 'owned-3'}

    def resume(self, session_id):
        return {'resumed': 'yes'}

    def cancel(self, session_id):
        return {'acknowledged': 'true'}


def test_no_cancellation_claim_without_acknowledgement():
    """The plan's required case: resume worked, cancel was refused, so the probe
    reports exactly that and does not round a refusal up to a capability."""
    result = run_probe(RefusingClient())
    assert result['owned_session_resume'] is True
    assert result['cancel_acknowledged'] is False


def test_resume_reuses_the_id_the_first_call_returned():
    client = WorkingClient()
    result = run_probe(client)
    assert client.calls == ['start', ('resume', 'owned-2'), ('cancel', 'owned-2')]
    assert result == {
        'sdk_version': None,
        'provider': None,
        'model': None,
        'owned_session_resume': True,
        'selected_history': False,
        'cancel_acknowledged': True,
        'permission_hook': False,
        'budget_enforced': False,
        'runtime_requirements': {
            'own_session_started': True,
            'cancel_requires_explicit_ack': True,
        },
        'result': 'ok',
    }


def test_a_start_without_a_session_id_probes_nothing_further():
    """No session id means nothing to resume. Reporting `owned_session_resume`
    optimistically here is how an adapter ends up driving the wrong session."""
    result = run_probe(NoSessionClient())
    assert result['owned_session_resume'] is False
    assert result['cancel_acknowledged'] is False
    assert result['result'] == 'start_returned_no_session_id'
    assert result['runtime_requirements']['own_session_started'] is False


def test_truthy_strings_are_not_capabilities():
    result = run_probe(TruthyStringClient())
    assert result['owned_session_resume'] is False
    assert result['cancel_acknowledged'] is False


def test_probe_output_is_exactly_the_sanitized_key_set():
    """Evidence must not leak session contents, tokens or real project paths by
    accretion: an adapter that returns extra fields must not widen the artifact."""
    result = run_probe(WorkingClient())
    assert tuple(result) == PROBE_KEYS
    assert json.loads(json.dumps(result)) == result
