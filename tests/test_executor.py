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
    assert ex_mod.undo._load() == {'n1': before}


def test_a_refused_write_saves_no_undo(monkeypatch):
    live = in_note(monkeypatch, 'Recipes', 'Notes')
    assert not ex_mod.Executor(audit=False).replace('Recipes', 'rewrite', folder='Notes').ok
    assert ex_mod.undo._load() == {} and live['writes'] == []


def test_restore_writes_the_body_back_verbatim(monkeypatch):
    live = in_note(monkeypatch, workspace.TODAY)
    original = '<div>Today</div><div>*original* — her words, not markdown</div>'
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
    in_note(monkeypatch, workspace.TODAY)
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
