"""Synthetic Notes → durable request → production graph acceptance cases."""
import json

import pytest

from notron import conversation, graph, markup, requests, workspace


@pytest.mark.parametrize('question_text,mode,expected', [
    ('Can you dumb it down a bit for me?', 'transform', 'transform'),
    ('Expand on that', 'transform', 'transform'),
    ('New topic\n\nMake that simpler', 'transform', 'clarify'),
    ('What about the other one?', 'clarify', 'clarify'),
    ('What is the latest evidence for it?', 'answer', 'answer'),
    ('Move that meeting to Friday', 'answer', 'clarify'),
])
def test_notes_conversation_cases(_notes_is_never_the_real_one, outbound_transport, question_text, mode, expected):
    app = _notes_is_never_the_real_one
    brain, calls = outbound_transport
    nid = f'{workspace.FOLDER}/{workspace.ASK}'
    body = markup.render(workspace.ASK, 'What are dark energy and dark matter?\n\n' +
                         conversation.turn('Dark matter pulls; dark energy expands the universe.') + '\n' + question_text)
    app.bodies[nid] = body
    q = conversation.unanswered(body, ignore=(workspace.ASK,))[-1]
    envelope = requests.current().observe(nid, body, [q], source='ask', title=workspace.ASK,
                                          folder=workspace.FOLDER)[0]
    calls.replies.extend([json.dumps({'intent': 'question', 'response_mode': mode,
                                     'resolved_request': 'latest evidence for dark energy',
                                     'needs_web': mode == 'answer'}), 'A simpler explanation.'])
    state = graph.run_request(envelope, brain=brain, dry_run=True)
    assert state.response_mode == expected
    assert state.answer and not state.actions
    if expected in ('transform', 'clarify'):
        assert calls.search == []
    if expected == 'transform':
        assert 'expands the universe' in str(calls.chat)
    assert all(w.title != workspace.MEMORY for w in state.writes)


def test_inserted_top_question_never_sees_later_answer(_notes_is_never_the_real_one, outbound_transport):
    app = _notes_is_never_the_real_one
    brain, calls = outbound_transport
    nid = f'{workspace.FOLDER}/{workspace.ASK}'
    body = markup.render(workspace.ASK, 'Make that simpler\n\n———\n\nOld question?\n\n' +
                         conversation.turn('FUTURE_ANSWER'))
    app.bodies[nid] = body
    q = conversation.unanswered(body, ignore=(workspace.ASK,))[0]
    envelope = requests.current().observe(nid, body, [q], source='ask', title=workspace.ASK, folder=workspace.FOLDER)[0]
    calls.replies.append(json.dumps({'intent': 'question', 'response_mode': 'transform'}))
    state = graph.run_request(envelope, brain=brain, dry_run=True)
    assert state.response_mode == 'clarify'
    assert 'FUTURE_ANSWER' not in str(calls.chat)


def test_tagged_followup_retains_local_thought_but_no_future_or_excess_history(_notes_is_never_the_real_one, outbound_transport):
    from notron import library
    app = _notes_is_never_the_real_one
    brain, calls = outbound_transport
    nid = 'Notes/Parking Garages'
    selected = library.load()
    selected.decided.add(nid)
    library.save(selected)
    body = markup.render('Parking Garages', '@notron Explain gravity\n\n' + conversation.turn('Things attract.') +
                         '\nLocal thought\n@notron make that simpler\n\n———\n\n@notron LATER_QUESTION\n\n' + conversation.turn('FUTURE_ANSWER'))
    app.bodies[nid] = body
    q = conversation.unanswered(body, ignore=('Parking Garages',), require_tag=True)[0]
    envelope = requests.current().observe(nid, body, [q], source='mention', title='Parking Garages', folder='Notes')[0]
    calls.replies.extend([json.dumps({'intent': 'question', 'response_mode': 'transform'}), 'Simple gravity.'])
    state = graph.run_request(envelope, brain=brain, dry_run=True)
    assert state.response_mode == 'transform'
    prompt = str(calls.chat)
    assert 'Local thought' in prompt
    assert 'FUTURE_ANSWER' not in prompt and 'LATER_QUESTION' not in prompt
    assert state.here == 'Local thought\n@notron make that simpler'


def test_deleting_latest_answer_does_not_transform_older_answer(_notes_is_never_the_real_one, outbound_transport):
    app = _notes_is_never_the_real_one
    brain, calls = outbound_transport
    nid = f'{workspace.FOLDER}/{workspace.ASK}'
    body = markup.render(workspace.ASK, 'First?\n\n' + conversation.turn('OLDER_ANSWER') +
                         '\nLatest question?\n\n———\n\nMake that simpler')
    app.bodies[nid] = body
    qs = conversation.unanswered(body, ignore=(workspace.ASK,))
    envelope = requests.current().observe(nid, body, qs, source='ask', title=workspace.ASK, folder=workspace.FOLDER)[-1]
    calls.replies.append(json.dumps({'intent': 'question', 'response_mode': 'transform'}))
    state = graph.run_request(envelope, brain=brain, dry_run=True)
    assert state.response_mode == 'clarify'
    assert 'OLDER_ANSWER' not in str(calls.chat)


def test_secret_in_history_is_redacted_at_live_transport(_notes_is_never_the_real_one, outbound_transport):
    app = _notes_is_never_the_real_one
    brain, calls = outbound_transport
    nid = f'{workspace.FOLDER}/{workspace.ASK}'
    secret = 'sk-' + 'a' * 40
    body = markup.render(workspace.ASK, 'Prior?\n\n' + conversation.turn('Secret ' + secret) + '\nSummarize that')
    app.bodies[nid] = body
    q = conversation.unanswered(body, ignore=(workspace.ASK,))[-1]
    envelope = requests.current().observe(nid, body, [q], source='ask', title=workspace.ASK, folder=workspace.FOLDER)[0]
    calls.replies.extend([json.dumps({'intent': 'question', 'response_mode': 'transform'}), 'Summary'])
    state = graph.run_request(envelope, brain=brain, dry_run=True)
    assert secret not in str(calls.chat) and secret not in str(state.conversation)
