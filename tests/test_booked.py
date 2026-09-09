"""The durable ledger supersedes main's ten-minute text fingerprint cache."""
from datetime import datetime, timedelta

from notron import executor, reminders
from notron.state import Action


def test_retry_reuses_operation_but_identical_new_request_remains_distinct(monkeypatch):
    created = []
    monkeypatch.setattr(reminders, 'resolve_targets', lambda *a, **kw: [{'id':'inbox', 'title':'Inbox'}])
    monkeypatch.setattr(reminders, 'create', lambda title, **kw: created.append(title) or str(len(created)))
    due = (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%dT14:00')
    action = Action(kind='reminder', op='create', title='Call the pharmacy', when=due)
    ex = executor.Executor(audit=False)
    first, retry = ex.do(action), ex.do(action)
    assert first.ok and retry.ok
    assert first.ref == retry.ref
    assert created == ['Call the pharmacy']
    separate = ex.do(Action(kind='reminder', op='create', title=action.title, when=due))
    assert separate.ok and separate.ref != first.ref
    assert created == ['Call the pharmacy', 'Call the pharmacy']


def test_dry_run_creates_no_operation_or_external_effect(monkeypatch):
    from notron import operations
    monkeypatch.setattr(reminders, 'create', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('saved')))
    due = (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%dT14:00')
    action = Action(kind='reminder', op='create', title='Call the pharmacy', when=due)
    assert executor.Executor(audit=False, dry_run=True).do(action).ok
    assert operations.current().get(action.operation_id) is None
