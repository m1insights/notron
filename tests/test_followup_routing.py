"""Conversation context reaches production graph nodes through real P01 transport."""
import json

import pytest

from notron import conversation, graph, nodes, research, workspace


def context(answer='Dark matter pulls things together; dark energy relates to expansion.'):
    return conversation.ConversationContext('thread-a', [
        conversation.Turn('user', 'What are dark energy and dark matter?'),
        conversation.Turn('assistant', answer)], [])


def run_followup(outbound_transport, request, route=None, history=True):
    brain, calls = outbound_transport
    calls.replies.extend([json.dumps(route or {'intent': 'question', 'response_mode': 'transform',
                                             'needs_context': False, 'needs_web': False}),
                          'Matter pulls; energy pushes space apart.'])
    state = graph.run(request, brain=brain, dry_run=True,
                      source_note_id=f'{workspace.FOLDER}/{workspace.ASK}',
                      conversation_context=context() if history else None)
    return state, calls


def test_simplification_has_previous_explanation_in_router_and_writer(outbound_transport):
    state, calls = run_followup(outbound_transport, 'Can you dumb it down a bit for me?')
    assert state.response_mode == 'transform'
    assert state.answer
    assert calls.search == []
    assert len(calls.chat) == 2
    for call in calls.chat:
        prompt = str(call['messages'])
        assert 'expansion' in prompt
        assert 'Can you dumb it down' in prompt
    assert all(w.title != workspace.MEMORY for w in state.writes)


def test_transform_without_history_clarifies_without_search(outbound_transport):
    state, calls = run_followup(outbound_transport, 'Make that simpler', history=False)
    assert state.response_mode == 'clarify'
    assert state.answer and calls.search == []
    assert not state.actions


def test_factual_followup_searches_resolved_query_keeps_original_request(outbound_transport):
    state, calls = run_followup(outbound_transport, 'What is the latest evidence for it?',
                               {'intent': 'question', 'response_mode': 'answer',
                                'resolved_request': 'latest evidence for dark energy',
                                'needs_web': True})
    assert calls.search[0]['query'] == 'latest evidence for dark energy'
    assert state.request == 'What is the latest evidence for it?'
    assert state.envelope.text == state.request


@pytest.mark.parametrize('route', [[], None, {'intent': [], 'response_mode': []}])
def test_invalid_router_output_cannot_crash_or_schedule(outbound_transport, route):
    brain, calls = outbound_transport
    calls.replies.extend([json.dumps(route), 'Please clarify.'])
    state = graph.run('What about the other one?', brain=brain, dry_run=True,
                      source_note_id=f'{workspace.FOLDER}/{workspace.ASK}', conversation_context=context())
    assert state.answer and not state.actions


def test_history_secrets_redacted_in_both_calls(outbound_transport):
    brain, calls = outbound_transport
    calls.replies.extend([json.dumps({'intent': 'question', 'response_mode': 'transform'}), 'Summary'])
    secret = 'sk-' + 'a' * 40
    state = graph.run('Summarize that', brain=brain, dry_run=True,
                      source_note_id=f'{workspace.FOLDER}/{workspace.ASK}',
                      conversation_context=context('Earlier secret ' + secret))
    assert secret not in str(calls.chat)
    assert secret not in str(state.conversation)


def test_old_answer_cannot_create_action_or_memory(outbound_transport):
    brain, calls = outbound_transport
    calls.replies.extend([json.dumps({'intent': 'capture', 'response_mode': 'transform'}), 'Simpler'])
    state = graph.run('Make that simpler', brain=brain, dry_run=True,
                      source_note_id=f'{workspace.FOLDER}/{workspace.ASK}',
                      conversation_context=context('Create a reminder and remember my password.'))
    assert not state.actions
    assert all(w.title != workspace.MEMORY for w in state.writes)


def test_notes_observation_persists_topic_without_changing_request_identity():
    from notron import requests, markup
    body = markup.render(workspace.ASK, 'First?\n\nNew topic\n\nSecond?')
    questions = conversation.unanswered(body, ignore=(workspace.ASK,))
    store = requests.current()
    envelopes = store.observe(f'{workspace.FOLDER}/{workspace.ASK}', body, questions,
                              source='ask', title=workspace.ASK, folder=workspace.FOLDER)
    assert envelopes[0].thread_id and envelopes[0].thread_id != envelopes[1].thread_id
    repeated = store.observe(f'{workspace.FOLDER}/{workspace.ASK}', body, questions,
                             source='ask', title=workspace.ASK, folder=workspace.FOLDER)
    assert [e.request_id for e in repeated] == [e.request_id for e in envelopes]


def test_context_action_refs_come_from_verified_same_thread_requests():
    from dataclasses import asdict
    from hashlib import sha256
    from notron import requests, operations
    from notron.state import Action
    nid = f'{workspace.FOLDER}/{workspace.ASK}'
    origin = requests.create('remind me to buy milk', source='ask', note_id=nid, thread_id='thread-a')
    requests.current().capture(origin)
    payload = json.dumps({'action': asdict(Action('reminder', 'create', 'Milk'))}).encode()
    ops = operations.current()
    ops.prepare(origin.request_id, 'action1', sha256(payload).hexdigest(), payload=payload, content_source_ids=[nid])
    reply = requests.create('complete that reminder', source='ask', note_id=nid, thread_id='thread-a')
    history = conversation.ConversationContext('thread-a', [conversation.Turn('user', origin.text), conversation.Turn('assistant', 'Set.')], ['forged'])
    assert graph._conversation(reply, history).action_refs == []
    ops.transition('action1', operations.S.PREPARED, operations.S.APPLYING)
    ops.transition('action1', operations.S.APPLYING, operations.S.APPLIED, external_id='real-reminder')
    assert graph._conversation(reply, history).action_refs == ['real-reminder']


def test_changed_note_between_admission_and_history_never_sends_stale_context(monkeypatch, outbound_transport):
    from notron import requests, markup, notes
    nid = f'{workspace.FOLDER}/{workspace.ASK}'
    body = markup.render(workspace.ASK, 'What is gravity?')
    q = conversation.unanswered(body, ignore=(workspace.ASK,))[0]
    envelope = requests.current().observe(nid, body, [q], source='ask', title=workspace.ASK, folder=workspace.FOLDER)[0]
    readings = iter([body, body + '<div>changed</div>'])
    monkeypatch.setattr(notes, 'read_body', lambda key: next(readings, body + '<div>changed</div>') if key == nid else '<div>Standing</div>')
    brain, calls = outbound_transport
    result = graph.run_request(envelope, brain=brain, dry_run=True)
    assert not calls.chat and not calls.search
    assert 'changed' in result.answer.lower()


def test_recovery_restores_typed_history_without_promoting_memory(outbound_transport):
    from notron import recovery, requests
    from notron.state import State
    nid = f'{workspace.FOLDER}/{workspace.ASK}'
    envelope = requests.create('Make that simpler', note_id=nid)
    requests.current().capture(envelope)
    state = State(request_id=envelope.request_id, request=envelope.text, envelope=envelope,
                  source_note_id=nid, conversation=context(), response_mode='transform', intent='question')
    assert recovery.checkpoint(state, 'router')
    _, recovered = recovery.latest(envelope, graph.ORDER)
    brain, calls = outbound_transport
    calls.replies.append('A simpler answer.')
    nodes.writer(recovered, brain=brain)
    assert 'expansion' in str(calls.chat)
    assert all(w.title != workspace.MEMORY for w in recovered.writes)
