"""Restart boundaries use the real graph/ledger and persistent fake Apple stores."""
from datetime import datetime, timedelta
from types import SimpleNamespace
import pytest
from notron import graph, operations, requests, notes, reminders, calendar, workspace


class Crash(BaseException):
    pass


@pytest.fixture
def recovery_harness(monkeypatch, _notes_is_never_the_real_one):
    try:
        from notron import recovery
    except ImportError:
        recovery = SimpleNamespace(boundary=lambda *a: None, reference=lambda oid: oid)
    app = _notes_is_never_the_real_one
    ask_id = f'{workspace.FOLDER}/{workspace.ASK}'
    app.bodies[ask_id] = '<div>Ask</div><div>Call dentist</div>'
    monkeypatch.setattr(notes, 'write_body', lambda nid, body: app.bodies.__setitem__(nid, body))
    monkeypatch.setattr(reminders, 'resolve_targets', lambda *a, **kw: [{'id':'inbox','title':'Inbox'}])
    monkeypatch.setattr(calendar, 'resolve_targets', lambda *a, **kw: [{'id':'work','title':'Work'}])
    monkeypatch.setattr(calendar, 'overlaps', lambda *a, **kw: [])
    rows, calls = [], []
    h = SimpleNamespace(point=None, envelope=None, state=None, rows=rows, calls=calls, kind='reminder')
    def fail(point, operation_id):
        if h.point == point and ':audit:' not in operation_id:
            h.point = None
            raise Crash(point)
    monkeypatch.setattr(recovery, 'boundary', fail)
    def create(title, **kw):
        rows.append({'id': str(len(rows) + 1), 'notes': kw['notes'], 'kind': h.kind})
        return rows[-1]['id']
    monkeypatch.setattr(reminders, 'create', create)
    monkeypatch.setattr(calendar, 'create', create)
    def find(operation_id):
        return [r['id'] for r in rows if recovery.reference(operation_id) in r['notes'].splitlines()]
    h.adapters = {'reminder': reminders.find_by_operation, 'event': calendar.find_by_operation}
    monkeypatch.setattr(reminders, 'find_by_operation', find, raising=False)
    monkeypatch.setattr(calendar, 'find_by_operation', find, raising=False)
    class Brain:
        def ask_json(self, **kw):
            calls.append(kw['purpose'])
            if kw['purpose'] == 'route':
                return {'intent': 'schedule' if h.kind == 'event' else 'remind'}
            return {'kind': h.kind, 'op': 'create', 'title': 'Call dentist',
                    'when': (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%dT14:00'),
                    'ends': (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%dT15:00') if h.kind == 'event' else None}
        def ask(self, **kw):
            raise AssertionError('Action receipts must not infer')
    h.brain = Brain()
    def submit(rid, text):
        if h.kind == "event":
            text += " from 14:00 to 15:00"
            app.bodies[ask_id] = app.bodies[ask_id].replace("<div>Call dentist</div>", "<div>" + text + "</div>")
        h.envelope = requests.create(text, request_id=rid, source='ask', note_id=ask_id,
            source_revision=requests.revision(app.bodies[ask_id]), source_text=text,
            reply_to=(workspace.ASK, workspace.FOLDER, 1))
    def run_once():
        try:
            h.state = graph.run_request(h.envelope, brain=h.brain)
        except Crash:
            pass
    h.submit, h.run_once = submit, run_once
    h.fail_at = lambda point: setattr(h, 'point', point)
    h.restart = lambda: operations.current()  # New connection + authenticated payload reopen.
    h.reminder_count = lambda oid: len(find(oid))
    h.receipt_count = lambda rid: app.bodies[ask_id].count('Reminder set:') + app.bodies[ask_id].count('In your calendar:')
    h.app, h.ask_id = app, ask_id
    return h


def test_receipt_failure_does_not_create_a_second_reminder(recovery_harness):
    h = recovery_harness
    h.submit('r1', 'Call dentist')
    h.fail_at('before_receipt')
    h.run_once()
    h.restart()
    h.run_once()
    assert h.reminder_count('r1:action:0') == 1
    assert h.receipt_count('r1') == 1
    assert h.calls.count('schedule') == 1
    assert h.state.receipt_complete
    assert requests.current().get('r1').status == 'completed'


@pytest.mark.parametrize('kind', ['reminder', 'event'])
@pytest.mark.parametrize('point', ['before_applying', 'after_external_save', 'before_external_id', 'before_receipt', 'after_receipt'])
def test_restart_at_every_action_boundary(recovery_harness, kind, point):
    h = recovery_harness
    h.kind = kind
    h.submit('r1', 'Call dentist')
    h.fail_at(point)
    h.run_once()
    h.restart()
    h.run_once()
    assert h.reminder_count('r1:action:0') == 1
    assert h.receipt_count('r1') == 1
    assert len(h.calls) == 2


@pytest.mark.parametrize('matches', [0, 2])
def test_uncertain_save_never_recreates_when_reconciliation_is_inconclusive(recovery_harness, matches):
    h = recovery_harness
    h.submit('r1', 'Call dentist')
    h.fail_at('after_external_save')
    h.run_once()
    if matches == 0:
        h.rows.clear()  # delayed sync is not evidence that the save failed
    else:
        h.rows.append(dict(h.rows[0], id='ambiguous'))
    h.restart()
    h.run_once()
    assert len(h.rows) == matches
    assert operations.current().get('r1:action:0').status == operations.S.NEEDS_REVIEW
    assert requests.current().get('r1').status == 'needs_review'
    assert not h.state.receipt_complete


def test_audit_failure_is_independent_and_retries_redacted_metadata(recovery_harness, monkeypatch):
    from notron import audit
    h = recovery_harness
    log_id = f'{workspace.FOLDER}/{workspace.LOG}'
    original_get = notes.get_note
    fail = True
    def get(nid):
        return None if nid == log_id and fail else original_get(nid)
    monkeypatch.setattr(notes, 'get_note', get)
    h.submit('r1', 'Call dentist')
    h.run_once()
    assert requests.current().get('r1').status == 'completed'
    assert len(h.rows) == 1
    assert audit.pending_count() >= 1
    fail = False
    audit.drain()
    assert 'Call dentist' not in h.app.bodies[log_id]
    assert 'never-store-this' not in h.app.bodies[log_id]
    assert len(h.rows) == 1


def test_audit_queue_is_bounded_and_expires(monkeypatch):
    from notron import audit
    monkeypatch.setattr(audit, 'MAX_RECORDS', 3)
    for _ in range(7):
        audit.enqueue('password = arbitrary-private-text')
    assert audit.pending_count() == 3
    store = operations.current()
    with store.transaction() as db:
        db.execute("UPDATE operations SET created_at='2000-01-01T00:00:00+00:00' WHERE operation_id LIKE 'audit:%'")
    audit.prune()
    assert audit.pending_count() == 0


def test_running_request_cannot_be_taken_over_during_live_inference(recovery_harness):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    h = recovery_harness
    h.submit('r1', 'Call dentist')
    entered, release = threading.Event(), threading.Event()
    original = h.brain.ask_json
    def ask(**kw):
        if kw['purpose'] == 'schedule':
            entered.set()
            assert release.wait(3)
        return original(**kw)
    h.brain.ask_json = ask
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(graph.run_request, h.envelope, brain=h.brain)
        assert entered.wait(3)
        second = pool.submit(graph.run_request, h.envelope, brain=h.brain)
        release.set()
        assert first.result().receipt_complete
        assert 'already completed' in second.result().answer
    assert len(h.calls) == 2
    assert len(h.rows) == 1


def test_watcher_does_not_report_action_success_as_receipt_delivery(recovery_harness, monkeypatch):
    from notron import watch
    h = recovery_harness
    h.submit('r1', 'Call dentist')
    from notron.state import State
    monkeypatch.setattr(graph, 'run_request', lambda *a, **kw: State(results=['✓ reminder — done']))
    watcher = watch.Watcher(h.brain)
    assert not watcher._answer('Call dentist', title=workspace.ASK, folder=workspace.FOLDER,
                                after=1, note_id=h.ask_id, envelope=h.envelope)


def test_changed_source_cannot_apply_prepared_action_on_retry(recovery_harness):
    h = recovery_harness
    h.submit('r1', 'Call dentist')
    h.fail_at('before_applying')
    h.run_once()
    h.app.bodies[h.ask_id] = '<div>Ask</div><div>Do not call dentist</div>'
    h.run_once()
    assert not h.rows
    assert requests.current().get('r1').status == 'needs_review'


def test_conflicting_action_identity_is_refused(recovery_harness):
    from notron import executor
    from notron.state import Action
    h = recovery_harness
    h.submit('r1', 'Call dentist')
    h.run_once()
    with requests.execution(h.envelope), pytest.raises(operations.OperationConflict):
        executor.Executor(audit=False).do(Action(kind='reminder', op='create', title='Different task',
                                                 operation_id='r1:action:0'))


def test_conflicting_applied_write_identity_is_refused(recovery_harness):
    from dataclasses import replace
    from notron import executor
    h = recovery_harness
    w = executor.capture_write(workspace.ASK, mode='append')
    w.markdown = 'Original answer'
    ex = executor.Executor(audit=False)
    assert ex.apply_write(w).ok
    with pytest.raises(operations.OperationConflict):
        ex.apply_write(replace(w, markdown='Different answer'))


def test_applied_action_recovers_missing_audit_without_reapplying(recovery_harness, monkeypatch):
    from notron import audit
    h = recovery_harness
    h.submit('r1', 'Call dentist')
    enqueue = audit.enqueue
    def crash(*a, **kw):
        raise Crash('audit enqueue interrupted')
    monkeypatch.setattr(audit, 'enqueue', crash)
    h.run_once()
    assert len(h.rows) == 1
    monkeypatch.setattr(audit, 'enqueue', enqueue)
    h.run_once()
    log_id = f'{workspace.FOLDER}/{workspace.LOG}'
    from hashlib import sha256
    audit_id = 'audit:' + sha256('r1:action:0'.encode()).hexdigest()
    assert operations.current().get(audit_id) is not None
    assert 'Operation verified' in h.app.bodies.get(log_id, '')
    assert len(h.rows) == 1


def test_policy_revocation_purges_cached_inference_and_never_recreates_it(recovery_harness):
    from notron import library, recovery
    h = recovery_harness
    h.submit('r1', 'Call dentist')
    h.fail_at('before_receipt')
    h.run_once()
    policy = library.load()
    policy.ignore.add(h.ask_id)
    library.save(policy)
    record = requests.current().get('r1')
    assert not recovery.available(record)
    assert all(op.payload_ref is None for op in operations.current().pending() if op.request_id == 'r1')
    assert len(h.rows) == 1


def test_receipt_save_crash_reconciles_notes_without_second_inference(recovery_harness, monkeypatch):
    h = recovery_harness
    h.submit('r1', 'Call dentist')
    original = notes.write_body
    failed = False
    def write(nid, body):
        nonlocal failed
        original(nid, body)
        if nid == h.ask_id and not failed:
            failed = True
            raise OSError('uncertain Notes save')
    monkeypatch.setattr(notes, 'write_body', write)
    h.run_once()
    h.restart()
    h.run_once()
    assert h.state.receipt_complete
    assert h.receipt_count('r1') == 1
    assert len(h.rows) == 1
    assert len(h.calls) == 2


def test_background_recovery_finishes_request_even_when_receipt_hides_question(recovery_harness):
    from notron import watch, conversation
    h = recovery_harness
    h.submit('r1', 'Call dentist')
    h.fail_at('after_receipt')
    h.run_once()
    assert not any(q.text == 'Call dentist' for q in conversation.unanswered(h.app.bodies[h.ask_id]))
    watcher = watch.Watcher(h.brain)
    assert watcher.recover_pending()
    assert requests.current().get('r1').status == 'completed'
    assert h.receipt_count('r1') == 1
    assert len(h.rows) == 1


def test_audit_queue_can_backfill_from_applied_metadata_after_enqueue_failure(recovery_harness, monkeypatch):
    from notron import audit
    h = recovery_harness
    h.submit('r1', 'Call dentist')
    original = audit.enqueue
    def unavailable(*a, **kw):
        raise OSError('local queue unavailable')
    monkeypatch.setattr(audit, 'enqueue', unavailable)
    h.run_once()
    assert h.state.receipt_complete
    monkeypatch.setattr(audit, 'enqueue', original)
    audit.drain()
    assert 'Operation verified' in h.app.bodies.get(f'{workspace.FOLDER}/{workspace.LOG}', '')


@pytest.mark.parametrize('kind', ['reminder', 'event'])
def test_reconciliation_adapters_use_exact_opaque_reference_as_data(recovery_harness, kind):
    from notron import recovery
    h = recovery_harness
    h.kind = kind
    h.submit('r1', 'Call dentist')
    h.run_once()
    # The original adapter uses a caller that intercepts the native boundary.
    find = h.adapters[kind]
    calls = []
    def caller(script, *, data):
        calls.append((script, data))
        return ['saved-id']
    assert find('r1:action:0', caller=caller) == ['saved-id']
    script, data = calls[0]
    assert data['reference'] == recovery.reference('r1:action:0')
    assert data['reference'] not in script
    assert "indexOf(input.reference)" in script
    if kind == 'event':
        assert 'start' in data


def test_uncertain_complete_uses_persisted_target_and_does_not_select_another(monkeypatch):
    from notron import executor
    from notron.state import Action
    selected, completed = [], []
    def find(title, **kw):
        selected.append(title)
        return [reminders.Reminder('original-id', title, '', '')]
    def complete(nid):
        completed.append(nid)
        raise OSError('saved then adapter lost response')
    monkeypatch.setattr(reminders, 'find_open', find)
    monkeypatch.setattr(reminders, 'complete', complete)
    monkeypatch.setattr(reminders, 'is_completed', lambda nid: nid == 'original-id')
    ex = executor.Executor(audit=False)
    action = Action('reminder', 'complete', 'Call dentist')
    assert not ex.do(action).ok
    assert ex.do(action).ok
    assert selected == ['Call dentist']
    assert completed == ['original-id']


def test_new_checkpoint_cannot_recreate_content_after_policy_purged_parent(recovery_harness):
    from notron import library, recovery
    h = recovery_harness
    h.submit('r1', 'Call dentist')
    h.fail_at('before_receipt')
    h.run_once()
    lib = library.load()
    lib.allow_new_notes = False  # Existing source remains readable, parent is invalidated.
    library.save(lib)
    with requests.execution(h.envelope), pytest.raises(ValueError):
        recovery.put('r1', 'r1:checkpoint:late', {'text': 'Call dentist'}, [h.ask_id])
    assert operations.current().get('r1:checkpoint:late') is None
