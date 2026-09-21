"""R00 Task 2 — Claude session adapter lifecycle probe.

**Scope note.** Following the September 21 rubric correction, Claude is no longer
the first workflow: NVIDIA Nemotron owns the reasoning and every decision, and a
Claude Code session is optional, disclosed, bring-your-own capability inside a
contained run. This probe therefore exists to answer one narrow question — *can a
Notron-owned session be started, resumed and cancelled, and does cancellation
actually get acknowledged?* — without making the product depend on the answer.

It is deliberately three calls and no SDK import. The client is injected, so the
lifecycle contract is testable with no credential, no network and no provider.
Nothing here reads ambient credentials, a real project path, or session content.

Two rules this module exists to enforce:

1. **Nothing is claimed that was not observed.** Every capability starts `False`
   and is only raised by an affirmative value in a probe response. A provider
   that silently ignores a cancel must not be recorded as supporting cancel.
2. **A process kill does not prove a remote stop.** `cancel_acknowledged` records
   an explicit acknowledgement from the provider, because killing a local process
   says nothing about whether remote work or billing stopped.
"""
from __future__ import annotations

#: Every key the probe may emit. Evidence must be sanitized to exactly these.
PROBE_KEYS = (
    'sdk_version',
    'provider',
    'model',
    'owned_session_resume',
    'selected_history',
    'cancel_acknowledged',
    'permission_hook',
    'budget_enforced',
    'runtime_requirements',
    'result',
)


def _clean(value) -> bool:
    """Only a real boolean True counts. A provider returning "true" or 1 is not
    an observed capability, and treating it as one is how a probe invents
    support that does not exist."""
    return value is True


def _result(*, owned_session_resume: bool, cancel_acknowledged: bool,
            result: str, session_id_seen: bool = False) -> dict:
    return {
        # Recorded by the caller, never guessed here.
        'sdk_version': None,
        'provider': None,
        'model': None,
        'owned_session_resume': owned_session_resume,
        'selected_history': False,
        'cancel_acknowledged': cancel_acknowledged,
        'permission_hook': False,
        'budget_enforced': False,
        'runtime_requirements': {
            'own_session_started': session_id_seen,
            'cancel_requires_explicit_ack': True,
        },
        'result': result,
    }


def run_probe(client) -> dict:
    """Exercise start → resume → cancel against an injected client.

    `client.start()`, `client.resume(session_id)` and `client.cancel(session_id)`
    are functions supplied by the caller and return dictionaries. A real client
    wraps the pinned SDK; the contract tests supply fakes.

    Resuming requires the ID the *first* call returned — an adapter that cannot
    carry a session ID across calls cannot resume the session it started, and
    must not report that it can.
    """
    started = client.start()
    session_id = started.get('session_id') if isinstance(started, dict) else None
    if not session_id:
        return _result(owned_session_resume=False, cancel_acknowledged=False,
                       result='start_returned_no_session_id')

    resumed = client.resume(session_id)
    resumed_ok = isinstance(resumed, dict) and _clean(resumed.get('resumed'))

    cancelled = client.cancel(session_id)
    cancel_ok = isinstance(cancelled, dict) and _clean(cancelled.get('acknowledged'))

    return _result(
        owned_session_resume=resumed_ok,
        cancel_acknowledged=cancel_ok,
        result='ok' if resumed_ok else 'resume_failed',
        session_id_seen=True,
    )
