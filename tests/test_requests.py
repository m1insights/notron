"""Stable occurrence identity, source changes and graph admission at real storage boundaries."""
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from notron import graph, requests
from notron.requests import RequestEnvelope, RequestStore
from notron.operations import OperationConflict, OperationStatus


def observe(store, body='body-v1', *, note_id='n1', items=None):
    from notron.conversation import Question
    items = items if items is not None else [('remind me to call', 3)]
    return store.observe(note_id, body, [Question(text, after) for text, after in items],
                         source='ask', title='Ask', folder='Notes')


def test_restart_before_inference_and_unrelated_edit_keep_identity():
    store = requests.current()
    first = observe(store)[0]
    restarted = RequestStore(store.operations)
    second = observe(restarted, 'unrelated edit', items=[('remind me to call', 8)])[0]
    assert second.request_id == first.request_id
    assert second.observed_at == first.observed_at
    assert second.captured_at is None and second.capture_confidence == 'observed_only'
    assert restarted.get(first.request_id).envelope == second


def test_identical_reminders_submitted_separately_are_distinct():
    store = requests.current()
    first = observe(store)[0]
    assert store.claim(first.request_id)
    store.finish(first.request_id)
    # A completed occurrence still present is not a newly submitted request.
    assert observe(store)[0].request_id == first.request_id
    observe(store, 'answer received', items=[])
    again = observe(store, 'later reminder')[0]
    other_note = observe(store, note_id='n2')[0]
    assert len({first.request_id, again.request_id, other_note.request_id}) == 3


def test_two_initial_identical_occurrences_have_distinct_ids():
    envelopes = observe(requests.current(), items=[('remind me to call', 3), ('remind me to call', 9)])
    assert len({e.request_id for e in envelopes}) == 2


def test_changed_request_cancels_old_before_execution():
    store = requests.current()
    before = observe(store)[0]
    after = observe(store, 'changed body', items=[('remind me to write', 3)])[0]
    assert after.request_id != before.request_id
    assert store.get(before.request_id).status == 'cancelled'
    assert not store.claim(before.request_id)


def test_copy_of_pending_occurrence_requires_review():
    store = requests.current()
    first = observe(store)[0]
    copies = observe(store, 'copied body', items=[('remind me to call', 3), ('remind me to call', 9)])
    assert copies
    assert all(store.get(e.request_id).status == 'needs_review' for e in copies)
    assert store.get(first.request_id).status == 'needs_review'


def test_reordered_pending_occurrences_require_review():
    store = requests.current()
    first = observe(store, items=[('one', 3), ('two', 9)])
    after = observe(store, 'reordered', items=[('two', 3), ('one', 9)])
    assert {e.request_id for e in first} == {e.request_id for e in after}
    assert all(store.get(e.request_id).status == 'needs_review' for e in after)


def test_envelope_validates_aware_time_and_identity():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError):
        RequestEnvelope(1, 'r', 'cli', 'hi', None, now.replace(tzinfo=None), 'UTC', 'observed_only')
    with pytest.raises(ValueError):
        RequestEnvelope(1, 'r', 'cli', 'hi', None, now, 'not-a-zone', 'observed_only')


def test_supplied_request_id_is_immutable_and_replay_does_not_infer(monkeypatch):
    calls = []
    def node(state, **kw):
        record = requests.current().get('caller-id')
        assert record.envelope.text == 'hello'
        assert record.status == 'running'
        calls.append(state.request_id)
        return state
    monkeypatch.setattr(graph, 'ORDER', ('router',))
    monkeypatch.setitem(graph.NODES, 'router', node)
    first = graph.run('hello', brain=None, request_id='caller-id')
    second = graph.run('hello', brain=None, request_id='caller-id')
    assert first.request_id == second.request_id == 'caller-id'
    assert calls == ['caller-id']
    with pytest.raises(OperationConflict):
        graph.run('different', brain=None, request_id='caller-id')


def test_interruption_cannot_blindly_restart_inference(monkeypatch):
    calls = []
    def node(state, **kw):
        calls.append(1)
        raise RuntimeError('synthetic failure')
    monkeypatch.setattr(graph, 'ORDER', ('router',))
    monkeypatch.setitem(graph.NODES, 'router', node)
    with pytest.raises(RuntimeError):
        graph.run('hello', brain=None, request_id='interrupted')
    graph.run('hello', brain=None, request_id='interrupted')
    assert calls == [1]
    assert requests.current().get('interrupted').status == 'needs_review'


def test_dry_run_does_not_complete_real_request(monkeypatch):
    monkeypatch.setattr(graph, 'ORDER', ())
    graph.run('hello', brain=None, request_id='dry', dry_run=True)
    assert requests.current().get('dry').status == 'prepared'
    graph.run('hello', brain=None, request_id='dry')
    assert requests.current().get('dry').status == 'completed'


def test_request_body_only_persists_encrypted():
    store = requests.current()
    text = 'synthetic confidential sentence for storage test'
    observe(store, items=[(text, 3)])
    for file in store.operations.path.parent.rglob('*'):
        if file.is_file():
            assert text.encode() not in file.read_bytes()


def test_source_change_between_observation_and_graph_prevents_inference(monkeypatch):
    store = requests.current()
    envelope = observe(store)[0]
    from notron.notes import Note
    monkeypatch.setattr('notron.notes.list_all_notes', lambda: [Note('n1', 'Ask', 'Notes', 'today')])
    monkeypatch.setattr('notron.notes.read_body', lambda nid: 'changed after settling')
    monkeypatch.setattr(graph, 'ORDER', ('router',))
    def forbidden(*a, **kw):
        pytest.fail('Inference ran on an obsolete source occurrence')
    monkeypatch.setitem(graph.NODES, 'router', forbidden)
    result = graph.run_request(envelope, brain=None)
    assert 'source changed' in result.answer.lower()
    assert store.get(envelope.request_id).status == 'prepared'


def test_revocation_purges_request_and_operation_payloads():
    from hashlib import sha256
    from notron import library
    store = requests.current()
    envelope = observe(store)[0]
    body = b'synthetic request operation payload'
    store.operations.prepare(envelope.request_id, 'op', sha256(body).hexdigest(),
                             payload=body, source_id='n1', content_source_ids=('n1',))
    lib = library.load()
    lib.ignore.add('n1')
    library.save(lib)
    record = store.get(envelope.request_id)
    assert record.envelope is None
    assert record.status == 'needs_review'
    assert store.operations.payload('op') is None
    assert list(store.operations.payload_store.root.glob('operation-*.enc')) == []
    with pytest.raises(OperationConflict):
        store.capture(envelope)


def test_deleted_source_purges_after_successful_inventory(monkeypatch):
    from notron import retention, notes
    store = requests.current()
    envelope = observe(store)[0]
    monkeypatch.setattr(notes, 'list_all_notes', lambda: [])
    retention.reconcile()
    assert store.get(envelope.request_id).envelope is None


def test_same_request_cannot_be_claimed_twice():
    store = requests.current()
    envelope = observe(store)[0]
    assert store.claim(envelope.request_id)
    reopened = RequestStore(store.operations)
    assert not reopened.claim(envelope.request_id)


def test_cli_supplied_id_reaches_real_ledger(monkeypatch, capsys):
    from notron import cli
    monkeypatch.setattr(cli, '_brain', lambda: None)
    monkeypatch.setattr(graph, 'ORDER', ())
    monkeypatch.setattr('sys.argv', ['notron', 'ask', '--quiet', '--request-id', 'cli-supplied', 'hello'])
    cli.main()
    assert requests.current().get('cli-supplied').envelope.text == 'hello'


def test_request_envelope_does_not_grant_reply_capability(monkeypatch):
    from notron import policy
    assert policy.request_note_id() is None
    envelope = requests.create('hello', source='mention', note_id='n1')
    def node(state, **kw):
        assert policy.request_note_id() is None
        return state
    monkeypatch.setattr(graph, 'ORDER', ('router',))
    monkeypatch.setitem(graph.NODES, 'router', node)
    graph.run_request(envelope, brain=None, dry_run=True)


def test_capture_retains_iana_timezone_without_inventing_notes_capture(monkeypatch):
    monkeypatch.setenv('TZ', 'America/New_York')
    envelope = requests.create('tomorrow', source='mention')
    assert envelope.timezone == 'America/New_York'
    assert envelope.captured_at is None
    assert requests.create('hello', timezone_name='Europe/London').timezone == 'Europe/London'


def test_invalid_configured_timezone_fails_closed(monkeypatch):
    monkeypatch.setenv('TZ', 'not-a-real-zone')
    with pytest.raises(ValueError):
        requests.create('tomorrow')


def test_regrant_cannot_resurrect_purged_occurrence():
    from notron import library
    store = requests.current()
    observe(store)
    lib = library.load()
    lib.ignore.add('n1')
    library.save(lib)
    lib.ignore.remove('n1')
    library.save(lib)
    with pytest.raises(OperationConflict):
        observe(store, 'unrelated edit after regrant')


def test_updated_mention_context_reaches_model_with_original_request_identity(monkeypatch):
    from notron import conversation, notes
    body = '<div>Ideas</div><div>context before</div><div>#notron hello</div>'
    updated = body.replace('context before', 'context after')
    store = requests.current()
    first = store.observe('n1', body, [conversation.Question('#notron hello', 2)], source='mention', title='Ideas', folder='Notes')[0]
    second = store.observe('n1', updated, [conversation.Question('#notron hello', 2)], source='mention', title='Ideas', folder='Notes')[0]
    monkeypatch.setattr(notes, 'read_body', lambda nid: updated)
    monkeypatch.setattr(notes, 'list_all_notes', lambda: [notes.Note('n1', 'Ideas', 'Notes', 'today')])
    seen = []
    def node(state, **kw):
        from notron import nodes
        from notron.outbound import prepare_outbound
        seen.extend(prepare_outbound('write', nodes._prompt(state)))
        return state
    monkeypatch.setattr(graph, 'ORDER', ('writer',))
    monkeypatch.setitem(graph.NODES, 'writer', node)
    graph.run_request(second, brain=None, dry_run=True)
    assert first.request_id == second.request_id
    assert 'context after' in '\n'.join(seen)
    assert 'context before' not in '\n'.join(seen)


def test_morning_source_is_not_relabeled_cli(monkeypatch):
    monkeypatch.setattr(graph, 'ORDER', ())
    result = graph.run('plan my day', brain=None, trigger='morning', dry_run=True)
    assert result.envelope.source == 'morning'


def test_cli_file_records_request_before_filing(monkeypatch, capsys):
    from notron import cli, filer
    monkeypatch.setattr(cli, '_brain', lambda: None)
    seen = []
    def run(*a, **kw):
        record = requests.current().get('file-request')
        assert record.status == 'running'
        seen.append(record.envelope.source)
        return filer.Outcome()
    monkeypatch.setattr(filer, 'run', run)
    cli.main(['file', '--request-id', 'file-request'])
    cli.main(['file', '--request-id', 'file-request'])
    assert seen == ['cli']


def test_identical_resubmission_after_receipt_needs_no_empty_poll():
    from notron import conversation
    store = requests.current()
    first_body = '<div>Ask</div><div>remind me to call</div>'
    first = store.observe('n1', first_body, [conversation.Question('remind me to call', 1)], source='ask', title='Ask', folder='Notes')[0]
    store.claim(first.request_id)
    store.finish(first.request_id)
    next_body = first_body + '<div>Notron:</div><div>Reminder set</div><div>———</div><div>remind me to call</div>'
    questions = conversation.unanswered(next_body, ignore=('Ask',))
    assert len(questions) == 1
    second = store.observe('n1', next_body, questions, source='ask', title='Ask', folder='Notes')[0]
    assert first.request_id != second.request_id
    assert store.get(second.request_id).status == 'prepared'


@pytest.mark.parametrize('finish_review', [False, True])
def test_uncertain_request_keeps_identity_across_absence_and_restart(finish_review):
    store = requests.current()
    first = observe(store)[0]
    assert store.claim(first.request_id)
    if finish_review:
        store.finish(first.request_id, needs_review=True)
    observe(store, 'source temporarily empty', items=[])
    reopened = RequestStore(store.operations)
    restored = observe(reopened, 'source restored')[0]
    assert restored.request_id == first.request_id
    assert reopened.get(restored.request_id).status == 'needs_review'
    assert not reopened.claim(restored.request_id)


def test_receipt_for_first_identical_turn_preserves_second_pending_identity():
    from notron import conversation
    store = requests.current()
    first_body = '<div>Ask</div><div>remind me to call</div><div></div><div></div><div>remind me to call</div>'
    questions = conversation.unanswered(first_body, ignore=('Ask',))
    first, second = store.observe('n1', first_body, questions, source='ask', title='Ask', folder='Notes')
    assert store.claim(first.request_id)
    store.finish(first.request_id)
    after_receipt = '<div>Ask</div><div>remind me to call</div><div>Notron:</div><div>Reminder set</div><div>———</div><div></div><div></div><div>remind me to call</div>'
    remaining = store.observe('n1', after_receipt, conversation.unanswered(after_receipt, ignore=('Ask',)), source='ask', title='Ask', folder='Notes')
    assert len(remaining) == 1 and remaining[0].request_id == second.request_id
    assert store.get(second.request_id).status == 'prepared'
    assert store.claim(second.request_id)
    store.finish(second.request_id)
    assert all(store.get(envelope.request_id).status == 'completed' for envelope in (first, second))


@pytest.mark.parametrize('edit', ['prefix', 'suffix'])
def test_identical_pending_turns_keep_identity_across_unrelated_edits(edit):
    from notron import conversation
    store = requests.current()
    body = '<div>Ask</div><div>remind me to call</div><div></div><div></div><div>remind me to call</div>'
    first = store.observe('n1', body, conversation.unanswered(body, ignore=('Ask',)), source='ask', title='Ask', folder='Notes')
    if edit == 'prefix':
        updated = '<div>unrelated context</div><div></div><div></div>' + body
    else:
        updated = body + '<div></div><div></div><div>unrelated context</div>'
    questions = [q for q in conversation.unanswered(updated, ignore=('Ask',)) if q.text == 'remind me to call']
    second = store.observe('n1', updated, questions, source='ask', title='Ask', folder='Notes')
    assert [envelope.request_id for envelope in second] == [envelope.request_id for envelope in first]
    assert all(store.get(envelope.request_id).status == 'prepared' for envelope in second)


@pytest.mark.parametrize('position', ['before', 'between', 'after', 'removed'])
def test_distinct_request_insertion_or_removal_preserves_identical_pending_turns(position):
    from notron import conversation
    store = requests.current()
    call = '<div>remind me to call</div>'
    write = '<div>remind me to write</div>'
    gap = '<div></div><div></div>'
    body = '<div>Ask</div>' + call + gap + call
    if position == 'removed':
        body += gap + write
    first = store.observe('n1', body, conversation.unanswered(body, ignore=('Ask',)), source='ask', title='Ask', folder='Notes')
    expected = [envelope.request_id for envelope in first if envelope.text == 'remind me to call']
    turns = {'before': [write, call, call], 'between': [call, write, call],
             'after': [call, call, write], 'removed': [call, call]}[position]
    updated = '<div>Ask</div>' + gap.join(turns)
    second = store.observe('n1', updated, conversation.unanswered(updated, ignore=('Ask',)), source='ask', title='Ask', folder='Notes')
    retained = [envelope for envelope in second if envelope.text == 'remind me to call']
    assert [envelope.request_id for envelope in retained] == expected
    assert all(store.get(envelope.request_id).status == 'prepared' for envelope in retained)


def test_shifted_receipt_preserves_second_identical_pending_turn():
    from notron import conversation
    store = requests.current()
    original = '<div>Ask</div><div>remind me to call</div><div></div><div></div><div>remind me to call</div>'
    first, second = store.observe('n1', original, conversation.unanswered(original, ignore=('Ask',)), source='ask', title='Ask', folder='Notes')
    store.claim(first.request_id)
    store.finish(first.request_id)
    updated = '<div></div><div>Ask</div><div>remind me to call</div><div>Notron:</div><div>Reminder set</div><div>———</div><div></div><div></div><div>remind me to call</div>'
    remaining = store.observe('n1', updated, conversation.unanswered(updated, ignore=('Ask',)), source='ask', title='Ask', folder='Notes')
    assert len(remaining) == 1 and remaining[0].request_id == second.request_id
    assert store.claim(second.request_id)
    store.finish(second.request_id)
    assert store.get(first.request_id).status == store.get(second.request_id).status == 'completed'
