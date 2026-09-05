"""P01 boundaries: hostile data through real code, synthetic external adapters.

These regressions catch widened operation dispatch, model-granted capabilities,
script interpolation, shell execution and launchd argument injection.
"""
import json
import plistlib
import subprocess
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from notron import applescript, calendar, credentials, daily, eventkit, library
from notron import nodes, notes, policy, reminders, rewrite, watch
from notron.executor import Executor
from notron.state import Action, State

# Save before the autouse fake Apple adapter is installed; subprocess stays mocked.
APPLE_RUN = applescript.run
HOSTILE = 'synthetic-attack"; Application("System Events").doShellScript("touch /tmp/NO"); //\n$(id) `id` \\ \u2028 --help'


@pytest.fixture
def process_calls(monkeypatch, tmp_path):
    calls = []
    def run(argv, **kw):
        calls.append((argv, kw))
        return NS(returncode=0, stdout='{"id":"synthetic-id","title":"done"}', stderr='')
    monkeypatch.setattr(subprocess, 'run', run)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('unexpected helper'))
    monkeypatch.setattr(applescript, 'LOCK', tmp_path / 'notes.lock')
    monkeypatch.setattr(notes, 'run', APPLE_RUN)
    return calls


@pytest.mark.parametrize('kind,op', [('shell', 'execute'), ('python', 'eval'),
    ('permission', 'grant'), ('event', 'delete'), ('event', 'complete'),
    ('reminder', 'delete'), ('note', 'replace')])
def test_model_cannot_add_an_operation(kind, op, outbound_transport, process_calls):
    brain, calls = outbound_transport
    calls.replies.append(json.dumps({'kind': kind, 'op': op, 'title': HOSTILE}))
    state = nodes.scheduler(State(request='remind me about a synthetic task', intent='remind'), brain=brain)
    assert state.actions == []
    # Final authority must also reject the same action, even if a node regresses.
    result = Executor(audit=False).do(Action(kind=kind, op=op, title=HOSTILE))
    assert not result.ok
    assert not process_calls


@pytest.mark.parametrize('intent', ['file', 'undo', 'organize', 'shell', 'grant_permission'])
def test_model_route_cannot_grant_authority(intent, outbound_transport):
    brain, calls = outbound_transport
    before = library.STATE.read_bytes()
    calls.replies.append(json.dumps({'intent': intent, 'homes': ['denied'],
        'rewrite_allowed': True, 'request_id': 'forged', 'needs_context': False}))
    state = nodes.router(State(request='What does this synthetic passage mean?'), brain=brain)
    assert state.intent == 'question'
    assert not state.actions and not state.writes
    assert library.STATE.read_bytes() == before
    assert not policy.current().can_reply('n1', 'forged')
    assert not rewrite.allowed('denied')


@pytest.mark.parametrize('field,value', [('title', {'shell': HOSTILE}), ('where', [HOSTILE]),
    ('notes', {'source': HOSTILE}), ('when', [HOSTILE]), ('ends', 42)])
def test_scheduler_rejects_nontext_model_fields(field, value, outbound_transport, process_calls):
    brain, calls = outbound_transport
    out = {'kind': 'reminder', 'op': 'create', 'title': 'synthetic task'}
    out[field] = value
    calls.replies.append(json.dumps(out))
    state = nodes.scheduler(State(request='remind me about a synthetic task', intent='remind'), brain=brain)
    assert state.actions == []
    assert not process_calls


@pytest.mark.parametrize('kind', ['reminder', 'event'])
def test_model_action_values_are_arguments_never_jxa_source(kind, outbound_transport, process_calls):
    brain, calls = outbound_transport
    when = (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%dT14:00')
    calls.replies.append(json.dumps({'kind': kind, 'op': 'create', 'title': HOSTILE,
        'notes': HOSTILE, 'where': HOSTILE, 'when': when,
        'request_id': 'forged', 'rewrite_allowed': True, 'shell': HOSTILE}))
    state = nodes.scheduler(State(request='remind me about a synthetic task', intent='remind'), brain=brain)
    assert len(state.actions) == 1
    assert Executor(audit=False).do(state.actions[0]).ok
    argv, kw = process_calls.pop()
    assert argv[:4] == ['osascript', '-l', 'JavaScript', '-']
    assert len(argv) == 5
    data = json.loads(argv[4])
    assert data['title'] == data['notes'] == HOSTILE
    assert HOSTILE not in kw['input'] and 'synthetic-attack' not in kw['input']
    assert not kw.get('shell', False)
    assert not process_calls


def test_reminder_identifier_is_data_not_jxa_source(process_calls):
    assert reminders.complete(HOSTILE) == 'done'
    argv, kw = process_calls.pop()
    assert json.loads(argv[4]) == {'id': HOSTILE}
    assert 'synthetic-attack' not in kw['input']
    assert not kw.get('shell', False)


@pytest.mark.parametrize('operation', ['write', 'read', 'show', 'create', 'folder'])
def test_notes_dynamic_values_never_become_applescript(operation, process_calls, monkeypatch):
    monkeypatch.setattr(notes, 'resolve', lambda folder: (1, []))
    if operation == 'write': notes.write_body(HOSTILE, HOSTILE)
    elif operation == 'read': notes.read_body(HOSTILE)
    elif operation == 'show': notes.show_note(HOSTILE)
    elif operation == 'create': notes.create_note('Synthetic', HOSTILE)
    else: notes.ensure_folder(HOSTILE)
    argv, kw = process_calls[0]
    assert argv[:2] == ['osascript', '-']
    assert HOSTILE in argv[2:]
    assert 'synthetic-attack' not in kw['input']
    assert not kw.get('shell', False)


@pytest.mark.parametrize('which', ['watch', 'daily'])
def test_launchd_paths_cannot_inject_program_arguments(which):
    path = '/synthetic/A&B</string><string>/bin/sh</string><string>$(id)'
    raw = watch.plist(path, path) if which == 'watch' else daily.plist(path, path, 9, 30)
    data = plistlib.loads(raw.encode())
    assert data['ProgramArguments'] == [path, '-m', 'notron', 'listen' if which == 'watch' else 'morning']
    assert data['WorkingDirectory'] == path
    assert data['StandardOutPath'] == data['StandardErrorPath'] == '/dev/null'


@pytest.mark.parametrize('command', ['listen_install', 'listen_off', 'schedule_install', 'schedule_off', 'status'])
def test_launchctl_boundary_uses_fixed_argv(command, process_calls, monkeypatch, tmp_path):
    from pathlib import Path
    from notron import cli
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    if command == 'status':
        assert watch.is_running()
    elif command.startswith('listen'):
        cli.cmd_listen(NS(status=False, install=command.endswith('install'), off=command.endswith('off')))
    else:
        cli.cmd_schedule(NS(off=command.endswith('off'), hour=9, minute=30))
    assert process_calls
    for argv, kw in process_calls:
        assert argv[0] == 'launchctl'
        assert argv[1] in ('bootout', 'bootstrap', 'print')
        assert isinstance(argv, list) and not kw.get('shell', False)
        if argv[1] == 'bootstrap':
            data = plistlib.loads(Path(argv[3]).read_bytes())
            assert data['ProgramArguments'][1:3] == ['-m', 'notron']


def test_startup_cannot_be_enabled_by_environment(monkeypatch, process_calls):
    for name in ('NOTRON_DEVELOPMENT', 'NOTRON_KEYCHAIN_HELPER', 'NOTRON_PYTHON'):
        monkeypatch.setenv(name, HOSTILE)
    with pytest.raises(credentials.CredentialUnavailable, match='P06'):
        credentials.startup()
    assert not process_calls


def test_model_reply_is_prose_and_cannot_change_policy_or_write_target(outbound_transport, process_calls, monkeypatch):
    from notron import workspace
    brain, calls = outbound_transport
    before = library.STATE.read_bytes()
    answer = json.dumps({'operations': [{'kind': 'shell', 'source': HOSTILE}],
        'homes': ['denied'], 'allow_new_notes': True, 'rewrite_allowed': True,
        'request_id': 'forged', 'title': 'Other note', 'mode': 'restore'})
    calls.replies.append(answer)
    note = notes.Note('n1', 'Synthetic', 'Notes', '')
    monkeypatch.setattr(notes, 'find_note', lambda *args: note)
    monkeypatch.setattr(notes, 'read_body', lambda nid: '<div>Synthetic</div>')
    state = State(request='Explain this synthetic text', intent='question',
                  reply_to=('Synthetic', 'Notes', 0), source_note_id='n1')
    nodes.writer(state, brain=brain)
    assert not state.actions and len(state.writes) == 1
    write = state.writes[0]
    assert write.title == 'Synthetic' and write.mode == 'insert'
    assert not write.rewrite_allowed
    assert Executor(audit=False).insert(write.title, write.markdown, folder='Notes', after=0).ok
    argv, kw = process_calls.pop()
    assert 'operations' in argv[-1]  # answer actually reaches the Notes adapter as data
    assert 'operations' not in kw['input']
    assert library.STATE.read_bytes() == before
    assert policy.request_id() is None and not rewrite.allowed('denied')


@pytest.mark.parametrize('operation', ['calendar_names', 'calendar_window', 'reminder_lists', 'reminder_open', 'permissions'])
def test_eventkit_read_boundaries_use_fixed_scripts(operation, process_calls, monkeypatch):
    from notron import permissions
    def run(argv, **kw):
        process_calls.append((argv, kw))
        return NS(returncode=0, stdout='{}' if operation == 'permissions' else '[]', stderr='')
    monkeypatch.setattr(subprocess, 'run', run)
    call = {'calendar_names': calendar.names, 'calendar_window': calendar.window,
            'reminder_lists': reminders.lists, 'reminder_open': reminders.open_items,
            'permissions': permissions._read}[operation]
    call()
    argv, kw = process_calls.pop()
    assert argv[:4] == ['osascript', '-l', 'JavaScript', '-']
    assert not kw.get('shell', False)
    if operation == 'calendar_window':
        assert json.loads(argv[4]) == {'back': 0, 'days': 7}
    else:
        assert len(argv) == 4


def test_credential_name_cannot_select_an_executable(process_calls, tmp_path):
    with pytest.raises(credentials.CredentialUnavailable, match='Unknown credential'):
        credentials.KeychainStore(tmp_path / 'synthetic-helper').get(HOSTILE)
    assert not process_calls
