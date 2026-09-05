from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from dataclasses import replace
import pytest
from notron import when, nodes, guard, layout
from notron.requests import RequestEnvelope
from notron.state import State, Action

FRIDAY = datetime(2026, 9, 4, 18, tzinfo=ZoneInfo('America/New_York'))
MONDAY = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)

def envelope(text='remind me tomorrow', captured_at=None):
    return RequestEnvelope(1, 'delayed', 'cli' if captured_at else 'ask', text,
        captured_at, FRIDAY, 'America/New_York', 'explicit' if captured_at else 'observed_only')

def test_relative_date_after_resume_requires_confirmation():
    result = when.resolve_time_context(envelope(), now=MONDAY, resumed=True)
    assert result.needs_confirmation and 'date' in result.message

def test_friday_capture_processed_monday_keeps_capture_timezone():
    result = when.resolve_time_context(envelope(captured_at=FRIDAY), now=MONDAY, resumed=True)
    assert result.reference == FRIDAY and not result.needs_confirmation

def test_timezone_change_does_not_reinterpret_capture():
    result = when.resolve_time_context(envelope(captured_at=FRIDAY), now=MONDAY.astimezone(ZoneInfo('Asia/Tokyo')), resumed=True)
    assert result.reference.tzinfo.key == 'America/New_York'

@pytest.mark.parametrize('value', ['2026-11-01T01:30', '2026-03-08T02:30'])
def test_dst_ambiguous_and_nonexistent_times_require_offset(value):
    with pytest.raises(ValueError):
        when.resolve_local(value, 'America/New_York')

def test_explicit_offset_survives_parsing():
    assert when.parse('2026-11-01T01:30-04:00').utcoffset().total_seconds() == -14400
    assert when.parse('2026-09-04T22:00Z').tzinfo is not None

def test_explicit_past_time_is_refused_even_within_old_grace_period():
    action = Action('reminder', 'create', 'Call', when='2026-09-07T11:59+00:00')
    assert not guard.check_action(action, now=MONDAY)

def test_date_only_today_is_allowed_and_receipt_explains_alarm():
    action = Action('reminder', 'create', 'Call', when='2026-09-07')
    assert guard.check_action(action, now=MONDAY)
    from notron.executor import WriteResult
    assert 'no explicit timed alarm' in nodes._confirmation(action, WriteResult(True, 'ok'))

def test_journal_unknown_capture_labels_filing_date():
    assert 'filing date; capture date unknown' in layout.markdown([('Thought', [])], shape='log', existing_text='', day=MONDAY.date(), capture_known=False)

@pytest.mark.parametrize('text,out', [
 ('remind me every day', {'kind':'reminder','title':'Call'}),
 ('remind me to call and schedule a meeting', {'kind':'reminder','title':'Call'}),
 ('remind me to call', {'kind':'reminder','title':'Call','actions':[{},{}]}),
])
def test_unsupported_extraction_never_saves_partial_action(text, out):
    class Brain:
        def ask_json(self, **kw): return out
    result = nodes.scheduler(State(request=text, intent='remind'), brain=Brain())
    assert not result.actions

def test_missing_event_duration_asks_for_end():
    class Brain:
        def ask_json(self, **kw): return {'kind':'event', 'title':'Call', 'when':'2026-09-09T10:00'}
    result = nodes.scheduler(State(request='schedule Call September 9 at 10am', intent='schedule'), brain=Brain())
    assert not result.actions and 'end' in result.answer


def test_cached_scheduler_checkpoint_cannot_bypass_delayed_date_check(monkeypatch, _notes_is_never_the_real_one):
    from notron import notes
    app = _notes_is_never_the_real_one
    monkeypatch.setattr(notes, "write_body", lambda nid, body: app.bodies.__setitem__(nid, body))
    from notron import graph, requests, recovery, reminders
    request = envelope()
    requests.current().capture(request)
    requests.current().claim(request.request_id)
    state = State(request=request.text, request_id=request.request_id, envelope=request, intent='remind',
                  actions=[Action('reminder','create','Call',when='2026-09-08T10:00-04:00')])
    recovery.checkpoint(state, 'scheduler')
    monkeypatch.setattr(reminders,'create',lambda *a,**kw: pytest.fail('delayed checkpoint must not create'))
    result = graph.run_request(request, brain=None)
    assert 'original capture date is unknown' in result.answer
    assert requests.current().get(request.request_id).status == 'completed'


def test_applied_action_receipt_recovery_does_not_recheck_delayed_date(monkeypatch):
    from notron import requests, recovery, operations
    from notron.executor import Executor
    request = envelope()
    requests.current().capture(request)
    requests.current().claim(request.request_id)
    action = Action('reminder','create','Call',when='2026-09-08T10:00-04:00',target_id='inbox')
    with requests.execution(request):
        recovery.put(request.request_id, action.operation_id, {'action':__import__('dataclasses').asdict(action)}, [])
        store = operations.current()
        store.transition(action.operation_id,operations.S.PREPARED,operations.S.APPLYING)
        store.transition(action.operation_id,operations.S.APPLYING,operations.S.APPLIED,external_id='saved')
        assert Executor(audit=False).do(action, resumed=True).ref == 'saved'


def test_offset_hour_rules_use_captured_zone_not_host_zone(monkeypatch):
    monkeypatch.setenv('TZ','Asia/Tokyo')
    action = Action('reminder','create','Call',when='2026-09-08T12:00+00:00')
    verdict = guard.check_action(action, about='Never schedule before 9am', now=MONDAY, timezone_name='America/New_York')
    assert not verdict and 'before' in verdict.reason


def test_model_cannot_invent_duration_when_user_provided_none():
    class Brain:
        def ask_json(self, **kw): return {'kind':'event','title':'Call','when':'2026-09-09T10:00','ends':'2026-09-09T11:00'}
    result = nodes.scheduler(State(request='schedule Call September 9 at 10am',intent='schedule'),brain=Brain())
    assert not result.actions and 'duration' in result.answer


def test_multiple_reminder_tasks_in_one_request_are_not_partially_extracted():
    class Brain:
        def ask_json(self, **kw): return {'kind':'reminder','title':'Buy milk'}
    result = nodes.scheduler(State(request='remind me to buy milk and call Sam',intent='remind'),brain=Brain())
    assert not result.actions


def test_wrong_relative_date_is_refused_against_capture_reference():
    class Brain:
        def ask_json(self, **kw):return {'kind':'reminder','title':'Call','when':'2026-09-08T10:00'}
    request=envelope(captured_at=FRIDAY)
    result=nodes.scheduler(State(request=request.text,envelope=request,intent='remind'),brain=Brain())
    assert not result.actions and '2026-09-05' in result.answer


def test_date_only_adapter_uses_request_timezone_after_host_change(monkeypatch):
    from notron import reminders
    monkeypatch.setenv('TZ','Asia/Tokyo')
    calls=[]
    reminders.create('Call',when_iso='2026-09-07',target_id='inbox',timezone_name='America/New_York',caller=lambda body,**kw:calls.append(kw['data']) or {'id':'new'})
    assert calls[0]['offset']==-14400
    assert calls[0]['when']=='2026-09-07'


def test_first_observation_today_does_not_invent_original_capture():
    result=when.resolve_time_context(envelope(),now=FRIDAY,resumed=False)
    assert result.needs_confirmation


def test_cli_file_command_capture_is_not_the_notes_thought_capture(monkeypatch):
    from notron import filer, requests
    request=envelope(text='file my brain dump',captured_at=FRIDAY)
    item=filer.Item('Old thought','Old thought',0,'Dump','Notes',note_id='note-1')
    monkeypatch.setattr(filer,'_today',lambda:MONDAY.date())
    with requests.execution(request):
        result=filer._entry_markdown([item],shape='log')
    assert 'Mon 7 Sep 2026' in result and 'capture date unknown' in result


def test_actual_explicit_thought_capture_uses_original_date(monkeypatch):
    from notron import filer, requests
    request=replace(envelope(text='Old thought',captured_at=FRIDAY),note_id='note-1',source_text='Old thought')
    item=filer.Item('Old thought','Old thought',0,'Dump','Notes',note_id='note-1')
    monkeypatch.setattr(filer,'_today',lambda:MONDAY.date())
    with requests.execution(request):
        result=filer._entry_markdown([item],shape='log')
    assert 'Fri 4 Sep 2026' in result and 'capture date unknown' not in result


def test_model_duration_must_match_requested_minutes():
    class Brain:
        def ask_json(self,**kw):return {'kind':'event','title':'Call','when':'2026-09-09T10:00','ends':'2026-09-09T11:00'}
    result=nodes.scheduler(State(request='Schedule Call September 9 at 10am for 30 minutes',intent='schedule'),brain=Brain())
    assert not result.actions and 'duration' in result.answer


def test_each_week_is_recurring_not_one_supported_action():
    class Brain:
        def ask_json(self,**kw):return {'kind':'reminder','title':'Call','when':'2026-09-09T10:00'}
    result=nodes.scheduler(State(request='Remind me each week to Call',intent='remind'),brain=Brain())
    assert not result.actions
