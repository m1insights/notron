from datetime import datetime, timedelta, timezone

import pytest
from notron import clarifications, operations
from notron.securestore import EncryptedStore

@pytest.fixture
def store(tmp_path):
    return clarifications.ClarificationStore(operations.OperationStore(tmp_path/'ledger.db', EncryptedStore(tmp_path/'payloads', b'k'*32)))

def add(store, **kw):
    return store.add(request_id='r1', thread_id='a', question='Which list?', candidate_ids=['work','home'], source_revision='rev', proposal={'title':'Buy milk'}, candidates={'work':'Work','home':'Home'}, **kw)

def test_yes_in_another_thread_is_not_consent(store):
    c = add(store)
    assert store.resolve(c.id, reply='yes', thread_id='b', live_revision='rev').status == 'unmatched'

def test_natural_list_reply_consumes_exact_original_proposal_once(store):
    c = add(store)
    result = store.resolve(c.id, reply='Work', thread_id='a', live_revision='rev')
    assert result.status == 'resolved'
    assert result.request_id == 'r1'
    assert result.proposal == {'title':'Buy milk', 'target_id':'work'}
    assert store.resolve(c.id, reply='Work', thread_id='a', live_revision='rev').status == 'unmatched'

def test_ambiguous_yes_does_not_choose_target(store):
    c = add(store)
    assert store.resolve(c.id, reply='yes', thread_id='a', live_revision='rev').status == 'clarify'
    assert store.latest('a').id == c.id

def test_changed_proposal_expires(store):
    c = add(store)
    assert store.resolve(c.id, reply='Work', thread_id='a', live_revision='changed').status == 'expired'

def test_twenty_four_hour_expiry(store):
    c = add(store, expires_at=datetime.now(timezone.utc)-timedelta(seconds=1))
    assert store.resolve(c.id, reply='Work', thread_id='a', live_revision='rev').status == 'expired'

def test_sensitive_fields_are_encrypted_and_restart_safe(store):
    c = add(store)
    assert b'Buy milk' not in store.operations.path.read_bytes()
    restarted = clarifications.ClarificationStore(store.operations)
    assert restarted.latest('a').proposal == {'title':'Buy milk'}

def test_action_references_require_applied_external_identity(store):
    ops=store.operations
    import json
    from hashlib import sha256
    payload=json.dumps({'action': {'kind': 'event', 'title':'Meeting'}}).encode()
    ops.prepare('r1','o1',sha256(payload).hexdigest(),payload=payload,content_source_ids=[])
    assert ops.action_references(['r1']) == []
    ops.transition('o1',operations.S.PREPARED,operations.S.APPLYING)
    assert ops.action_references(['r1']) == []
    ops.transition('o1',operations.S.APPLYING,operations.S.APPLIED,external_id='event1')
    assert ops.action_references(['r1'])[0].external_id == 'event1'

def test_resolution_is_recoverable_only_by_consuming_reply(store):
    c = add(store)
    store.resolve(c.id, reply='Work', thread_id='a', live_revision='rev', reply_request_id='reply1')
    assert store.for_reply('reply1').proposal['target_id'] == 'work'
    assert store.for_reply('reply2') is None

def test_move_meeting_never_calls_model():
    from notron.nodes import scheduler
    from notron.state import State
    state=State(intent='schedule', request='move that meeting to tomorrow')
    class Brain:
        def ask_json(self, **kwargs):
            pytest.fail('Unsupported edit must not reach inference')
    scheduler(state, brain=Brain())
    assert not state.actions and 'create' in state.answer.lower()

def test_date_reply_resumes_saved_task_not_unrelated_schedule(monkeypatch):
    from notron import requests, conversation
    from notron.state import State
    from notron.nodes import scheduler
    original=requests.create('remind me tomorrow to buy milk', source='ask', thread_id='thread', timezone_name='America/New_York')
    requests.current().capture(original)
    ctx=conversation.ConversationContext('thread', [], [])
    state=State(request_id=original.request_id, envelope=original, request=original.text, conversation=ctx, intent='remind')
    class Brain:
        def ask_json(self, **kwargs):
            return {'kind':'reminder','op':'create','title':'buy milk','when':'2026-09-10','clear':True}
    scheduler(state, brain=Brain())
    assert not state.actions and 'exact date' in state.answer
    reply=requests.create('2026-12-01', source='ask', thread_id='thread', timezone_name='America/New_York')
    requests.current().capture(reply)
    ctx=conversation.ConversationContext('thread', [conversation.Turn('user',original.text), conversation.Turn('assistant',state.answer)], [])
    next_state=State(request_id=reply.request_id, envelope=reply, request=reply.text, conversation=ctx)
    assert clarifications.resume(next_state)
    assert next_state.actions[0].title == 'buy milk'
    assert next_state.actions[0].when == '2026-12-01'
    assert next_state.action_request_id == original.request_id
    assert next_state.actions[0].origin_request_id == original.request_id
    retried=State(request_id=reply.request_id, envelope=reply, request=reply.text, conversation=ctx)
    assert clarifications.resume(retried)
    assert retried.actions[0] == next_state.actions[0]

def test_deleted_original_task_does_not_consume_reply(monkeypatch):
    from notron import requests, conversation
    from notron.state import State
    original=requests.create('create milk reminder', source='ask', thread_id='thread', timezone_name='America/New_York')
    requests.current().capture(original)
    c=clarifications.current().add(request_id=original.request_id, thread_id='thread', candidate_ids=['work'], proposal={'kind':'reminder','op':'create','title':'milk'}, source_revision=requests.revision(original.text))
    reply=requests.create('yes', source='ask', thread_id='thread', timezone_name='America/New_York')
    state=State(request_id=reply.request_id, envelope=reply, request='yes', conversation=conversation.ConversationContext('thread',[],[]))
    assert clarifications.resume(state)
    assert not state.actions
    assert clarifications.current().latest('thread').id == c.id

def test_complete_that_uses_verified_reminder_id(monkeypatch):
    import json
    from hashlib import sha256
    from notron import conversation
    from notron.nodes import scheduler
    from notron.state import State
    ops=operations.current()
    payload=json.dumps({'action': {'kind':'reminder','op':'create','title':'Milk','where':'Work'}}).encode()
    ops.prepare('origin','op',sha256(payload).hexdigest(),payload=payload,content_source_ids=[])
    ops.transition('op',operations.S.PREPARED,operations.S.APPLYING)
    ops.transition('op',operations.S.APPLYING,operations.S.APPLIED,external_id='actual-reminder')
    state=State(request='complete that reminder', intent='remind', conversation=conversation.ConversationContext('thread',[],['actual-reminder']))
    scheduler(state, brain=None)
    assert state.actions[0].target_id == 'actual-reminder'
    assert state.actions[0].op == 'complete'

def test_assistant_claim_without_verified_id_cannot_complete():
    from notron import conversation
    from notron.nodes import scheduler
    from notron.state import State
    state=State(request='complete that reminder', intent='remind', conversation=conversation.ConversationContext('thread',[conversation.Turn('assistant','Reminder set: Milk')],[]))
    scheduler(state, brain=None)
    assert not state.actions and 'Which reminder' in state.answer

def test_expired_sensitive_proposals_are_erased_but_cannot_authorize(store):
    c=add(store, expires_at=datetime.now(timezone.utc)-timedelta(seconds=1))
    store.prune()
    pending=store.latest('a')
    assert pending.proposal == {} and pending.candidate_ids == []
    assert store.resolve(c.id,reply='Work',thread_id='a',live_revision='rev').status=='expired'
    for path in store.operations.payload_store.root.glob('operation-*.enc'):
        assert b'Buy milk' not in store.operations.payload_store.read(path.stem)
