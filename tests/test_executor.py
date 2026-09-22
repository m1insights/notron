"""Executor integration against a mutable synthetic Notes body and real policy."""
from notron import executor as ex_mod, library, workspace, rewrite
from notron.notes import Note


def in_note(monkeypatch, title=workspace.ASK, folder=workspace.FOLDER, body=None):
    note = Note('n1', title, folder, 'observed')
    live = {'body': body or f'<div>{title}</div><div>original question</div>', 'writes': []}
    lib = library.load()
    if folder == workspace.FOLDER:
        lib.system_notes[title] = note.id
    else:
        lib.homes.add(note.id)
    library.save(lib)
    monkeypatch.setattr(ex_mod.notes, 'get_note', lambda nid: note if nid == note.id else None)
    monkeypatch.setattr(ex_mod.notes, 'list_notes', lambda f: [note] if f == folder else [])
    monkeypatch.setattr(ex_mod.notes, 'find_note', lambda f, t: note if (f, t) == (folder, title) else None)
    monkeypatch.setattr(ex_mod.notes, 'read_body', lambda nid: live['body'])
    def write(nid, body):
        live['body'] = body
        live['writes'].append(body)
    monkeypatch.setattr(ex_mod.notes, 'write_body', write)
    return live


def test_a_write_is_skipped_if_the_note_changed_since_it_was_read(monkeypatch):
    live = in_note(monkeypatch)
    target = ex_mod.capture_write(workspace.ASK, mode='insert', anchor='original question', after=1)
    target.markdown = 'answer'
    live['body'] = '<div>Ask</div><div>question changed</div>'
    result = ex_mod.Executor(audit=False).apply_write(target)
    assert not result.ok and 'changed' in result.reason
    assert live['writes'] == []


def test_a_write_proceeds_when_the_note_is_unchanged(monkeypatch):
    live = in_note(monkeypatch)
    result = ex_mod.Executor(audit=False).insert(workspace.ASK, 'answer', after=1, anchor='original question')
    assert result.ok and 'answer' in live['body']


def test_replace_reads_again_before_commit_and_verifies_afterward(monkeypatch):
    live = in_note(monkeypatch, workspace.TODAY)
    reads = []
    monkeypatch.setattr(ex_mod.notes, 'read_body', lambda nid: reads.append(live['body']) or live['body'])
    result = ex_mod.Executor(audit=False).replace(workspace.TODAY, 'new plan')
    assert result.ok
    assert len(reads) >= 3 and reads[-1] == live['body']
    assert reads[-2] != reads[-1]


def test_a_write_to_an_existing_note_saves_its_old_body(monkeypatch):
    live = in_note(monkeypatch, 'Some Note', 'Notes')
    before = live['body']
    result = ex_mod.Executor(audit=False).append('Some Note', 'more text', folder='Notes')
    assert result.ok
    assert ex_mod.undo.peek('n1').before_html == before
    assert ex_mod.undo.peek('n1').after_revision == ex_mod.revision(live['body'])


def test_a_refused_write_saves_no_undo(monkeypatch):
    live = in_note(monkeypatch, 'Recipes', 'Notes')
    assert not ex_mod.Executor(audit=False).replace('Recipes', 'rewrite', folder='Notes').ok
    assert ex_mod.undo._load() == {} and live['writes'] == []


def test_restore_writes_the_body_back_verbatim(monkeypatch):
    live = in_note(monkeypatch, workspace.TODAY)
    original = '<div>Today</div><div>*original* — her words, not markdown</div>'
    ex_mod.undo.save('n1', original, ex_mod.revision(live['body']), 'previous')
    ex_mod.undo.promote('n1', 'previous')
    result = ex_mod.Executor(audit=False).restore(workspace.TODAY, original)
    assert result.ok and live['writes'] == [original]


def test_replace_outside_her_folder_needs_the_opt_in(monkeypatch):
    live = in_note(monkeypatch, 'Recipes', 'Notes')
    ex = ex_mod.Executor(audit=False)
    assert not ex.replace('Recipes', 'rewrite', folder='Notes').ok
    rewrite.allow('n1')
    assert ex.replace('Recipes', 'rewrite', folder='Notes', rewrite_allowed=True).ok
    assert len(live['writes']) == 1


def test_a_restore_saves_no_undo_slot(monkeypatch):
    live = in_note(monkeypatch, workspace.TODAY)
    ex_mod.undo.save('n1', '<div>original</div>', ex_mod.revision(live['body']), 'previous')
    ex_mod.undo.promote('n1', 'previous')
    assert ex_mod.Executor(audit=False).restore(workspace.TODAY, '<div>original</div>').ok
    assert ex_mod.undo._load() == {}


def test_restoring_a_note_that_no_longer_exists_is_refused(monkeypatch):
    monkeypatch.setattr(ex_mod.notes, 'list_notes', lambda folder: [])
    monkeypatch.setattr(ex_mod.notes, 'get_note', lambda nid: None)
    monkeypatch.setattr(ex_mod.notes, 'create_note', lambda *args: __import__('pytest').fail('must not create'))
    assert not ex_mod.Executor(audit=False).restore('Gone Note', '<div>original</div>').ok


def test_a_deleted_log_requires_setup_to_register_a_replacement(monkeypatch):
    monkeypatch.setattr(ex_mod.notes, 'get_note', lambda nid: None)
    created = []
    monkeypatch.setattr(ex_mod.notes, 'create_note', lambda *args: created.append(args))
    ex_mod.Executor()._log('synthetic operation')
    assert created == []


def test_a_folder_she_cannot_see_never_multiplies_the_log_note(monkeypatch):
    monkeypatch.setattr(ex_mod.notes, 'get_note', lambda nid: None)
    created = []
    monkeypatch.setattr(ex_mod.notes, 'create_note', lambda *args: created.append(args))
    for _ in range(14):
        ex_mod.Executor()._log('synthetic operation')
    assert created == []


def test_restore_without_snapshot_proof_is_refused(monkeypatch):
    live = in_note(monkeypatch, workspace.TODAY)
    assert not ex_mod.Executor(audit=False).restore(workspace.TODAY, '<div>arbitrary</div>').ok
    assert live['writes'] == []


def _saved_write(monkeypatch):
    live = in_note(monkeypatch, workspace.TODAY)
    before = live['body']
    ex = ex_mod.Executor(audit=False)
    assert ex.replace(workspace.TODAY, 'new plan').ok
    return ex, live, before, ex_mod.undo.peek('n1')


def test_failed_restore_keeps_snapshot(monkeypatch):
    ex, live, before, snapshot = _saved_write(monkeypatch)
    def fail(*args):
        raise OSError('synthetic restore failure')
    monkeypatch.setattr(ex_mod.notes, 'write_body', fail)
    assert not ex.restore(workspace.TODAY, before).ok
    assert ex_mod.undo.peek('n1') == snapshot


def test_successful_restore_consumes_only_after_verification_and_second_undo_refuses(monkeypatch):
    ex, live, before, snapshot = _saved_write(monkeypatch)
    assert ex.restore(workspace.TODAY, before).ok
    assert live['body'] == before
    assert ex_mod.undo.peek('n1') is None
    assert not ex.restore(workspace.TODAY, before).ok
    assert len(live['writes']) == 2


def test_intervening_edit_even_undo_command_never_overwritten(monkeypatch):
    ex, live, before, snapshot = _saved_write(monkeypatch)
    live['body'] += '<div>@notron undo</div>'
    edited = live['body']
    assert not ex.restore(workspace.TODAY, before).ok
    assert live['body'] == edited
    assert ex_mod.undo.peek('n1') == snapshot


def test_dry_run_keeps_snapshot(monkeypatch):
    ex, live, before, snapshot = _saved_write(monkeypatch)
    assert ex_mod.Executor(audit=False, dry_run=True).restore(workspace.TODAY, before).ok
    assert ex_mod.undo.peek('n1') == snapshot
    assert len(live['writes']) == 1


def test_final_refusal_preserves_prior_snapshot(monkeypatch):
    ex, live, before, snapshot = _saved_write(monkeypatch)
    save = ex_mod.undo.save
    def edit(*args):
        saved = save(*args)
        live['body'] += '<div>new user words</div>'
        return saved
    monkeypatch.setattr(ex_mod.undo, 'save', edit)
    assert not ex.replace(workspace.TODAY, 'another plan').ok
    assert ex_mod.undo.peek('n1') == snapshot
    assert len(live['writes']) == 1


def test_restore_crash_after_effect_reconciles_without_second_write(monkeypatch):
    from notron import recovery
    from dataclasses import replace
    ex, live, before, snapshot = _saved_write(monkeypatch)
    write = replace(ex_mod.capture_write(workspace.TODAY, mode='restore'),
                    markdown=before, snapshot_id=snapshot.snapshot_id)
    class Crash(BaseException):
        pass
    def crash(name, oid):
        if name == 'after_external_save':
            raise Crash()
    monkeypatch.setattr(recovery, 'boundary', crash)
    import pytest
    with pytest.raises(Crash):
        ex.apply_write(write)
    assert ex_mod.undo.peek('n1') == snapshot
    monkeypatch.setattr(recovery, 'boundary', lambda *args: None)
    assert ex.apply_write(write).ok
    assert live['body'] == before
    assert len(live['writes']) == 2
    assert ex_mod.undo.peek('n1') is None


def test_evidence_save_failure_before_mutation_preserves_previous_snapshot(monkeypatch):
    from notron import recovery
    ex, live, before, snapshot = _saved_write(monkeypatch)
    def disk_full(*args):
        raise OSError('synthetic evidence write failure')
    monkeypatch.setattr(recovery, 'put', disk_full)
    assert not ex.replace(workspace.TODAY, 'another plan').ok
    assert ex_mod.undo.peek('n1') == snapshot
    assert len(live['writes']) == 1


def test_undo_receipt_flag_cannot_skip_destructive_backup(monkeypatch):
    from dataclasses import replace
    live = in_note(monkeypatch, workspace.TODAY)
    write = replace(ex_mod.capture_write(workspace.TODAY, mode='replace'),
                    markdown='destructive', undo_reply=True)
    assert not ex_mod.Executor(audit=False).apply_write(write).ok
    assert live['writes'] == []


def test_restore_body_and_snapshot_cannot_be_substituted(monkeypatch):
    from dataclasses import replace
    ex, live, before, snapshot = _saved_write(monkeypatch)
    write = replace(ex_mod.capture_write(workspace.TODAY, mode='restore'),
                    markdown=before, snapshot_id=snapshot.snapshot_id)
    assert not ex.apply_write(replace(write, markdown='<div>forged</div>')).ok
    assert not ex.apply_write(replace(write, operation_id='wrong-proof', snapshot_id='wrong')).ok
    assert ex_mod.undo.peek('n1') == snapshot
    assert len(live['writes']) == 1


def test_post_restore_divergence_keeps_snapshot(monkeypatch):
    ex, live, before, snapshot = _saved_write(monkeypatch)
    def divergent(nid, body):
        live['body'] = body + '<div>new remote words</div>'
        live['writes'].append(body)
    monkeypatch.setattr(ex_mod.notes, 'write_body', divergent)
    assert not ex.restore(workspace.TODAY, before).ok
    assert ex_mod.undo.peek('n1') == snapshot
    assert 'new remote words' in live['body']


def test_snapshot_storage_failure_after_verified_restore_can_finish_without_replay(monkeypatch):
    from dataclasses import replace
    ex, live, before, snapshot = _saved_write(monkeypatch)
    write = replace(ex_mod.capture_write(workspace.TODAY, mode='restore'),
                    markdown=before, snapshot_id=snapshot.snapshot_id)
    consume = ex_mod.undo.consume
    def disk_full(*args):
        raise OSError('synthetic snapshot consume failure')
    monkeypatch.setattr(ex_mod.undo, 'consume', disk_full)
    assert not ex.apply_write(write).ok
    assert ex_mod.undo.peek('n1') == snapshot
    monkeypatch.setattr(ex_mod.undo, 'consume', consume)
    assert ex.apply_write(write).ok
    assert ex_mod.undo.peek('n1') is None
    assert len(live['writes']) == 2


def test_backup_save_then_failure_keeps_previous_snapshot(monkeypatch):
    ex, live, before, snapshot = _saved_write(monkeypatch)
    save = ex_mod.undo.save
    def saved_then_failed(*args):
        save(*args)
        raise OSError('synthetic completion failure')
    monkeypatch.setattr(ex_mod.undo, 'save', saved_then_failed)
    assert not ex.replace(workspace.TODAY, 'another plan').ok
    assert ex_mod.undo.peek('n1') == snapshot
    assert len(live['writes']) == 1


def test_snapshot_replaced_during_restore_preparation_refuses_old_proof(monkeypatch):
    from notron import recovery
    ex, live, before, snapshot = _saved_write(monkeypatch)
    def replace_snapshot(name, oid):
        if name == 'before_applying':
            ex_mod.undo.save('n1', '<div>newer backup</div>')
    monkeypatch.setattr(recovery, 'boundary', replace_snapshot)
    assert not ex.restore(workspace.TODAY, before).ok
    assert len(live['writes']) == 1
    assert ex_mod.undo.peek('n1').before_html == '<div>newer backup</div>'


def test_revoked_restore_purges_snapshot_and_never_writes(monkeypatch):
    ex, live, before, snapshot = _saved_write(monkeypatch)
    lib = library.load()
    lib.ignore.add('n1')
    library.save(lib)
    assert not ex.restore(workspace.TODAY, before).ok
    assert ex_mod.undo.peek('n1') is None
    assert len(live['writes']) == 1


def test_rich_saved_body_is_never_restored_as_lossy_html(monkeypatch):
    ex, live, before, snapshot = _saved_write(monkeypatch)
    rich = '<div>old</div><object data="cid:attachment"></object>'
    ex_mod.undo.save('n1', rich, ex_mod.revision(live['body']), 'rich-write')
    ex_mod.undo.promote('n1', 'rich-write')
    snapshot = ex_mod.undo.peek('n1')
    result = ex.restore(workspace.TODAY, rich)
    assert not result.ok and 'rich' in result.reason
    assert ex_mod.undo.peek('n1') == snapshot
    assert len(live['writes']) == 1


def test_crashed_write_retains_actual_preimage_until_verified_reconciliation(monkeypatch):
    from notron import recovery
    live = in_note(monkeypatch, workspace.TODAY)
    before = live['body']
    ex = ex_mod.Executor(audit=False)
    write = ex_mod.capture_write(workspace.TODAY, mode='replace')
    write.markdown = 'new plan'
    class Crash(BaseException):
        pass
    def crash(name, oid):
        if name == 'after_external_save':
            raise Crash()
    monkeypatch.setattr(recovery, 'boundary', crash)
    import pytest
    with pytest.raises(Crash):
        ex.apply_write(write)
    snapshot = ex_mod.undo.peek('n1')
    assert snapshot.before_html == before
    assert snapshot.after_revision is None  # uncertain effect cannot authorize a restore
    assert not ex.replace(workspace.TODAY, 'another plan').ok
    assert ex_mod.undo.peek('n1') == snapshot
    monkeypatch.setattr(recovery, 'boundary', lambda *args: None)
    assert ex.apply_write(write).ok
    assert ex_mod.undo.peek('n1').after_revision == ex_mod.revision(live['body'])
    assert len(live['writes']) == 1


# --- a failed action says what macOS said ----------------------------------

def test_a_failed_action_reports_what_macos_said_not_a_python_class_name(monkeypatch):
    """It used to read "event app said no (EventKitError)" whether the calendar
    was denied, read-only or gone. Only the first of those has a fix the user
    can carry out, and only if she says which one it is."""
    from datetime import datetime, timedelta

    from notron import eventkit
    from notron.state import Action

    soon = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT14:00")
    ex = ex_mod.Executor(audit=False)
    monkeypatch.setattr(ex_mod.reminders, 'resolve_targets', lambda *a, **kw: [{'id': 'inbox', 'title': 'Inbox'}])
    boom = eventkit.EventKitError(
        "could not create the event: save failed — Calendar access denied")

    def explode(_action):
        raise boom

    ex._perform = explode
    result = ex.do(Action(kind="reminder", op="create", title="Dentist", when=soon))
    assert result.ok is False
    assert "Calendar access denied" in result.reason
    assert "EventKitError" not in result.reason


def test_a_failure_message_stays_short_enough_to_read_in_a_note(monkeypatch):
    from datetime import datetime, timedelta

    from notron.state import Action

    soon = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT14:00")
    ex = ex_mod.Executor(audit=False)
    monkeypatch.setattr(ex_mod.reminders, 'resolve_targets', lambda *a, **kw: [{'id': 'inbox', 'title': 'Inbox'}])

    def explode(_action):
        raise RuntimeError("x" * 5000)

    ex._perform = explode
    result = ex.do(Action(kind="reminder", op="create", title="Call back", when=soon))
    assert len(result.reason) <= ex_mod.MAX_REASON_CHARS


def test_byte_identical_bodies_are_a_landed_write():
    from notron.executor import write_landed
    body = '<div>Exact</div>'
    assert write_landed(body, body)


def test_a_write_that_landed_is_not_divergent_when_notes_re_encodes_it():
    """Apple Notes is a renderer, not a byte store.

    Measured 2026-09-22: an answer of 1,070 characters was written into
    `📥 Ask Notron`, was present in the note, and was reported as "post-write
    divergence; outcome needs review". Left alone, every successful write would
    have been flagged — and a signal that fires on success is a signal people
    learn to ignore, which costs the one time it means something.
    """
    from notron.executor import write_landed

    expected = '<div>Hello <i>world</i></div><div>Second line</div>'
    for reencoded in ('<div>Hello <em>world</em></div><div>Second line</div>',
                      '<div>Hello <i>world</i></div><div>Second line</div><br>',
                      '<div>Hello <i>world</i><br>Second line</div>'):
        assert write_landed(expected, reencoded), reencoded


def test_a_write_that_did_not_take_is_still_divergent():
    """The point of the check survives: if the content is absent the flattened
    text differs, and the outcome still needs review. This must not become a
    rubber stamp."""
    from notron.executor import write_landed

    expected = '<div>Hello</div><div>the new answer</div>'
    for untouched in ('<div>Hello</div>',                       # nothing written
                      '<div>Hello</div><div>the new</div>',     # truncated
                      '<div>Hello</div><div>something else</div>'):
        assert not write_landed(expected, untouched), untouched


def test_an_entirely_unrelated_note_is_divergent():
    from notron.executor import write_landed
    assert not write_landed('<div>intended</div>', '')
