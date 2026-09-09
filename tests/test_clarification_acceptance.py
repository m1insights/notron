"""Real graph and encrypted ledger; only model/native adapters are synthetic."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from notron import calendar, clarifications, conversation, graph, markup, notes, operations, reminders, requests, workspace


@pytest.fixture
def exchange(monkeypatch, _notes_is_never_the_real_one):
    app = _notes_is_never_the_real_one
    nid = f'{workspace.FOLDER}/{workspace.ASK}'
    h = SimpleNamespace(rows=[], targets=[{'id':'work','title':'Work'}, {'id':'home','title':'Home'}], date=None, kind="reminder", ends=None, op="create", source="ask")
    monkeypatch.setattr(notes, 'write_body', lambda note_id, body: app.bodies.__setitem__(note_id,body))
    def targets(name='', target_id=None, **kw):
        return [t for t in h.targets if (not target_id or t['id']==target_id) and (not name or t['title']==name)]
    monkeypatch.setattr(reminders,'resolve_targets',targets)
    monkeypatch.setattr(calendar,'resolve_targets',targets)
    monkeypatch.setattr(calendar,'overlaps',lambda *a,**kw: [])
    def create(title, **kw):
        h.rows.append({'title':title, **kw})
        return 'reminder-'+str(len(h.rows))
    monkeypatch.setattr(reminders,'create',create)
    monkeypatch.setattr(calendar,'create', lambda title, *args, **kw: create(title, **kw))
    class Brain:
        def ask_json(self, **kw):
            if kw['purpose']=='route':
                return {'intent':'question'} if h.text=='yes' or h.text.startswith('What ') else {'intent':'schedule' if h.kind=='event' else 'remind'}
            return {'kind':h.kind,'op':h.op,'title':'Buy milk','when':h.date,'ends':h.ends,'clear':True}
        def ask(self, **kw):
            return 'Please say which action you mean.'
    def submit(text, *, new_topic=False, hook=None):
        h.text=text
        prior=app.bodies.get(nid)
        app.bodies[nid]=(prior or markup.render(workspace.ASK,''))+markup.render('', ('New topic\n\n' if new_topic else '')+text)
        body=app.bodies[nid]
        qs=conversation.unanswered(body,ignore=(workspace.ASK,))
        envelopes=requests.current().observe(nid,body,qs,source=h.source,title=workspace.ASK,folder=workspace.FOLDER)
        h.envelope=[e for e in envelopes if e.source_text==text][-1]
        h.state=graph.run_request(h.envelope,brain=Brain(),on_node=hook)
        return h.state
    h.submit,h.app,h.nid,h.brain=submit,app,nid,Brain()
    return h


def test_list_reply_fulfills_original_action_once(exchange):
    h=exchange
    first=h.submit('remind me to buy milk')
    assert not h.rows and 'Which existing' in first.answer
    assert clarifications.current().latest(first.envelope.thread_id)
    result=h.submit('Work')
    assert len(h.rows)==1, result.answer
    assert h.rows[0]['title']=='Buy milk' and h.rows[0]['target_id']=='work'
    assert result.actions[0].origin_request_id==first.request_id
    assert all(w.title != workspace.MEMORY for w in result.writes)
    replay=graph.run_request(h.envelope,brain=h.brain)
    assert len(h.rows)==1
    assert replay.answer=='This request was already completed.'


def test_delayed_date_reply_fulfills_saved_task_once(exchange):
    h=exchange
    h.targets=[{'id':'work','title':'Work'}]
    first=h.submit('remind me tomorrow to buy milk')
    assert not h.rows and 'exact date' in first.answer
    day=(datetime.now()+timedelta(days=5)).strftime('%Y-%m-%d')
    result=h.submit(day)
    assert len(h.rows)==1, result.answer
    assert h.rows[0]['when_iso']==day and h.rows[0]['title']=='Buy milk'
    assert result.actions[0].origin_request_id==first.request_id
    assert all(w.title != workspace.MEMORY for w in result.writes)
    graph.run_request(h.envelope,brain=h.brain)
    assert len(h.rows)==1


def test_wrong_thread_yes_cannot_fulfill(exchange):
    h=exchange
    h.submit('remind me to buy milk')
    result=h.submit('yes',new_topic=True)
    assert not h.rows and not result.actions


def test_target_renamed_after_selection_prevents_fulfillment(exchange):
    h=exchange
    h.submit('remind me to buy milk')
    def change(name,state):
        if name=='scheduler':
            h.targets=[{'id':'work','title':'Renamed'}]
    result=h.submit('Work',hook=change)
    assert not h.rows and 'changed' in result.answer


def test_original_source_edited_after_resolution_prevents_fulfillment(exchange):
    h=exchange
    h.submit('remind me to buy milk')
    def change(name,state):
        if name=='scheduler':
            h.app.bodies[h.nid]=h.app.bodies[h.nid].replace('remind me to buy milk','remind me to buy tea')
    result=h.submit('Work',hook=change)
    assert not h.rows and 'changed' in result.answer

def test_permission_revoked_before_fulfillment_has_no_effect(exchange, monkeypatch):
    h=exchange
    h.submit('remind me to buy milk')
    def denied(*args, **kwargs):
        raise PermissionError('Reminders permission denied')
    def change(name,state):
        if name=='scheduler':
            monkeypatch.setattr(reminders,'resolve_targets',denied)
    result=h.submit('Work',hook=change)
    assert not h.rows and 'unavailable' in result.answer


def test_conflict_yes_is_bound_to_exact_proposal(exchange, monkeypatch):
    h=exchange
    h.kind='event'
    h.targets=[{'id':'work','title':'Work'}]
    day=(datetime.now()+timedelta(days=5)).strftime('%Y-%m-%d')
    h.date,h.ends=day+'T14:00',day+'T15:00'
    monkeypatch.setattr(calendar,'overlaps',lambda *a,**kw: [object()])
    monkeypatch.setattr(calendar,'conflict_token',lambda *a: 'token-original')
    first=h.submit('create meeting '+day+' from 14:00 to 15:00')
    assert not h.rows and 'anyway' in first.answer
    result=h.submit('yes')
    assert len(h.rows)==1, result.answer
    assert result.actions[0].origin_request_id==first.request_id
    assert all(w.title != workspace.MEMORY for w in result.writes)
    graph.run_request(h.envelope,brain=h.brain)
    assert len(h.rows)==1


def test_changed_conflict_invalidates_yes(exchange, monkeypatch):
    h=exchange
    h.kind='event'
    h.targets=[{'id':'work','title':'Work'}]
    day=(datetime.now()+timedelta(days=5)).strftime('%Y-%m-%d')
    h.date,h.ends=day+'T14:00',day+'T15:00'
    monkeypatch.setattr(calendar,'overlaps',lambda *a,**kw: [object()])
    monkeypatch.setattr(calendar,'conflict_token',lambda *a: 'token-original')
    h.submit('create meeting '+day+' from 14:00 to 15:00')
    monkeypatch.setattr(calendar,'conflict_token',lambda *a: 'token-changed')
    result=h.submit('yes')
    assert not h.rows and 'anyway' in result.answer


@pytest.mark.parametrize('field,value', [('title','Buy milk tomorrow'),('list_name','Renamed'),('due','2099-01-01'),('recurring',True)])
def test_changed_reminder_candidate_is_not_completed(exchange, monkeypatch, field, value):
    from dataclasses import replace
    h=exchange
    h.op='complete'
    items=[reminders.Reminder('one','Buy milk','Work',''), reminders.Reminder('two','Buy milk','Home','')]
    monkeypatch.setattr(reminders,'open_items',lambda: items)
    monkeypatch.setattr(reminders,'complete',lambda rid: h.rows.append(rid))
    h.submit('complete Buy milk')
    assert not h.rows
    def change(name,state):
        if name=='scheduler':
            items[0]=replace(items[0], **{field:value})
    result=h.submit('Buy milk [Work]',hook=change)
    assert not h.rows and 'changed' in result.answer


def test_multiline_tagged_origin_fulfills_once(exchange):
    h=exchange
    h.source='mention'
    first=h.submit('Shopping context\n@notron remind me to buy milk')
    assert not h.rows and 'Which existing' in first.answer
    result=h.submit('@notron Work')
    assert len(h.rows)==1, result.answer
    assert result.actions[0].origin_request_id==first.request_id


def test_changed_prefix_before_original_refuses_consent(exchange):
    h=exchange
    h.app.bodies[h.nid]=markup.render(workspace.ASK,'Earlier context\n\n'+conversation.turn('Earlier answer'))
    h.submit('remind me to buy milk')
    h.app.bodies[h.nid]=h.app.bodies[h.nid].replace('Earlier context','Changed context')
    result=h.submit('Work')
    assert not h.rows and 'changed' in result.answer


def test_unrelated_question_cancels_old_consent(exchange):
    h=exchange
    h.submit('remind me to buy milk')
    h.submit('What is dark matter?')
    result=h.submit('yes')
    assert not h.rows and not result.actions
