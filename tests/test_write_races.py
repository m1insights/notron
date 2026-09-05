"""P02 guarded writes against a synthetic Apple store; policy and storage stay real."""
from dataclasses import replace
from uuid import uuid4

import pytest

from notron import executor, library, markup, notes, rewrite, workspace
from notron.state import Write


class NoteStore:
    def __init__(self):
        self.rows = {}
        self.writes = []
        self.after_write = None

    def add(self, title, body, folder='Notes'):
        nid = uuid4().hex
        self.rows[nid] = [notes.Note(nid, title, folder, 'observed'), body]
        lib = library.load()
        lib.homes.add(nid)
        lib.decided.add(nid)
        library.save(lib)
        return nid

    def get_note(self, nid):
        return self.rows[nid][0] if nid in self.rows else None

    def find_note(self, folder, title):
        return next((row[0] for row in self.rows.values()
                     if row[0].folder == folder and row[0].title == title), None)

    def list_all_notes(self):
        return [row[0] for row in self.rows.values()]

    def list_notes(self, folder):
        return [n for n in self.list_all_notes() if n.folder == folder]

    def body(self, nid):
        return self.rows[nid][1]

    def set_body(self, nid, body):
        self.rows[nid][1] = body

    def write_body(self, nid, body):
        self.set_body(nid, body)
        self.writes.append((nid, body))
        if self.after_write:
            self.after_write(nid)

    def move(self, nid, title, folder):
        self.rows[nid][0] = replace(self.rows[nid][0], title=title, folder=folder)


@pytest.fixture
def fake_note_store(monkeypatch):
    store = NoteStore()
    monkeypatch.setattr(notes, 'get_note', store.get_note, raising=False)
    monkeypatch.setattr(notes, 'find_note', store.find_note)
    monkeypatch.setattr(notes, 'list_all_notes', store.list_all_notes)
    monkeypatch.setattr(notes, 'list_notes', store.list_notes)
    monkeypatch.setattr(notes, 'read_body', store.body)
    monkeypatch.setattr(notes, 'write_body', store.write_body)
    return store


@pytest.fixture
def safe_executor(fake_note_store):
    return executor.Executor(audit=False)


@pytest.fixture
def make_write(fake_note_store):
    def make(*, note_id, mode='replace', markdown='cleaned', **kwargs):
        note = fake_note_store.get_note(note_id)
        if mode == 'replace':
            rewrite.allow(note_id)
            kwargs.setdefault('rewrite_allowed', True)
        kwargs.setdefault('expected_revision', executor.revision(fake_note_store.body(note_id)))
        return Write(title=note.title, folder=note.folder, note_id=note_id,
                     mode=mode, markdown=markdown, operation_id=uuid4().hex, **kwargs)
    return make


def test_replace_rejects_newer_user_text(fake_note_store, safe_executor, make_write):
    nid = fake_note_store.add('Ideas', '<div>Ideas</div><div>original</div>')
    write = make_write(note_id=nid)
    live = '<div>Ideas</div><div>original plus new thought</div>'
    fake_note_store.set_body(nid, live)
    result = safe_executor.apply_write(write)
    assert not result.ok
    assert fake_note_store.body(nid) == live
    assert fake_note_store.writes == []


def test_exact_id_selects_second_of_duplicate_titles(fake_note_store, safe_executor, make_write):
    first = fake_note_store.add('Ideas', '<div>Ideas</div><div>first</div>')
    second = fake_note_store.add('Ideas', '<div>Ideas</div><div>second</div>', 'Work')
    write = make_write(note_id=second)
    assert safe_executor.apply_write(write).ok
    assert fake_note_store.body(first).endswith('<div>first</div>')
    assert 'cleaned' in fake_note_store.body(second)
    assert [nid for nid, _ in fake_note_store.writes] == [second]


def test_rename_and_move_keep_original_target(fake_note_store, safe_executor, make_write):
    nid = fake_note_store.add('Ideas', '<div>Ideas</div><div>original</div>')
    write = make_write(note_id=nid, mode='append', markdown='new entry')
    fake_note_store.move(nid, 'Renamed', 'Archive')
    twin = fake_note_store.add('Ideas', '<div>Ideas</div><div>unrelated twin</div>')
    result = safe_executor.apply_write(write)
    assert result.ok
    assert 'new entry' in fake_note_store.body(nid)
    assert 'new entry' not in fake_note_store.body(twin)


def test_deleted_target_does_not_redirect_to_title_twin(fake_note_store, safe_executor, make_write):
    nid = fake_note_store.add('Ideas', '<div>Ideas</div><div>old</div>')
    write = make_write(note_id=nid)
    del fake_note_store.rows[nid]
    twin = fake_note_store.add('Ideas', '<div>Ideas</div><div>new note</div>')
    assert not safe_executor.apply_write(write).ok
    assert fake_note_store.body(twin).endswith('<div>new note</div>')
    assert fake_note_store.writes == []


@pytest.mark.parametrize('rich', [
    '<object data="cid:attachment"></object>',
    '<img src="cid:photo">',
    '<input type="checkbox" checked>',
    '<ul class="Apple-dash-list"><li>native checklist</li></ul>',
    '<table><tr><td>rich table</td></tr></table>',
])
def test_unsupported_rich_content_never_replaced(rich, fake_note_store, safe_executor, make_write):
    before = '<div>Ideas</div><div>original</div>' + rich
    nid = fake_note_store.add('Ideas', before)
    write = make_write(note_id=nid)
    assert not safe_executor.apply_write(write).ok
    assert fake_note_store.body(nid) == before
    assert fake_note_store.writes == []


def test_unique_insert_anchor_can_rebase_after_unrelated_edit(fake_note_store, safe_executor, make_write):
    nid = fake_note_store.add('Ideas', '<div>Ideas</div><div>question</div>')
    write = make_write(note_id=nid, mode='insert', markdown='answer', anchor='question', after=1)
    live = '<div>Ideas</div><div>new thought</div><div>question</div>'
    fake_note_store.set_body(nid, live)
    assert safe_executor.apply_write(write).ok
    assert fake_note_store.body(nid).startswith(live)
    assert 'answer' in fake_note_store.body(nid)


@pytest.mark.parametrize('live', [
    '<div>Ideas</div><div>edited question</div>',
    '<div>Ideas</div><div>question</div><div>question</div>',
])
def test_missing_or_ambiguous_insert_anchor_does_not_guess(live, fake_note_store, safe_executor, make_write):
    nid = fake_note_store.add('Ideas', '<div>Ideas</div><div>question</div>')
    write = make_write(note_id=nid, mode='insert', markdown='answer', anchor='question', after=1)
    fake_note_store.set_body(nid, live)
    assert not safe_executor.apply_write(write).ok
    assert fake_note_store.body(nid) == live
    assert fake_note_store.writes == []


def test_policy_revocation_after_preparation_blocks_write(fake_note_store, safe_executor, make_write):
    nid = fake_note_store.add('Ideas', '<div>Ideas</div><div>original</div>')
    write = make_write(note_id=nid)
    lib = library.load()
    lib.ignore.add(nid)
    library.save(lib)
    assert not safe_executor.apply_write(write).ok
    assert fake_note_store.writes == []


def test_postwrite_divergence_is_not_reported_as_verified_success(fake_note_store, safe_executor, make_write):
    nid = fake_note_store.add('Ideas', '<div>Ideas</div><div>original</div>')
    write = make_write(note_id=nid)
    fake_note_store.after_write = lambda target: fake_note_store.set_body(target, '<div>remote edit</div>')
    result = safe_executor.apply_write(write)
    assert not result.ok
    assert fake_note_store.body(nid) == '<div>remote edit</div>'
    assert len(fake_note_store.writes) == 1


def test_required_backup_failure_prevents_destructive_write(monkeypatch, fake_note_store, safe_executor, make_write):
    nid = fake_note_store.add('Ideas', '<div>Ideas</div><div>original</div>')
    write = make_write(note_id=nid)
    def disk_full(*args):
        raise OSError('synthetic disk full')
    monkeypatch.setattr(executor.undo, 'save', disk_full)
    try:
        result = safe_executor.apply_write(write)
    except OSError:
        pass
    else:
        assert not result.ok
    assert fake_note_store.writes == []
    assert 'original' in fake_note_store.body(nid)


def test_organizer_captures_revision_before_model_work(fake_note_store):
    from notron import nodes
    from notron.state import State
    nid = fake_note_store.add('Ideas', '<div>Ideas</div><div>keep both original details</div>')
    rewrite.allow(nid)
    live = '<div>Ideas</div><div>keep both original details and this new thought</div>'
    class EditingBrain:
        def ask(self, **kwargs):
            fake_note_store.set_body(nid, live)
            return 'Both original details remain in this carefully tidied version of the note.'
    state = State(request='clean this up', intent='organize',
                  reply_to=('Ideas', 'Notes', 1), source_note_id=nid)
    state = nodes.organizer(state, brain=EditingBrain())
    state = nodes.executor(state)
    assert fake_note_store.body(nid) == live
    assert fake_note_store.writes == []
    assert any(line.startswith('✗') for line in state.results)


def test_planner_captures_destination_before_model_work(fake_note_store):
    from notron import nodes
    from notron.state import State
    nid = fake_note_store.add(workspace.TODAY, '<div>Today</div><div>original plan</div>', workspace.FOLDER)
    lib = library.load()
    lib.system_notes[workspace.TODAY] = nid
    library.save(lib)
    live = '<div>Today</div><div>original plan plus user edit</div>'
    class EditingBrain:
        def ask(self, **kwargs):
            fake_note_store.set_body(nid, live)
            return 'A new generated plan'
    state = nodes.planner(State(request='plan my day', intent='plan'), brain=EditingBrain())
    state = nodes.executor(state)
    assert fake_note_store.body(nid) == live
    assert fake_note_store.writes == []
    assert any(line.startswith('✗') for line in state.results)


def test_edit_during_backup_is_checked_before_final_write(monkeypatch, fake_note_store, safe_executor, make_write):
    nid = fake_note_store.add('Ideas', '<div>Ideas</div><div>original</div>')
    write = make_write(note_id=nid)
    original_save = executor.undo.save
    live = '<div>Ideas</div><div>edited during backup</div>'
    def save_then_edit(target, body):
        original_save(target, body)
        fake_note_store.set_body(target, live)
    monkeypatch.setattr(executor.undo, 'save', save_then_edit)
    assert not safe_executor.apply_write(write).ok
    assert fake_note_store.body(nid) == live
    assert fake_note_store.writes == []


def test_restore_rejects_revision_changed_since_proposal(fake_note_store, safe_executor, make_write):
    nid = fake_note_store.add('Ideas', '<div>Ideas</div><div>Notron version</div>')
    write = make_write(note_id=nid, mode='restore', markdown='<div>Ideas</div><div>earlier version</div>')
    live = '<div>Ideas</div><div>Notron version plus later words</div>'
    fake_note_store.set_body(nid, live)
    assert not safe_executor.apply_write(write).ok
    assert fake_note_store.body(nid) == live
    assert fake_note_store.writes == []


def test_same_applied_write_cannot_append_twice(fake_note_store, safe_executor, make_write):
    nid = fake_note_store.add('Ideas', '<div>Ideas</div><div>old</div>')
    write = make_write(note_id=nid, mode='append', markdown='one new entry')
    assert safe_executor.apply_write(write).ok
    safe_executor.apply_write(write)
    assert fake_note_store.body(nid).count('one new entry') == 1
    assert len(fake_note_store.writes) == 1


def test_explicit_write_without_expected_revision_is_refused(fake_note_store, safe_executor, make_write):
    nid = fake_note_store.add('Ideas', '<div>Ideas</div><div>old</div>')
    write = make_write(note_id=nid, expected_revision=None)
    assert not safe_executor.apply_write(write).ok
    assert fake_note_store.writes == []


def test_append_rebases_on_current_text_and_records_observed_revision(fake_note_store, safe_executor, make_write):
    from notron import operations
    nid = fake_note_store.add('Ideas', '<div>old</div>')
    write = make_write(note_id=nid, mode='append', markdown='new entry', anchor='old')
    live = '<div>old</div><div>user edit</div>'
    fake_note_store.set_body(nid, live)
    assert safe_executor.apply_write(write).ok
    assert fake_note_store.body(nid).startswith(live)
    op = operations.current().get(write.operation_id)
    assert op.status == operations.S.APPLIED
    assert op.expected_revision == write.expected_revision
    assert op.observed_revision == executor.revision(fake_note_store.body(nid))


def test_divergence_and_uncertain_timeout_never_reapply(monkeypatch, fake_note_store, safe_executor, make_write):
    from notron import operations
    nid = fake_note_store.add('Ideas', '<div>old</div>')
    write = make_write(note_id=nid, mode='append', markdown='entry')
    original = fake_note_store.write_body
    def applied_then_timeout(target, body):
        original(target, body)
        raise TimeoutError('synthetic uncertain timeout')
    monkeypatch.setattr(notes, 'write_body', applied_then_timeout)
    assert not safe_executor.apply_write(write).ok
    assert operations.current().get(write.operation_id).status == operations.S.NEEDS_REVIEW
    safe_executor.apply_write(write)
    assert len(fake_note_store.writes) == 1


def test_policy_revoked_during_backup_refuses_write(monkeypatch, fake_note_store, safe_executor, make_write):
    nid = fake_note_store.add('Ideas', '<div>old</div>')
    write = make_write(note_id=nid)
    original = executor.undo.save
    def save_then_revoke(target, body):
        original(target, body)
        lib = library.load()
        lib.ignore.add(target)
        library.save(lib)
    monkeypatch.setattr(executor.undo, 'save', save_then_revoke)
    assert not safe_executor.apply_write(write).ok
    assert fake_note_store.writes == []


def test_final_read_write_race_is_explicitly_nonatomic(monkeypatch, fake_note_store, safe_executor, make_write):
    nid = fake_note_store.add('Ideas', '<div>old</div>')
    write = make_write(note_id=nid)
    original = fake_note_store.write_body
    def remote_writes_in_gap(target, body):
        fake_note_store.set_body(target, '<div>remote text in final gap</div>')
        original(target, body)
    monkeypatch.setattr(notes, 'write_body', remote_writes_in_gap)
    result = safe_executor.apply_write(write)
    # Apple has no CAS: a remote writer in this final gap cannot be detected.
    assert result.ok
    assert 'remote text in final gap' not in fake_note_store.body(nid)
    assert 'cleaned' in fake_note_store.body(nid)


def test_dry_run_has_no_operation_or_undo_side_effects(fake_note_store, make_write):
    from notron import operations, undo
    nid = fake_note_store.add('Ideas', '<div>old</div>')
    write = make_write(note_id=nid)
    assert executor.Executor(dry_run=True).apply_write(write).ok
    assert not operations.PATH.exists()
    assert undo._load() == {}
    assert fake_note_store.writes == []


@pytest.mark.parametrize('rich', [
    '<div style="unknown:object">text</div>', '<a href="https://example.com">link</a>',
    '<div><b>unclosed</div>', '<custom>unknown</custom>', '<div data-attachment="id">x</div>',
])
def test_unsupported_or_malformed_html_offers_separate_text(rich, fake_note_store, safe_executor, make_write):
    nid = fake_note_store.add('Ideas', '<div>Ideas</div>' + rich)
    result = safe_executor.apply_write(make_write(note_id=nid))
    assert not result.ok
    assert result.alternative_text == 'cleaned'
    assert fake_note_store.writes == []


def test_supported_formatting_can_be_replaced(fake_note_store, safe_executor, make_write):
    nid = fake_note_store.add('Ideas', '<div><b>Ideas</b></div><ul><li>one <i>detail</i></li></ul>')
    assert safe_executor.apply_write(make_write(note_id=nid)).ok


def test_two_local_executors_serialize_read_modify_write(monkeypatch, fake_note_store, make_write):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    nid = fake_note_store.add('Ideas', '<div>old</div>')
    first = make_write(note_id=nid, mode='append', markdown='first', anchor='old')
    second = make_write(note_id=nid, mode='append', markdown='second', anchor='old')
    in_backup, release = threading.Event(), threading.Event()
    original = executor.undo.save
    calls = []
    def hold_first(target, body):
        calls.append(body)
        if len(calls) == 1:
            in_backup.set()
            assert release.wait(3)
        original(target, body)
    monkeypatch.setattr(executor.undo, 'save', hold_first)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(executor.Executor(audit=False).apply_write, first)
        assert in_backup.wait(3)
        b = pool.submit(executor.Executor(audit=False).apply_write, second)
        release.set()
        assert a.result(timeout=5).ok and b.result(timeout=5).ok
    assert 'first' in fake_note_store.body(nid) and 'second' in fake_note_store.body(nid)
    assert len(fake_note_store.writes) == 2


def test_rich_organizer_returns_separate_result_without_any_source_write(fake_note_store):
    from notron import nodes
    from notron.state import State
    body = '<div>Ideas</div><div>original details</div><object data="cid:attachment"></object>'
    nid = fake_note_store.add('Ideas', body)
    rewrite.allow(nid)
    ask = fake_note_store.add(workspace.ASK, '<div>Ask</div>', workspace.FOLDER)
    lib = library.load(); lib.system_notes[workspace.ASK] = ask; library.save(lib)
    class Brain:
        def ask(self, **kwargs):
            return 'Original details carefully organized in a separate plain text result.'
    state = nodes.organizer(State(request='clean this up', intent='organize',
                            reply_to=('Ideas', 'Notes', 1), source_note_id=nid), brain=Brain())
    nodes.executor(state)
    assert fake_note_store.body(nid) == body
    assert [target for target, value in fake_note_store.writes] == [ask]
    assert 'separate' in state.answer and 'Original details' in state.answer


def test_care_preserves_user_edit_during_composition(monkeypatch, fake_note_store):
    from notron import care
    nid = fake_note_store.add(workspace.CARE, '<div>Care</div><div>old</div>', workspace.FOLDER)
    lib = library.load(); lib.system_notes[workspace.CARE] = nid; library.save(lib)
    monkeypatch.setattr(care, 'check', lambda: [care.Signal('test', 'ok', 'Fine', '')])
    live = '<div>Care</div><div>user edit during composition</div>'
    class Brain:
        def ask(self, **kwargs):
            fake_note_store.set_body(nid, live)
            return 'Everything is fine.'
    _, _, result = care.run(Brain())
    assert not result.ok
    assert fake_note_store.body(nid) == live


def test_reflection_preserves_lesson_edit_during_verification(monkeypatch, fake_note_store):
    from notron import reflect
    ask = fake_note_store.add(workspace.ASK, '<div>Ask</div>', workspace.FOLDER)
    nid = fake_note_store.add(workspace.LESSONS, '<div>Lessons</div><div>- old lesson</div>', workspace.FOLDER)
    lib = library.load(); lib.system_notes[workspace.LESSONS] = nid; lib.system_notes[workspace.ASK] = ask; library.save(lib)
    monkeypatch.setattr(reflect, 'misses', lambda history: [reflect.Miss(reflect.Exchange('Question', 'Answer'), 'No short answers', 'corrected')])
    live = '<div>Lessons</div><div>- user added lesson</div>'
    class Brain:
        def ask_json(self, **kwargs):
            if kwargs['tier'] == 'smart':
                return {'lessons': [{'rule': 'Use more detail.', 'evidence': 'No short answers'}]}
            fake_note_store.set_body(nid, live)
            return {'keep': [0]}
    result = reflect.run(Brain())
    assert not result['written']
    assert fake_note_store.body(nid) == live


@pytest.mark.parametrize('anchor,live', [('', '<div>old</div><div>new</div>'),
                                       ('old', '<div>old</div><div>old</div>')])
def test_stale_append_requires_unambiguous_captured_anchor(anchor, live, fake_note_store, safe_executor, make_write):
    nid = fake_note_store.add('Ideas', '<div>old</div>')
    write = make_write(note_id=nid, mode='append', anchor=anchor)
    fake_note_store.set_body(nid, live)
    assert not safe_executor.apply_write(write).ok
    assert fake_note_store.body(nid) == live


def test_refused_organizer_does_not_claim_cleanup_succeeded(fake_note_store):
    from notron import nodes
    from notron.state import State
    nid = fake_note_store.add('Ideas', '<div>original details</div>')
    rewrite.allow(nid)
    class Brain:
        def ask(self, **kwargs):
            fake_note_store.set_body(nid, '<div>user edit</div>')
            return 'Original details carefully cleaned and tidied.'
    state = nodes.organizer(State(request='clean this up', intent='organize',
                            reply_to=('Ideas', 'Notes', 0), source_note_id=nid), brain=Brain())
    nodes.executor(state)
    assert state.answer != nodes.CLEANED
    assert 'changed' in state.answer


def test_policy_change_on_final_body_read_prevents_write(monkeypatch, fake_note_store, safe_executor, make_write):
    nid = fake_note_store.add('Ideas', '<div>original</div>')
    write = make_write(note_id=nid)
    reads = 0
    def read_then_revoke(target):
        nonlocal reads
        reads += 1
        if reads == 2:
            lib = library.load(); lib.ignore.add(target); library.save(lib)
        return fake_note_store.body(target)
    monkeypatch.setattr(notes, 'read_body', read_then_revoke)
    result = safe_executor.apply_write(write)
    assert not result.ok
    assert fake_note_store.writes == []


def test_rename_during_backup_never_resurrects_old_title(monkeypatch, fake_note_store, safe_executor, make_write):
    nid = fake_note_store.add('Ideas', '<div>original</div>')
    write = make_write(note_id=nid)
    original = executor.undo.save
    def save_then_rename(target, body):
        original(target, body)
        fake_note_store.move(target, 'Renamed', 'Archive')
    monkeypatch.setattr(executor.undo, 'save', save_then_rename)
    assert not safe_executor.apply_write(write).ok
    assert fake_note_store.writes == []


def test_empty_rich_organizer_answer_never_modifies_original(fake_note_store):
    from notron import nodes
    from notron.state import State
    body = '<div>Ideas</div><div>clean this up</div><img src="cid:photo">'
    nid = fake_note_store.add('Ideas', body)
    class Brain:
        def ask(self, **kwargs): return ''
    state = nodes.organizer(State(request='clean this up', intent='organize',
                            reply_to=('Ideas', 'Notes', 1), source_note_id=nid), brain=Brain())
    nodes.executor(state)
    assert fake_note_store.writes == [] and fake_note_store.body(nid) == body


def test_audit_failure_does_not_erase_verified_primary_success(fake_note_store, make_write):
    nid = fake_note_store.add('Ideas', '<div>original</div>')
    log = fake_note_store.add(workspace.LOG, '<div>Log</div>', workspace.FOLDER)
    lib = library.load(); lib.system_notes[workspace.LOG] = log; library.save(lib)
    def diverge_log(target):
        if target == log:
            fake_note_store.set_body(log, '<div>remote log edit</div>')
    fake_note_store.after_write = diverge_log
    result = executor.Executor().apply_write(make_write(note_id=nid))
    assert result.ok and 'cleaned' in fake_note_store.body(nid)
    assert fake_note_store.body(log) == '<div>remote log edit</div>'


def test_blocked_write_keeps_generic_audit_without_mutating_target(fake_note_store, make_write):
    nid = fake_note_store.add('Ideas', '<div>original</div>')
    log = fake_note_store.add(workspace.LOG, '<div>Log</div>', workspace.FOLDER)
    lib = library.load(); lib.system_notes[workspace.LOG] = log; library.save(lib)
    write = make_write(note_id=nid)
    fake_note_store.set_body(nid, '<div>user edit</div>')
    assert not executor.Executor().apply_write(write).ok
    assert fake_note_store.body(nid) == '<div>user edit</div>'
    assert 'BLOCKED' in fake_note_store.body(log)


@pytest.mark.parametrize('answer', ['Carefully organized original details in a separate result.', '', 'x'])
def test_rich_organizer_delivers_visible_result_through_registered_ask(fake_note_store, answer):
    from notron import graph, requests
    body = '<div>Ideas</div><div>original details</div><div>@notron clean this up</div><img src="cid:photo">'
    nid = fake_note_store.add('Ideas', body)
    ask = fake_note_store.add(workspace.ASK, '<div>Ask</div>', workspace.FOLDER)
    lib = library.load(); lib.system_notes[workspace.ASK] = ask; library.save(lib)
    class Brain:
        def ask(self, **kwargs): return answer
    envelope = requests.create('clean this up', source='mention', note_id=nid,
                               source_revision=executor.revision(body),
                               source_text='@notron clean this up', reply_to=('Ideas', 'Notes', 2))
    state = graph.run_request(envelope, brain=Brain())
    assert fake_note_store.body(nid) == body
    assert [target for target, value in fake_note_store.writes] == [ask]
    visible = markup.to_text(fake_note_store.body(ask))
    assert ('separate' in visible if len(answer) > 1 else 'couldn\'t tidy' in visible)
    if len(answer) > 1:
        assert answer in visible
    assert state.results and all(result.startswith('✓') for result in state.results)
    assert requests.current().get(envelope.request_id).status == 'completed'


@pytest.mark.parametrize('ask_state', ['missing', 'denied', 'rich', 'changed'])
def test_rich_result_undeliverable_keeps_request_in_review(fake_note_store, ask_state):
    from notron import graph, requests
    body = '<div>Ideas</div><div>original details</div><div>@notron clean this up</div><img src="cid:photo">'
    nid = fake_note_store.add('Ideas', body)
    ask = None
    if ask_state != 'missing':
        ask_body = '<div>Ask</div>' + ('<img src="cid:another-photo">' if ask_state == 'rich' else '')
        ask = fake_note_store.add(workspace.ASK, ask_body, workspace.FOLDER)
        lib = library.load(); lib.system_notes[workspace.ASK] = ask
        if ask_state == 'denied': lib.ignore.add(ask)
        library.save(lib)
    class Brain:
        def ask(self, **kwargs):
            if ask_state == 'changed':
                fake_note_store.set_body(ask, '<div>Ask</div><div>new user words</div>')
            return 'Carefully organized original details in a separate plain text result.'
    envelope = requests.create('clean this up', source='mention', note_id=nid,
                               source_revision=executor.revision(body),
                               source_text='@notron clean this up', reply_to=('Ideas', 'Notes', 2))
    state = graph.run_request(envelope, brain=Brain())
    assert fake_note_store.writes == [] and fake_note_store.body(nid) == body
    assert any(result.startswith('✗') for result in state.results)
    assert requests.current().get(envelope.request_id).status == 'needs_review'
    assert 'could not verify' in state.answer.lower()


def test_rich_confirmation_refusal_is_visible_without_model_or_source_write(fake_note_store):
    from notron import graph, requests
    body = '<div>Ideas</div><div>@notron yes</div><img src="cid:photo">'
    nid = fake_note_store.add('Ideas', body)
    ask = fake_note_store.add(workspace.ASK, '<div>Ask</div>', workspace.FOLDER)
    lib = library.load(); lib.system_notes[workspace.ASK] = ask; library.save(lib)
    class Brain:
        def ask(self, **kwargs): pytest.fail('confirmation must not infer')
        def ask_json(self, **kwargs): pytest.fail('confirmation must not infer')
    envelope = requests.create('yes', source='mention', note_id=nid,
                               source_revision=executor.revision(body),
                               source_text='@notron yes', reply_to=('Ideas', 'Notes', 1))
    state = graph.run_request(envelope, brain=Brain())
    assert [target for target, value in fake_note_store.writes] == [ask]
    assert fake_note_store.body(nid) == body and not rewrite.allowed(nid)
    assert 'separate plain-text result' in markup.to_text(fake_note_store.body(ask))
    assert state.results and requests.current().get(envelope.request_id).status == 'completed'


@pytest.mark.parametrize("revoke_at", [1, 2])
def test_source_revoked_during_final_target_read_is_never_copied(monkeypatch, fake_note_store, safe_executor, make_write, revoke_at):
    from notron import operations
    source = fake_note_store.add('First source', '<div>first source paragraph</div>')
    target = fake_note_store.add('Destination', '<div>destination paragraph</div>')
    write = make_write(note_id=target, mode='append', markdown='first source paragraph',
                       source_checks=[(source, executor.revision(fake_note_store.body(source)), 'first source paragraph', 0)])
    reads = 0
    def read_then_revoke(nid):
        nonlocal reads
        if nid == target:
            reads += 1
            if reads == revoke_at:
                lib = library.load(); lib.ignore.add(source); library.save(lib)
        return fake_note_store.body(nid)
    monkeypatch.setattr(notes, 'read_body', read_then_revoke)
    assert not safe_executor.apply_write(write).ok
    assert fake_note_store.writes == []
    operation = operations.current().get(write.operation_id)
    if revoke_at == 1:
        assert operation is None, "revoked content must not be encrypted after the admission read"
    else:
        assert operation.status == operations.S.NEEDS_REVIEW
        assert operations.current().payload(write.operation_id) is None


def test_rich_request_source_revoked_during_inference_is_not_delivered(fake_note_store):
    from notron import graph, requests
    body = '<div>Ideas</div><div>original details</div><div>@notron clean this up</div><img src="cid:photo">'
    source = fake_note_store.add('Ideas', body)
    ask = fake_note_store.add(workspace.ASK, '<div>Ask</div>', workspace.FOLDER)
    lib = library.load(); lib.system_notes[workspace.ASK] = ask; library.save(lib)
    class Brain:
        def ask(self, **kwargs):
            lib = library.load(); lib.ignore.add(source); library.save(lib)
            return 'Original details carefully organized in a separate plain text result.'
    envelope = requests.create('clean this up', source='mention', note_id=source,
                               source_revision=executor.revision(body), source_text='@notron clean this up',
                               reply_to=('Ideas', 'Notes', 2))
    state = graph.run_request(envelope, brain=Brain())
    assert fake_note_store.writes == []
    assert any(result.startswith('✗') for result in state.results)
    assert requests.current().get(envelope.request_id).status == 'needs_review'


@pytest.mark.parametrize('removed', ['secondary', 'destination'])
def test_complete_operation_provenance_purges_any_deleted_contributor(removed, fake_note_store, safe_executor, make_write):
    from notron import operations, retention
    first = fake_note_store.add('First source', '<div>first source paragraph</div>')
    second = fake_note_store.add('Second source', '<div>second source paragraph</div>')
    target = fake_note_store.add('Destination', '<div>destination anchor</div>')
    write = make_write(note_id=target, mode='append', anchor='destination anchor',
                       markdown='first source paragraph\n\nsecond source paragraph',
                       source_checks=[(nid, executor.revision(fake_note_store.body(nid)), text, 0)
                                      for nid, text in [(first, 'first source paragraph'), (second, 'second source paragraph')]])
    assert safe_executor.apply_write(write).ok
    ledger = operations.current()
    ref = ledger.get(write.operation_id).payload_ref
    assert ledger.payload(write.operation_id)
    del fake_note_store.rows[second if removed == 'secondary' else target]
    retention.reconcile()
    assert ledger.payload(write.operation_id) is None
    assert not (ledger.payload_store.root / (ref + '.enc')).exists()
    assert ledger.get(write.operation_id).status == operations.S.NEEDS_REVIEW


def test_invalidated_applying_operation_is_checked_after_final_reads(monkeypatch, fake_note_store, safe_executor, make_write):
    from notron import operations
    target = fake_note_store.add('Destination', '<div>destination paragraph</div>')
    write = make_write(note_id=target, mode='append', markdown='new paragraph')
    reads = 0
    def invalidate_on_final_read(nid):
        nonlocal reads
        reads += 1
        if reads == 2:
            operations.current().transition(write.operation_id, operations.S.APPLYING,
                                            operations.S.NEEDS_REVIEW, failure_code='policy_changed')
        return fake_note_store.body(nid)
    monkeypatch.setattr(notes, 'read_body', invalidate_on_final_read)
    assert not safe_executor.apply_write(write).ok
    assert fake_note_store.writes == []


def test_writer_context_provenance_is_retained_and_revocable(fake_note_store):
    from notron import nodes, operations, retention
    from notron.outbound import Passage
    from notron.state import State
    context = fake_note_store.add('Reference', '<div>reference paragraph</div>')
    ask = fake_note_store.add(workspace.ASK, '<div>Ask</div>', workspace.FOLDER)
    lib = library.load(); lib.system_notes[workspace.ASK] = ask; library.save(lib)
    class Brain:
        def ask(self, **kwargs): return 'A derived answer from the reference paragraph.'
    state = nodes.writer(State(request='Explain', intent='question',
                         context=[Passage.from_note('reference paragraph', fake_note_store.get_note(context))]), brain=Brain())
    nodes.executor(state)
    assert state.results[0].startswith('✓')
    operation_id = state.writes[0].operation_id
    del fake_note_store.rows[context]
    retention.reconcile()
    assert operations.current().payload(operation_id) is None


def test_success_audit_payload_tracks_note_derived_title(fake_note_store, make_write):
    from notron import operations, retention
    target = fake_note_store.add('Source title', '<div>original</div>')
    log = fake_note_store.add(workspace.LOG, '<div>Log</div>', workspace.FOLDER)
    lib = library.load(); lib.system_notes[workspace.LOG] = log; library.save(lib)
    assert executor.Executor().apply_write(make_write(note_id=target)).ok
    ledger = operations.current()
    audit = next(op for op in ledger.pending() if op.target_id == log)
    assert b'Source title' in ledger.payload(audit.operation_id)
    del fake_note_store.rows[target]
    retention.reconcile()
    assert ledger.payload(audit.operation_id) is None
