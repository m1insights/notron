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


def test_default_native_startup_remains_gated(monkeypatch):
    from notron import credentials
    import subprocess
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('must not start unsigned helper'))
    with pytest.raises(credentials.CredentialUnavailable, match='P06'):
        credentials.startup()


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
    for call in (lambda: brain.embed(passages), lambda: research.search(passages), lambda: undo.pop('n1')):
        with pytest.raises(credentials.CredentialUnavailable): call()
    _task3_storage.put(credentials.STORAGE_KEY, b'x' * 32)
    with pytest.raises(IntegrityError): brain.embed(passages)
    assert not calls.embed and not calls.search
    _task3_storage.put(credentials.STORAGE_KEY, bytes(range(32)))
    assert undo.pop('n1') == 'synthetic original'


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
        assert '--credential-fd' in argv
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
