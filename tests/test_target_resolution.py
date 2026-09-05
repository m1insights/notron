from dataclasses import replace
from datetime import datetime, timezone
import pytest
from notron import reminders, calendar, eventkit, index, nodes
from notron.state import Action


def test_duplicate_reminder_names_return_all_stable_ids():
    rows = [{'id':str(i), 'title':'Buy milk', 'list':name} for i,name in enumerate(['Home','Work'])]
    hits = reminders.find_open('Buy milk', caller=lambda *a,**kw: rows)
    assert [hit.id for hit in hits] == ['0','1']

@pytest.mark.parametrize('module', [calendar, reminders])
def test_named_target_mismatch_and_duplicates_never_default(module):
    rows = [{'id':'1','title':'Work','default':True},{'id':'2','title':'Work','default':False}]
    assert module.resolve_targets('Missing', caller=lambda *a,**kw: rows) == []
    assert len(module.resolve_targets('Work', caller=lambda *a,**kw: rows)) == 2


def test_calendar_truncation_is_visible():
    rows = [{'calendar':'Work','title':str(i),'start':'2026-09-09T10:00+00:00','end':'2026-09-09T11:00+00:00'} for i in range(3)]
    assert 'truncated' in calendar.week(limit=1, caller=lambda *a,**kw: rows)


def test_timed_out_reminder_read_is_not_empty():
    with pytest.raises(eventkit.EventKitError):
        reminders.open_items(caller=lambda *a,**kw: {'error':'fetch timed out'})


def test_overlap_checks_actual_intervals_and_confirmation_is_exact():
    action = Action('event','create','Call',when='2026-09-09T10:00+00:00',ends='2026-09-09T11:00+00:00',target_id='work')
    rows = [{'id':'busy','calendar':'Work','title':'Busy','start':'2026-09-09T10:30+00:00','end':'2026-09-09T12:00+00:00'}]
    hits = calendar.overlaps(action.when, action.ends, caller=lambda *a,**kw: rows)
    assert len(hits) == 1
    token = calendar.conflict_token(action, hits)
    assert token != calendar.conflict_token(replace(action, ends='2026-09-09T11:30+00:00'), hits)
    assert calendar.overlaps('2026-09-09T12:00+00:00','2026-09-09T13:00+00:00',caller=lambda *a,**kw: rows) == []


def test_stale_index_selected_note_refreshes_before_answer(monkeypatch):
    from notron import notes, library, retention
    from notron.outbound import Passage
    monkeypatch.setattr(retention, 'reconcile', lambda: None)
    monkeypatch.setattr(index,'_load',lambda: {'n1':[dict(note_id='n1',title='Old',folder='Notes',modified='old',text='Old dose',vector=[1.,0.])]})
    monkeypatch.setattr(notes,'get_note',lambda nid: notes.Note(nid,'Current','Notes','new'))
    monkeypatch.setattr(notes,'read_body',lambda nid: '<div>Current dose</div>')
    class Brain:
        def embed(self, query): return [[1.,0.]]
    result = index.search([Passage('dose','user_request')], Brain())
    assert result[0].text == 'Current dose' and result[0].modified == 'new'


def test_stale_index_unavailable_is_incomplete_never_old_text(monkeypatch):
    from notron import notes, retention
    from notron.outbound import Passage
    monkeypatch.setattr(retention, 'reconcile', lambda: None)
    monkeypatch.setattr(index,'_load',lambda: {'n1':[dict(note_id='n1',title='Old',folder='Notes',modified='old',text='Old dose',vector=[1.,0.])]})
    monkeypatch.setattr(notes,'get_note',lambda nid: None)
    class Brain:
        def embed(self, query): return [[1.,0.]]
    result = index.search([Passage('dose','user_request')], Brain())
    assert not result and result.incomplete


def test_executor_conflict_confirmation_authorizes_only_exact_event(monkeypatch):
    from notron import executor, operations
    from datetime import timedelta
    from notron import when
    start = (datetime.now(timezone.utc)+timedelta(days=2)).replace(hour=10,minute=0,second=0,microsecond=0)
    action = Action('event','create','Call',when=start.isoformat(timespec='minutes'),ends=(start+timedelta(hours=1)).isoformat(timespec='minutes'),where='Work')
    monkeypatch.setattr(calendar,'resolve_targets',lambda *a,**kw:[{'id':'work','title':'Work'}])
    conflict = calendar.Event('Work','Busy',action.when,action.ends,id='busy')
    monkeypatch.setattr(calendar,'overlaps',lambda *a,**kw:[conflict])
    saved=[]
    monkeypatch.setattr(calendar,'create',lambda *a,**kw:saved.append(kw) or 'created')
    ex=executor.Executor(audit=False)
    denied=ex.do(action,request='Schedule Call from 10am to 11am')
    assert not denied.ok and not saved
    code=denied.reason.split('confirmation code: ')[1]
    changed=replace(action,title='Different',operation_id='changed')
    assert not ex.do(changed,request='Schedule Call from 10am to 11am\n'+code).ok
    assert ex.do(action,request='Schedule Call from 10am to 11am\n'+code).ok
    assert saved[0]['target_id']=='work'
    assert operations.current().get(action.operation_id).external_id=='created'


def test_executor_duplicate_completion_asks_and_retains_selected_id(monkeypatch):
    from notron.executor import Executor
    from notron import recovery
    items=[reminders.Reminder('a','Buy milk','Inbox',''),reminders.Reminder('b','Buy milk','Inbox','')]
    monkeypatch.setattr(reminders,'open_items',lambda **kw:items)
    completed=[]
    monkeypatch.setattr(reminders,'complete',lambda rid:completed.append(rid) or 'Buy milk')
    action=Action('reminder','complete','Buy milk')
    ex=Executor(audit=False)
    result=ex.do(action)
    assert not result.ok and 'a' in result.reason and 'b' in result.reason and not completed
    assert ex.do(action,request='Complete Buy milk\nreminder-id b').ok
    assert completed==['b'] and recovery.get(action.operation_id)['action']['target_id']=='b'


def test_query_read_budget_does_not_return_unvalidated_cached_notes(monkeypatch):
    from notron import notes, retention
    from notron.outbound import Passage
    monkeypatch.setattr(retention,'reconcile',lambda:None)
    monkeypatch.setattr(index,'_load',lambda:{nid:[dict(note_id=nid,title='Old',folder='Notes',modified='old',text='Old dose',vector=[1.,0.])] for nid in ['n1','note-1']})
    reads=[]
    monkeypatch.setattr(notes,'get_note',lambda nid:reads.append(nid) or notes.Note(nid,'Old','Notes','old'))
    class Brain:
        def embed(self,query):return [[1.,0.]]
    result=index.search([Passage('dose','user_request')],Brain(),read_budget=1)
    assert len(result)==1 and result.incomplete and len(reads)==1


@pytest.mark.parametrize('module', [calendar, reminders])
def test_denied_native_read_cannot_report_an_empty_schedule(module):
    # Run the production-generated JXA through a synthetic process interpreter.
    # The permission guard must execute before any rows can be returned.
    scripts=[]
    def runner(script,timeout,*args):
        scripts.append(script)
        if "authorizationStatusForEntityType" in script and "!== 3" in script:
            raise RuntimeError('denied')
        return '[]'
    reader=calendar.window if module is calendar else reminders.open_items
    with pytest.raises(eventkit.EventKitError,match='denied'):
        reader(caller=lambda body,**kw:eventkit.run(body,runner=runner,**kw))


def test_conflict_with_missing_identity_is_incomplete():
    rows=[{'title':'Busy','start':'2026-09-09T10:00Z','end':'2026-09-09T11:00Z'}]
    with pytest.raises(eventkit.EventKitError,match='incomplete'):
        calendar.overlaps('2026-09-09T10:00Z','2026-09-09T11:00Z',caller=lambda *a,**kw:rows)


def test_calendar_duplicate_can_be_selected_using_stable_id(monkeypatch):
    rows=[{'id':'a','title':'Work'},{'id':'b','title':'Work'}]
    assert calendar.resolve_targets('Work',target_id='b',caller=lambda *a,**kw:rows)==[rows[1]]


def test_date_only_and_midnight_receipts_are_distinct():
    from notron.executor import WriteResult
    date=Action('reminder','create','Call',when='2026-09-09')
    midnight=replace(date,when='2026-09-09T00:00+00:00')
    assert '00:00' in nodes._confirmation(midnight,WriteResult(True,'ok'))
    assert 'no explicit timed alarm' in nodes._confirmation(date,WriteResult(True,'ok'))


def test_calendar_bounds_and_untitled_busy_event_are_not_discarded():
    with pytest.raises(ValueError,match='bounded'):
        calendar.window(days=100000,caller=lambda *a,**kw:pytest.fail('must bound before read'))
    rows=[{'id':'busy','title':'','start':'2026-09-09T10:00Z','end':'2026-09-09T11:00Z'}]
    assert len(calendar.overlaps('2026-09-09T10:00Z','2026-09-09T11:00Z',caller=lambda *a,**kw:rows))==1


def test_multiple_proposed_actions_are_refused_before_any_effect(monkeypatch):
    from notron.state import State
    from notron.executor import Executor
    monkeypatch.setattr(Executor,'do',lambda *a,**kw:pytest.fail('must not partially apply'))
    result=nodes.doer(State(request='Do my task',actions=[Action('reminder','create','One'),Action('reminder','create','Two')]))
    assert 'one' in result.answer and result.results[0].startswith('✗')


def test_changed_index_context_notice_reaches_answer_without_model_cooperation(monkeypatch):
    from notron.state import State
    from notron.outbound import Passage
    state=State(request='What is the dose?',intent='question',context=[Passage('Context unavailable','diagnostic')],context_incomplete=True)
    class Brain:
        def ask(self,**kw):return 'Answer'
    result=nodes.writer(state,brain=Brain())
    assert 'context is incomplete' in result.answer


def test_doer_keeps_actual_selected_reminder_id(monkeypatch):
    from notron.state import State
    monkeypatch.setattr(reminders,'find_open',lambda *a,**kw:[reminders.Reminder('selected','Buy milk','Inbox','')])
    monkeypatch.setattr(reminders,'complete',lambda rid:'Buy milk')
    state=nodes.doer(State(request='Complete Buy milk',actions=[Action('reminder','complete','Buy milk')]))
    assert state.actions[0].target_id=='selected'


def test_renamed_private_note_is_excluded_before_refresh_body(monkeypatch):
    from notron import notes, retention
    from notron.outbound import Passage
    monkeypatch.setattr(retention,'reconcile',lambda:None)
    monkeypatch.setattr(index,'_load',lambda:{'n1':[dict(note_id='n1',title='Old',folder='Notes',modified='old',text='Old dose',vector=[1.,0.])]})
    monkeypatch.setattr(notes,'get_note',lambda nid:notes.Note(nid,'Passwords','Notes','new'))
    monkeypatch.setattr(notes,'read_body',lambda nid:pytest.fail('private title must be excluded before body read'))
    class Brain:
        def embed(self,query):return [[1.,0.]]
    result=index.search([Passage('dose','user_request')],Brain())
    assert not result and result.incomplete


from test_action_recovery import recovery_harness


def test_graph_delivers_conflict_question_without_saving_or_claiming_success(recovery_harness,monkeypatch):
    from notron import requests
    h=recovery_harness
    h.kind='event'
    h.submit('conflict-request','Call dentist')
    monkeypatch.setattr(calendar,'overlaps',lambda start,end:[calendar.Event('Work','Busy',start,end,id='busy')])
    h.run_once()
    assert len(h.rows)==0
    assert 'confirmation code' in h.app.bodies[h.ask_id]
    assert 'In your calendar:' not in h.app.bodies[h.ask_id]
    assert requests.current().get('conflict-request').status=='completed'


def test_prepared_legacy_create_without_bound_target_requires_review(monkeypatch):
    from notron import requests,recovery,operations
    from notron.executor import Executor
    from dataclasses import asdict
    request=requests.create('Call dentist',request_id='legacy')
    requests.current().capture(request)
    requests.current().claim(request.request_id)
    action=Action('reminder','create','Call dentist')
    monkeypatch.setattr(reminders,'create',lambda *a,**kw:pytest.fail('legacy unbound action must not create'))
    with requests.execution(request):
        recovery.put(request.request_id,action.operation_id,{'action':asdict(action)},[])
        result=Executor(audit=False).do(action)
    assert not result.ok and operations.current().get(action.operation_id).status==operations.S.NEEDS_REVIEW


def test_prepared_event_conflict_is_cancelled_before_delivering_question(monkeypatch):
    from notron import requests,recovery,operations
    from notron.executor import Executor
    from dataclasses import asdict
    from datetime import timedelta
    request=requests.create('Schedule Call from 10am to 11am',request_id='prepared-conflict')
    requests.current().capture(request)
    requests.current().claim(request.request_id)
    start=(datetime.now(timezone.utc)+timedelta(days=2)).replace(hour=10,minute=0,second=0,microsecond=0)
    action=Action('event','create','Call',when=start.isoformat(timespec='minutes'),ends=(start+timedelta(hours=1)).isoformat(timespec='minutes'),target_id='work',timezone='UTC')
    monkeypatch.setattr(calendar,'overlaps',lambda *a:[calendar.Event('Work','Busy',action.when,action.ends,id='busy')])
    with requests.execution(request):
        recovery.put(request.request_id,action.operation_id,{'action':asdict(action)},[])
        result=Executor(audit=False).do(action,request=request.text)
    assert not result.ok and result.needs_confirmation
    assert operations.current().get(action.operation_id).status==operations.S.CANCELLED


def test_existing_recurring_reminder_is_not_completed_automatically(monkeypatch):
    from notron.executor import Executor
    monkeypatch.setattr(reminders,'open_items',lambda **kw:[reminders.Reminder('repeat','Call','Inbox','',recurring=True)])
    monkeypatch.setattr(reminders,'complete',lambda *a:pytest.fail('must not complete recurrence'))
    result=Executor(audit=False).do(Action('reminder','complete','Call'))
    assert not result.ok and result.needs_confirmation and 'recurring' in result.reason
