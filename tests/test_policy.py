"""Production permission contracts; all IDs and content are synthetic."""
import json
import pytest
from notron import library, policy


def configured(path, **changes):
    raw = dict(homes=[], ignore=[], decided=['n1'], chosen_at='2026-09-04T12:00')
    raw.update(changes)
    path.write_text(json.dumps(raw))
    return policy.load_policy(path)


def test_configured_zero_homes_never_allows_filing(tmp_path):
    p = configured(tmp_path / 'library.json')
    assert p.status == 'ready'
    assert p.can_read('n1')
    assert not p.can_file('n1')
    assert not p.can_read('new')


def test_missing_and_corrupt_are_distinct_and_deny(tmp_path):
    path = tmp_path / 'library.json'
    assert policy.load_policy(path).status == 'unconfigured'
    for text in ['{', '[]', '{}', 'null', '{"version":99}', '\ufffd']:
        path.write_text(text)
        p = policy.load_policy(path)
        assert p.status == 'corrupt'
        assert not p.can_read('n1') and not p.can_file('n1')
        assert not p.can_reply('n1', 'model-invented')


@pytest.mark.parametrize('change', [dict(version=2), dict(version=True), dict(homes='n1'),
    dict(ignore=[1]), dict(decided=None), dict(allow_new_notes='false'),
    dict(start_from='nonsense'), dict(chosen_at=10)])
def test_invalid_fields_fail_closed(tmp_path, change):
    assert configured(tmp_path / 'library.json', **change).status == 'corrupt'


def test_legacy_choices_and_explicit_new_note_setting(tmp_path):
    path = tmp_path / 'library.json'
    p = configured(path, homes=['home'], ignore=['ignored'], decided=['n1', 'ignored'])
    assert p.can_file('home') and p.can_read('n1')
    assert not p.can_read('ignored') and not p.can_read('new')
    p = configured(path, allow_new_notes=True)
    assert p.can_read('new') and not p.can_file('new')


def test_reply_capability_is_note_bound_scoped_and_not_a_filing_grant(tmp_path, monkeypatch):
    path = tmp_path / 'library.json'
    monkeypatch.setattr(library, 'STATE', path)
    p = configured(path, decided=['n1', 'n2'], ignore=['n3'])
    assert not p.can_reply('n1', 'arbitrary')
    with policy.explicit_reply('n1') as request_id:
        assert p.can_reply('n1', request_id)
        assert not p.can_reply('n2', request_id)
        assert not p.can_file('n1')
    assert not p.can_reply('n1', request_id)
    with pytest.raises(policy.PolicyError):
        with policy.explicit_reply('n3'):
            pass


def test_backup_never_restores_silently(tmp_path, monkeypatch):
    path = tmp_path / 'library.json'
    monkeypatch.setattr(library, 'STATE', path)
    library.save(library.Library(homes={'n1'}))
    library.save(library.Library(decided={'n1'}))
    path.write_text('{')
    assert policy.load_policy(path).status == 'corrupt'
    with pytest.raises(policy.PolicyError):
        library.save(library.Library(homes={'n2'}))
    policy.restore_policy(path)
    assert policy.load_policy(path).can_file('n1')
    assert path.with_name('library.json.corrupt').read_text() == '{'


def test_executor_rechecks_policy_and_reply_scope(tmp_path, monkeypatch):
    from notron import executor, notes
    path = tmp_path / 'library.json'
    monkeypatch.setattr(library, 'STATE', path)
    configured(path, decided=['n1', 'n2'])
    live = notes.Note('n1', 'Readable', 'Notes', '')
    monkeypatch.setattr(notes, 'find_note', lambda *a: live)
    reads, writes = [], []
    monkeypatch.setattr(notes, 'read_body', lambda nid: reads.append(nid) or '<div>Readable</div><div>@notron hi</div>')
    monkeypatch.setattr(notes, 'write_body', lambda nid, body: writes.append(nid))
    ex = executor.Executor(audit=False)
    assert not ex.append('Readable', 'automatic', folder='Notes').ok
    assert reads == []
    with policy.explicit_reply('n1'):
        assert ex.insert('Readable', 'answer', folder='Notes', after=1).ok
        assert not ex.insert('Readable', 'another answer', folder='Notes', after=1).ok
    assert writes == ['n1']
    with policy.explicit_reply('n1'):
        configured(path, ignore=['n1'])
        assert not ex.insert('Readable', 'answer', folder='Notes', after=1).ok
    assert writes == ['n1']


def test_workspace_name_is_not_permission(tmp_path, monkeypatch):
    from notron import executor, notes, workspace
    path = tmp_path / 'library.json'
    monkeypatch.setattr(library, 'STATE', path)
    configured(path, system_notes={workspace.ASK: 'real-ask'})
    monkeypatch.setattr(notes, 'find_note', lambda *a: notes.Note('imposter', workspace.ASK, workspace.FOLDER, ''))
    monkeypatch.setattr(notes, 'read_body', lambda *a: pytest.fail('excluded note read'))
    assert not executor.Executor(audit=False).append(workspace.ASK, 'answer').ok


def test_rewrite_file_cannot_override_read_only_or_corrupt_policy(tmp_path, monkeypatch):
    from notron import rewrite
    path = tmp_path / 'library.json'
    monkeypatch.setattr(library, 'STATE', path)
    configured(path)
    rewrite.allow('n1')
    assert not rewrite.allowed('n1')
    configured(path, homes=['n1'])
    assert rewrite.allowed('n1')
    path.write_text('{')
    assert not rewrite.allowed('n1')


def test_graph_pauses_before_models_or_note_reads(tmp_path, monkeypatch):
    from notron import graph, notes
    monkeypatch.setattr(library, 'STATE', tmp_path / 'missing.json')
    monkeypatch.setattr(notes, 'read_body', lambda *a: pytest.fail('unexpected read'))
    with pytest.raises(policy.PolicyError):
        graph.run('synthetic question', brain=object())


def test_setup_registers_system_ids_without_enabling_ai(tmp_path, monkeypatch):
    from notron import workspace
    path = tmp_path / 'library.json'
    monkeypatch.setattr(library, 'STATE', path)
    workspace.bootstrap()
    p = policy.load_policy(path)
    assert p.status == 'unconfigured'
    assert set(p.system_notes) == set(workspace.SYSTEM_NOTES)
    assert not p.can_read(p.system_notes[workspace.ASK])


def test_cli_save_preserves_system_ids_and_recovery_is_visible(tmp_path, monkeypatch, capsys):
    import io
    from notron import cli, workspace
    path = tmp_path / 'library.json'
    monkeypatch.setattr(library, 'STATE', path)
    configured(path, system_notes={workspace.ASK: 'ask-id'}, homes=['n1'])
    payload = dict(homes=[], ignore=[], decided=['n1'], chosen_at='2026-09-04T12:00')
    monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps(payload)))
    cli.main(['library', 'save'])
    assert policy.load_policy(path).system_notes[workspace.ASK] == 'ask-id'
    assert not policy.load_policy(path).can_file('n1')
    path.write_text('{')
    cli.main(['library', 'recover'])
    assert 'restored' in capsys.readouterr().out.lower()
    assert policy.load_policy(path).can_file('n1')


def test_cli_read_explicitly_records_the_selected_id(tmp_path, monkeypatch):
    from notron import cli, notes, index
    path = tmp_path / 'library.json'
    monkeypatch.setattr(library, 'STATE', path)
    monkeypatch.setattr(notes, 'list_all_notes', lambda: [notes.Note('n1', 'Readable', 'Notes', '')])
    monkeypatch.setattr(index, 'glimpses', lambda *a, **k: {})
    cli.main(['library', '--read', 'n1'])
    assert policy.load_policy(path).can_read('n1')
    assert not policy.load_policy(path).can_file('n1')


def test_watcher_tag_reply_in_read_only_note_end_to_end(tmp_path, monkeypatch):
    from notron import notes, watch, workspace
    from tests.test_graph import FakeBrain
    path = tmp_path / 'library.json'
    monkeypatch.setattr(library, 'STATE', path)
    configured(path, decided=['n1'])
    n = notes.Note('n1', 'Readable', 'Notes', 'Friday, 4 September 2026 at 12:00:00')
    body = {'value': '<div>Readable</div><div>@notron explain gravity</div>'}
    monkeypatch.setattr(notes, 'list_all_notes', lambda: [n])
    monkeypatch.setattr(notes, 'find_note', lambda folder, title: n if (folder, title) == ('Notes', 'Readable') else None)
    monkeypatch.setattr(notes, 'read_body', lambda nid: body['value'])
    monkeypatch.setattr(notes, 'write_body', lambda nid, text: body.update(value=text))
    brain = FakeBrain(answer='Gravity attracts objects.')
    watcher = watch.Watcher(brain=brain, settle=0)
    watcher.sweep_mentions()
    watcher.sweep_mentions()
    assert 'Gravity attracts objects.' in body['value']
    assert not policy.current().can_file('n1')


def test_reply_cannot_land_in_a_same_title_home(tmp_path, monkeypatch):
    from notron import executor, notes
    path = tmp_path / 'library.json'
    monkeypatch.setattr(library, 'STATE', path)
    configured(path, homes=['n2'], decided=['n1'])
    monkeypatch.setattr(notes, 'find_note', lambda *a: notes.Note('n2', 'Twin', 'Notes', ''))
    monkeypatch.setattr(notes, 'read_body', lambda *a: pytest.fail('wrong twin read'))
    with policy.explicit_reply('n1'):
        assert not executor.Executor(audit=False).insert('Twin', 'private reply', folder='Notes', after=0).ok


def test_care_never_reads_unregistered_system_note(tmp_path, monkeypatch):
    from notron import notes, care, permissions, index, workspace
    monkeypatch.setattr(library, 'STATE', tmp_path / 'missing.json')
    monkeypatch.setattr(notes, 'find_note', lambda *a: notes.Note('unregistered', workspace.ABOUT, workspace.FOLDER, ''))
    monkeypatch.setattr(notes, 'read_body', lambda *a: pytest.fail('unapproved read'))
    monkeypatch.setattr(permissions, 'check', lambda: [])
    monkeypatch.setattr(care, '_usage', lambda *a, **kw: dict(calls=0, **{'in': 0, 'out': 0}))
    monkeypatch.setattr(index, 'exists', lambda: False)
    care.check()


@pytest.mark.parametrize('raw', [[], {'version': 99, 'allow': ['n1']},
    {'allow': 'n1'}, {'allow': ['n1'], 'default_new': 'invalid'}])
def test_invalid_rewrite_permissions_never_grant(tmp_path, monkeypatch, raw):
    from notron import rewrite
    path = tmp_path / 'library.json'
    monkeypatch.setattr(library, 'STATE', path)
    configured(path, homes=['n1'])
    rewrite.STATE.write_text(json.dumps(raw))
    assert not rewrite.allowed('n1')
    assert rewrite.default_for_new_notes() == 'ask'


def test_invalid_backup_is_not_restored(tmp_path):
    path = tmp_path / 'library.json'
    path.write_text('{')
    path.with_name('library.json.bak').write_text('{"version":99}')
    with pytest.raises(policy.PolicyError):
        policy.restore_policy(path)
    assert path.read_text() == '{'


def test_policy_write_interruption_preserves_valid_policy_and_backup(tmp_path, monkeypatch):
    import os
    path = tmp_path / 'library.json'
    monkeypatch.setattr(library, 'STATE', path)
    library.save(library.Library(decided={'n1'}))
    replace = os.replace
    def interrupt(source, destination):
        if destination == path:
            raise OSError('synthetic interruption before policy replacement')
        replace(source, destination)
    monkeypatch.setattr(os, 'replace', interrupt)
    with pytest.raises(OSError):
        library.save(library.Library(homes={'n1'}))
    assert policy.load_policy(path).status == 'ready'
    assert not policy.load_policy(path).can_file('n1')
    assert not policy.load_policy(path.with_name('library.json.bak')).can_file('n1')


def test_embedding_and_inference_pause_without_policy(tmp_path, monkeypatch):
    from notron.brain import Brain
    monkeypatch.setattr(library, 'STATE', tmp_path / 'missing.json')
    brain = object.__new__(Brain)  # no client initialization or provider call
    with pytest.raises(policy.PolicyError):
        brain._call('fast', 'static', [], 100, False, 0, 'route')
    with pytest.raises(policy.PolicyError):
        brain.embed([])


def test_read_only_organizer_does_not_promise_standing_rewrite(tmp_path, monkeypatch):
    from notron import nodes, notes
    from notron.state import State
    from tests.test_nodes import FakeBrain
    path = tmp_path / 'library.json'
    monkeypatch.setattr(library, 'STATE', path)
    configured(path)
    monkeypatch.setattr(notes, 'find_note', lambda *a: notes.Note('n1', 'Readable', 'Notes', ''))
    monkeypatch.setattr(notes, 'read_body', lambda *a: '<div>Readable</div><div>parking garages</div>')
    state = State(intent='organize', request='clean this up', source_note_id='n1', reply_to=('Readable', 'Notes', 1))
    nodes.organizer(state, brain=FakeBrain(answer='A cleaned copy of the parking garages'))
    assert nodes.ORGANIZE_ASK not in state.answer
    assert 'read only' in state.answer.lower()


def test_rewrite_recovery_is_explicit_and_validated(tmp_path, monkeypatch, capsys):
    from notron import rewrite, cli
    rewrite.allow('n1')
    rewrite.set_default_for_new_notes('never')
    rewrite.STATE.write_text('{')
    assert not rewrite.allowed('n1')
    with pytest.raises(policy.PolicyError):
        rewrite.allow('n1')
    cli.main(['rewrite', '--recover'])
    assert 'restored' in capsys.readouterr().out.lower()
    assert rewrite.allowed('n1')
    assert rewrite.STATE.with_name('rewrite.json.corrupt').read_text() == '{'


def test_setup_refuses_ambiguous_system_note_ids(tmp_path, monkeypatch):
    from notron import workspace, notes
    monkeypatch.setattr(library, 'STATE', tmp_path / 'library.json')
    monkeypatch.setattr(notes, 'list_notes', lambda *a: [
        notes.Note('a', workspace.ASK, workspace.FOLDER, ''),
        notes.Note('b', workspace.ASK, workspace.FOLDER, '')])
    monkeypatch.setattr(notes, 'create_note', lambda *a: pytest.fail('ambiguous setup must stop'))
    with pytest.raises(policy.PolicyError):
        workspace.bootstrap()
