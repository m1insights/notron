import io

import pytest


def test_missing_credentials_never_use_environment(monkeypatch):
    from notron import credentials
    from notron.brain import Brain
    monkeypatch.setenv('NEBIUS_API_KEY', 'synthetic-environment-key')
    credentials.configure(None)
    with pytest.raises(credentials.CredentialUnavailable):
        Brain.from_credentials()


def test_locked_or_lost_key_pauses_before_transport(outbound_transport):
    from notron import credentials
    from notron.outbound import Passage
    brain, calls = outbound_transport
    class Locked:
        def get(self, name):
            raise credentials.CredentialUnavailable('Keychain unavailable')
    credentials.configure(Locked())
    with pytest.raises(credentials.CredentialUnavailable):
        brain.ask(system='static', user=[Passage('synthetic', 'user_request')], purpose='route')
    assert calls.chat == []


def test_startup_refuses_and_spawns_nothing_without_a_signed_bundle(monkeypatch):
    """The P06 gate is not gone -- it is now a real check instead of an
    unconditional refusal. With nothing verifiable to point at, startup must
    refuse BEFORE executing anything, and must leave the provider unset so every
    protected command stays paused."""
    from notron import bundle, credentials
    import subprocess

    def refuse():
        raise bundle.BundleUnavailable('nothing signed here')

    monkeypatch.setattr(bundle, 'keychain_helper', refuse)
    monkeypatch.setattr(subprocess, 'Popen',
                        lambda *a, **k: pytest.fail('must not start an unverified helper'))
    with pytest.raises(credentials.CredentialUnavailable, match='signed Notron bundle'):
        credentials.startup()
    assert credentials._provider is None


def test_missing_key_does_not_generate_replacement(tmp_path):
    from notron import credentials, securestore
    class Missing:
        def get(self, name): return None
        def put(self, *args): pytest.fail('must not regenerate a lost key')
    credentials.configure(Missing())
    with pytest.raises(credentials.CredentialUnavailable):
        securestore.read_json(tmp_path / 'undo.json')


def test_missing_and_rotated_storage_key_block_all_boundaries(outbound_transport, _task3_storage, tmp_path):
    from notron import credentials, research, undo
    from notron.securestore import IntegrityError
    from notron.outbound import Passage
    brain, calls = outbound_transport
    undo.save('n1', 'synthetic original')
    _task3_storage.delete(credentials.STORAGE_KEY)
    passages = [Passage('synthetic', 'user_request')]
    for call in (lambda: brain.embed(passages), lambda: research.search(passages), lambda: undo.peek('n1')):
        with pytest.raises(credentials.CredentialUnavailable): call()
    _task3_storage.put(credentials.STORAGE_KEY, b'x' * 32)
    with pytest.raises(IntegrityError): brain.embed(passages)
    assert not calls.embed and not calls.search
    _task3_storage.put(credentials.STORAGE_KEY, bytes(range(32)))
    assert undo.peek('n1').before_html == 'synthetic original'


def test_keychain_bridge_uses_dedicated_pipe_and_sanitizes_errors(monkeypatch, tmp_path, capsys):
    from notron import credentials
    import base64
    import json
    import os
    import socket
    import subprocess
    import threading
    calls, requests = [], []
    class Process:
        returncode = 0
        def __init__(self, argv, **kw):
            calls.append((argv, kw))
            fd = os.dup(kw['pass_fds'][0])
            def serve():
                with socket.socket(fileno=fd) as channel:
                    raw = b''
                    while block := channel.recv(4096): raw += block
                    request = json.loads(raw)
                    requests.append(request)
                    channel.sendall(json.dumps({'status': 'ok', 'value': base64.b64encode(b'synthetic-key').decode()}).encode())
            self.worker = threading.Thread(target=serve)
            self.worker.start()
        def wait(self, **kw): self.worker.join(); return 0
        def poll(self): return 0
    monkeypatch.setattr(subprocess, 'Popen', Process)
    store = credentials.KeychainStore(tmp_path / 'mock-helper')
    store.put(credentials.NEBIUS_KEY, b'synthetic-key')
    assert store.get(credentials.NEBIUS_KEY) == b'synthetic-key'
    store.delete(credentials.NEBIUS_KEY)
    assert [r['operation'] for r in requests] == ['put', 'get', 'delete']
    for argv, kw in calls:
        assert 'synthetic-key' not in str(argv) + str(kw)
        assert kw['stdout'] == kw['stderr'] == subprocess.DEVNULL
        assert argv == [str(tmp_path / 'mock-helper'), '--credential-fd', str(kw['pass_fds'][0])]
        assert not kw.get('shell', False)
        assert kw['env'] == {'PATH': '/usr/bin:/bin'}
    assert capsys.readouterr().out == ''
    def failed(*args, **kwargs): raise OSError('synthetic-private-credential')
    monkeypatch.setattr(subprocess, 'Popen', failed)
    with pytest.raises(credentials.CredentialUnavailable) as error:
        store.get(credentials.NEBIUS_KEY)
    assert 'synthetic-private' not in str(error.value)


def test_locked_storage_prevents_apple_reads_and_operations(monkeypatch):
    from notron import credentials, notes, graph, care, reflect, daily, filer
    from notron.executor import Executor
    from notron.state import Action
    credentials.configure(None)
    monkeypatch.setattr(notes, 'find_note', lambda *a: pytest.fail('protected read while locked'))
    monkeypatch.setattr(notes, 'warm_up', lambda: pytest.fail('Apple access while locked'))
    for call in (lambda: graph.run('synthetic', brain=None), lambda: care.check(),
                 lambda: reflect.run(None), lambda: daily.morning(None),
                 lambda: filer.run(None), lambda: Executor().append('Synthetic', 'text')):
        with pytest.raises(credentials.CredentialUnavailable): call()


def test_legacy_repository_cache_blocks_cloud_even_when_managed_store_empty(monkeypatch, tmp_path, outbound_transport):
    from notron import retention
    from notron.outbound import Passage
    from notron.securestore import StorageError
    legacy = tmp_path / 'old-repository-cache'
    legacy.mkdir()
    (legacy / 'undo.json').write_text('{"n1":"synthetic-private"}')
    monkeypatch.setattr(retention, 'LEGACY_ROOT', legacy)
    brain, calls = outbound_transport
    with pytest.raises(StorageError):
        brain.ask(system='static', user=[Passage('synthetic', 'user_request')], purpose='route')
    assert calls.chat == []


# --- provisioning surface (`notron key`) -----------------------------------
#
# These cover the rules that keep a setup command from widening the credential
# boundary: which names may be set, what a paste must look like, and the fact
# that the secret travels on stdin rather than in argv.


def test_provision_api_key_stores_the_trimmed_value(_task3_storage):
    from notron import credentials
    credentials.provision_api_key('tavily-api-key', '  synthetic-key-value\n')
    assert _task3_storage.values['tavily-api-key'] == b'synthetic-key-value'


def test_provision_api_key_refuses_names_outside_the_provisionable_set(_task3_storage):
    """`storage-key` is generated by `storage initialize` against an empty
    destination and `managed-refresh` is native-only. Letting a general "store a
    key" command write either would widen a deliberate boundary."""
    from notron import credentials
    for name in (credentials.STORAGE_KEY, 'managed-refresh', 'anything-at-all'):
        with pytest.raises(credentials.CredentialUnavailable):
            credentials.provision_api_key(name, 'synthetic-key-value')


def test_provision_api_key_refuses_a_paste_accident(_task3_storage):
    """A multi-line or oversized paste stores fine and then fails much later,
    somewhere far from the cause. Refuse it at the door."""
    from notron import credentials
    for bad in ('', '   ', 'line one\nline two', 'has internal space', 'x' * 5000):
        with pytest.raises(credentials.CredentialUnavailable):
            credentials.provision_api_key('tavily-api-key', bad)


def test_provision_api_key_detects_a_store_that_did_not_take(monkeypatch):
    from notron import credentials

    class Loses:
        def get(self, name): return None
        def put(self, name, value): pass
        def delete(self, name): pass

    credentials.configure(Loses())
    with pytest.raises(credentials.CredentialUnavailable, match='verified'):
        credentials.provision_api_key('tavily-api-key', 'synthetic-key-value')


def test_provisioned_reports_presence_and_never_a_value(_task3_storage):
    """`_task3_storage` seeds nebius only, so this pins both states."""
    from notron import credentials
    state = dict(credentials.provisioned())
    assert set(state) == set(credentials.PROVISIONABLE)
    assert state['nebius-api-key'] is True
    assert state['tavily-api-key'] is False


def test_forget_api_key_removes_only_provisionable_names(_task3_storage):
    from notron import credentials
    credentials.provision_api_key('tavily-api-key', 'synthetic-key-value')
    credentials.forget_api_key('tavily-api-key')
    assert credentials.get('tavily-api-key') is None
    for name in (credentials.STORAGE_KEY, 'managed-refresh'):
        with pytest.raises(credentials.CredentialUnavailable):
            credentials.forget_api_key(name)


def test_key_command_reads_the_secret_from_stdin_never_argv(monkeypatch, capsys, _task3_storage):
    """argv is visible to every process on the machine and lands in shell
    history, so the secret must never be an argument -- and must not be echoed."""
    from notron import cli
    monkeypatch.setattr('sys.stdin', io.StringIO('synthetic-stdin-secret\n'))
    cli.main(['key', 'set', 'tavily-api-key'])
    captured = capsys.readouterr()
    assert 'synthetic-stdin-secret' not in captured.out
    assert 'synthetic-stdin-secret' not in captured.err
    assert _task3_storage.values['tavily-api-key'] == b'synthetic-stdin-secret'


def test_key_list_prints_names_and_presence_but_no_secrets(_task3_storage, capsys):
    from notron import cli
    cli.main(['key', 'list'])
    out = capsys.readouterr().out
    assert 'nebius-api-key' in out and 'missing' in out
    assert 'synthetic-inference-key' not in out


def test_setup_commands_report_their_specific_reason(monkeypatch, capsys, tmp_path):
    """The generic "Protected processing paused" message hid an actionable reason
    twice -- a wrong paste from `key set`, and the list of blocking files from
    `storage initialize`. Both looked like dead ends when both were fixable. This
    pins the specific message reaching the person who ran the command."""
    from notron import cli, credentials, health

    (tmp_path / 'leftover-from-an-old-run.enc').write_bytes(b'x')
    monkeypatch.setattr(health, 'control_artifacts', lambda root: set())

    class Empty:
        def get(self, name): return None
        def put(self, name, value): pass
        def delete(self, name): pass

    credentials.configure(Empty())
    try:
        with pytest.raises(SystemExit):
            cli.main(['storage', 'initialize', '--target', str(tmp_path)])
        err = capsys.readouterr().err
        assert 'leftover-from-an-old-run.enc' in err, 'the blocking file must be named'
        assert 'empty destination' in err
    finally:
        credentials.configure(None)


def test_key_set_reports_why_a_paste_was_refused(monkeypatch, capsys, _task3_storage):
    """Same property for `key set`: a person needs to know the paste was the
    problem, not that the machine is unavailable."""
    from notron import cli
    monkeypatch.setattr('sys.stdin', io.StringIO('two\nlines\n'))
    with pytest.raises(SystemExit):
        cli.main(['key', 'set', 'tavily-api-key'])
    err = capsys.readouterr().err
    assert 'more than one line' in err


class _FakeStderr:
    """Captures what the CLI writes, and claims to be a terminal or not."""
    def __init__(self, tty): self.text, self._tty = '', tty
    def isatty(self): return self._tty
    def write(self, text): self.text += text
    def flush(self): pass


def _locked_credentials():
    from notron import credentials

    class Locked:
        def get(self, name): raise RuntimeError('keychain refused')
        def put(self, name, value): pass
        def delete(self, name): pass

    credentials.configure(Locked())


def test_a_person_at_the_terminal_sees_the_reason(monkeypatch):
    """Generic in a log, specific at a terminal.

    The generic line hid an actionable reason three times while building this
    bridge -- a wrong paste, a leftover file, a missing setup step -- each of
    which looked like the same dead end.
    """
    import sys
    from notron import cli, credentials

    _locked_credentials()
    try:
        captured = _FakeStderr(tty=True)
        monkeypatch.setattr(sys, 'stderr', captured)
        with pytest.raises(SystemExit):
            cli.main(['key', 'list'])
        assert 'Protected processing paused' in captured.text
        assert 'Keychain unavailable' in captured.text, 'the reason must reach the person'
    finally:
        credentials.configure(None)


def test_a_log_gets_only_the_generic_line(monkeypatch):
    """Nothing specific lands in a launchd file or a notification, where it is
    neither actionable nor necessarily private."""
    import sys
    from notron import cli, credentials

    _locked_credentials()
    try:
        captured = _FakeStderr(tty=False)
        monkeypatch.setattr(sys, 'stderr', captured)
        with pytest.raises(SystemExit):
            cli.main(['key', 'list'])
        assert 'Protected processing paused' in captured.text
        assert 'Keychain unavailable' not in captured.text
    finally:
        credentials.configure(None)
