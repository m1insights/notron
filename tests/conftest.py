"""Tests run with no API key, no network and no real Notes app — always.

Every world question now routes to the researcher by design, so a Tavily key
exported in the developer's shell would quietly turn the graph tests into
live web searches. Strip it before every test; a test that wants a key sets
one itself.

The same went for the Notes app itself, and that one had teeth. Any test that
reached code calling `applescript.run` without patching it first talked to the
developer's own Apple Notes — 358 real notes, read as if they were fixtures.
On 2026-09-03 live work on this machine left fourteen junk `📊 Log` notes in
that library. `_notes_is_never_the_real_one` puts a small fake Notes app under
every test instead, so a forgotten patch produces a deterministic fake library
rather than whatever the person running the suite happens to have written down
— and no test can ever write into it.
"""

import pytest

from notron import applescript, mentions, notes, rewrite, undo, library, workspace


@pytest.fixture(autouse=True)
def _no_search_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def _undo_state_is_disposable(monkeypatch, tmp_path):
    """Every successful write now saves an undo slot (`Executor._apply`), so any
    test that lets Executor perform a real write — not just tests/test_executor.py,
    also the Filer and action tests — touches `undo.STATE` whether it means to or
    not. Redirect it everywhere, the same way `_no_search_key` strips a live key
    everywhere, so no test run pollutes the real .notron/undo.json with fake
    note ids and fake bodies. A test that specifically exercises undo.py itself
    still overrides this per-test, same as before."""
    monkeypatch.setattr(undo, "STATE", tmp_path / "undo.json")


@pytest.fixture(autouse=True)
def _mentions_state_is_disposable(monkeypatch, tmp_path):
    """A `mentions.Scanner()` constructed without patching `STATE` first and
    then exercised (`.prime()`/`.changed()`/`.scan()`) writes straight to the
    real, live `.notron/seen.json` — the background listener's own "what has
    she already looked at" record. One test that skipped this (predating this
    fixture, not this branch) overwrote the developer's real file with two
    lines of fake test data during this branch's own work — no Notes content
    was lost, but the listener's next restart would otherwise have treated
    every real note as newly changed and rescanned the whole library for old
    `#notron` tags. Same blanket redirect as undo/rewrite above, so no future
    test can do this again by omission."""
    monkeypatch.setattr(mentions, "STATE", tmp_path / "seen.json")


@pytest.fixture(autouse=True)
def _rewrite_state_is_disposable(monkeypatch, tmp_path):
    """`nodes.organizer` calls `rewrite.allow(note.id)` when it reads a `yes`
    under its own offer, so a node-level or graph-level test can now write to
    the real .notron/rewrite.json — and a fake note id left in there is a
    standing permission to rewrite a real note in place. Redirect it
    everywhere, same as undo above."""
    monkeypatch.setattr(rewrite, "STATE", tmp_path / "rewrite.json")


class FakeNotesApp:
    """An in-memory Notes app, addressed exactly the way the real one is.

    It answers the same scripts `notron/notes.py` sends, so it exercises the
    real index-addressing and name-verification logic rather than stubbing it
    out. `insert_folder` reproduces the one move that broke everything: a new
    folder appearing above an existing one and shifting its index.
    """

    def __init__(self, folders=None):
        self.folders = [(name, list(titles)) for name, titles in (folders or [
            ("Notes", ["Parking Garages", "Supps"]),
            ("Recently Deleted", ["an old thing"]),
            ("🤖 NOTRON", ["📌 About Me", "📥 Ask Notron", "🧠 Brain Dump", "📊 Log"]),
        ])]
        self.bodies: dict[str, str] = {}
        self.calls: list[str] = []

    def insert_folder(self, position: int, name: str) -> None:
        self.folders.insert(position - 1, (name, []))

    def run(self, script: str, *args: str, **kw) -> str:
        if script is notes._FOLDER_NAMES:
            self.calls.append("folders")
            return notes.US.join(name for name, _ in self.folders)

        if script is notes._LIST_BY_INDEX:
            index = int(args[0])
            self.calls.append(f"list:{index}")
            if index > len(self.folders):
                raise applescript.AppleScriptError(
                    'Can\'t get folder %d of application "Notes"' % index)
            name, titles = self.folders[index - 1]
            ids = [f"{name}/{t}" for t in titles]
            dates = ["Wednesday, 2 September 2026 at 21:30:00"] * len(titles)
            rest = notes.RS.join(
                (notes.US.join(ids), notes.US.join(titles), notes.US.join(dates)))
            return f"{name}{notes.RS}{rest}"

        if script is notes._METADATA:
            self.calls.append('metadata')
            for folder, titles in self.folders:
                for title in titles:
                    if args[0] == f'{folder}/{title}':
                        return notes.RS.join((args[0], title, folder, 'observed'))
            return ''

        if script is notes._BODY:
            self.calls.append("body")
            return self.bodies.get(args[0], f"<div>{args[0].split('/')[-1]}</div>")

        if script is notes._CREATE_AT_INDEX:
            index = int(args[0])
            self.calls.append(f"create:{index}")
            name, titles = self.folders[index - 1]
            title = args[1].splitlines()[0]
            titles.append(title)
            self.bodies[f"{name}/{title}"] = args[1]
            return f"{name}/{title}"

        if script is notes._ENSURE_FOLDER:
            self.calls.append("ensure")
            if any(name == args[0] for name, _ in self.folders):
                return "exists"
            self.folders.append((args[0], []))
            return "created"

        # A write, a `show note`, an EventKit JXA script — anything that would
        # have reached the real machine. Loud, with the script in the message,
        # so the test that forgot to patch is obvious from the failure alone.
        raise AssertionError(
            "a test reached the real Notes app — patch it:\n" + script.strip()[:200])


@pytest.fixture(autouse=True)
def _notes_is_never_the_real_one(monkeypatch):
    app = FakeNotesApp()
    monkeypatch.setattr(applescript, "run", app.run)
    monkeypatch.setattr(notes, "run", app.run)
    monkeypatch.setattr(notes, "_folders_cache", None)
    yield app
    notes._folders_cache = None


@pytest.fixture(autouse=True)
def _policy_is_disposable(monkeypatch, tmp_path, _task3_storage, _operation_storage_is_disposable):
    """Existing feature tests explicitly select their synthetic notes.

    Permission regression tests override STATE or save their own selection.
    Never inspect the developer's library.json, even for a read-only test.
    """
    monkeypatch.setattr(library, 'STATE', tmp_path / 'test-policy' / 'library.json')
    library.save(library.Library(
        homes={'n1', 'note-1'}, decided={'n1', 'note-1'},
        allow_new_notes=True,
        system_notes={title: f'{workspace.FOLDER}/{title}' for title in workspace.SYSTEM_NOTES}))


@pytest.fixture(autouse=True)
def _outbound_resources_are_disposable(monkeypatch, tmp_path):
    from notron import brain, care, filer, index, reflect
    import socket
    import urllib.request

    def blocked(*args, **kwargs):
        raise AssertionError('Live network is forbidden in unit tests')

    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'getaddrinfo', blocked)
    monkeypatch.setattr(urllib.request, 'urlopen', blocked)
    monkeypatch.delenv('NEBIUS_API_KEY', raising=False)
    for name in ('NEBIUS_BASE_URL', 'NOTRON_DEVELOPMENT', 'OPENAI_API_KEY', 'OPENAI_ADMIN_KEY'):
        monkeypatch.delenv(name, raising=False)
    for module, attr, name in (
        (brain, 'USAGE_LOG', 'usage.json'), (care, 'USAGE_LOG', 'usage.json'),
        (brain, 'PROVIDER_STATE', 'provider.json'),
        (care, 'MOOD_FILE', 'mood.json'), (filer, 'STATE', 'filer.json'),
        (reflect, 'STATE', 'reflect.json'), (index, 'CACHE', 'index.json'),
        (index, 'VECTORS', 'vectors.npy'),
    ):
        monkeypatch.setattr(module, attr, tmp_path / name)
    monkeypatch.setattr(index, '_MMAP', None)


@pytest.fixture
def outbound_policy():
    def select(*, ignored=(), decided=('approved',), system_notes=None):
        library.save(library.Library(homes=set(), decided=set(decided),
                                    ignore=set(ignored), system_notes=system_notes or {}))
    select()
    return select


@pytest.fixture
def outbound_transport(monkeypatch):
    """Real Brain, fake provider adapters; never construct a credential client."""
    from types import SimpleNamespace as NS
    from notron.brain import Brain
    import json
    from notron import network

    calls = NS(chat=[], embed=[], search=[], replies=[])
    def chat(**kw):
        calls.chat.append(kw)
        reply = calls.replies.pop(0) if calls.replies else 'synthetic answer'
        return NS(choices=[NS(message=NS(content=reply))], usage=None)
    def embed(**kw):
        calls.embed.append(kw)
        return NS(data=[NS(index=i, embedding=[1., 0.]) for i, _ in enumerate(kw['input'])], usage=None)
    class SearchConnection:
        def __init__(self, *args, **kwargs): self.sock = NS(settimeout=lambda value: None)
        def request(self, method, path, *, body, headers):
            calls.search.append(json.loads(body))
        def getresponse(self):
            class Response:
                status = 200
                def getheaders(self): return [('Content-Type', 'application/json')]
                def read(self): return b'{"answer":"synthetic result","results":[]}'
            return Response()
        def close(self): pass
    brain = Brain.__new__(Brain)
    brain._client = NS(chat=NS(completions=NS(create=chat)), embeddings=NS(create=embed))
    monkeypatch.setattr(network, '_ProviderConnection', SearchConnection)
    from notron import credentials
    credentials._provider.put(credentials.SEARCH_KEY, b'synthetic-test-key')
    return brain, calls


@pytest.fixture(autouse=True)
def _task3_storage(monkeypatch, tmp_path, _outbound_resources_are_disposable):
    from notron import credentials, diagnostics, retention
    class MemoryCredentials:
        def __init__(self):
            self.values = {credentials.STORAGE_KEY: bytes(range(32)),
                           credentials.NEBIUS_KEY: b'synthetic-inference-key'}
        def get(self, name): return self.values.get(name)
        def put(self, name, value): self.values[name] = value
        def delete(self, name): self.values.pop(name, None)
    provider = MemoryCredentials()
    monkeypatch.setattr(credentials, '_provider', provider)
    monkeypatch.setattr(diagnostics, 'ROOT', tmp_path / 'diagnostics')
    monkeypatch.setattr(retention, 'LEGACY_ROOT', tmp_path / 'legacy-repository')
    return provider


@pytest.fixture(autouse=True)
def _native_subprocesses_require_mocks(monkeypatch):
    """A missed EventKit/helper/launchctl mock must never touch the host account."""
    import subprocess

    def blocked(*args, **kwargs):
        raise AssertionError('Native subprocesses require a synthetic adapter in unit tests')

    monkeypatch.setattr(subprocess, 'run', blocked)
    monkeypatch.setattr(subprocess, 'Popen', blocked)


@pytest.fixture(autouse=True)
def _operation_storage_is_disposable(monkeypatch, tmp_path, _task3_storage):
    from notron import operations
    monkeypatch.setattr(operations, 'PATH', tmp_path / 'ledger' / 'operations.sqlite3')
    monkeypatch.setenv('TZ', 'UTC')
