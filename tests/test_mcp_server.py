"""`notron mcp serve`: the Apple bridge as an MCP server.

The server is a thin shell over `bridge`; these tests pin the shell itself —
which tools exist, what they promise a client, and that nothing reaches stdout,
which is the protocol channel (one stray print and the client drops the session).
"""

import json
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

pytest.importorskip("mcp")

import anyio

from notron import bridge, mcp_server
from notron.policy import PolicyError

READS = {"notes_search", "notes_list", "notes_read", "agenda"}


def _tools(app):
    return {t.name: t for t in anyio.run(app.list_tools)}


def _call(app, name, args):
    result = anyio.run(app.call_tool, name, args)
    assert not result.is_error, result
    return json.loads(result.content[0].text)


def _never():
    raise AssertionError("the brain was built before anyone asked")


def test_build_registers_exactly_the_bridge_tools():
    app = mcp_server.build(writes=False, ask=True, brain_factory=_never)
    assert set(_tools(app)) == READS | {"ask_notron"}


def test_no_ask_offers_only_the_read_tools():
    app = mcp_server.build(writes=False, ask=False, brain_factory=_never)
    assert set(_tools(app)) == READS


def test_read_tools_promise_read_only():
    tools = _tools(mcp_server.build(writes=True, ask=True, brain_factory=_never))
    for name in READS:
        assert tools[name].annotations.read_only_hint is True, name


def test_writes_flips_ask_notron_read_only_hint():
    """A client that trusts readOnlyHint must not auto-approve a server that can file notes."""
    off = _tools(mcp_server.build(writes=False, ask=True, brain_factory=_never))
    on = _tools(mcp_server.build(writes=True, ask=True, brain_factory=_never))
    assert off["ask_notron"].annotations.read_only_hint is True
    assert on["ask_notron"].annotations.read_only_hint is False
    # Unset is "may be destructive" by MCP default: once a note is opted in, the
    # organizer rewrites it in place, so claiming non-destructive would be untrue.
    assert on["ask_notron"].annotations.destructive_hint is None


def test_build_prints_nothing(capsys):
    """Stdout is the MCP wire. Anything printed there corrupts the first frame."""
    mcp_server.build(writes=True, ask=True, brain_factory=_never)
    out = capsys.readouterr()
    assert out.out == ""


def test_a_policy_error_becomes_an_error_result_not_a_dead_session(monkeypatch):
    def paused(*a, **kw):
        raise PolicyError("Note policy missing; AI paused.")
    monkeypatch.setattr(bridge, "notes_list", paused)
    app = mcp_server.build(writes=False, ask=False, brain_factory=_never)
    out = _call(app, "notes_list", {})
    assert out["error"].startswith("Notron's note policy is not ready")


def test_an_unexpected_error_does_not_leak_its_text(monkeypatch):
    """An exception message can carry a note title or a path into the client's provider."""
    def boom(note_id):
        raise RuntimeError("Can't read note 'Divorce lawyer'")
    monkeypatch.setattr(bridge, "notes_read", boom)
    out = _call(mcp_server.build(writes=False, ask=False, brain_factory=_never), "notes_read",
                {"note_id": "x"})
    assert "Divorce" not in out["error"] and "RuntimeError" in out["error"]


def test_reads_never_build_the_brain(monkeypatch):
    """Note reads work with no Nebius key; only ask_notron needs one."""
    monkeypatch.setattr(bridge, "notes_list", lambda limit: [{"id": "a"}])
    app = mcp_server.build(writes=False, ask=True, brain_factory=_never)
    assert _call(app, "notes_list", {}) == {"notes": [{"id": "a"}]}


def test_a_brain_that_cannot_start_is_an_answer_not_an_exit(monkeypatch):
    """cli._brain prints to stderr and raises SystemExit; inside a server that
    would end the session for every later read."""
    def no_key():
        raise SystemExit(2)
    app = mcp_server.build(writes=False, ask=True, brain_factory=no_key)
    out = _call(app, "ask_notron", {"request": "what's on today"})
    assert "could not start NVIDIA Nemotron" in out["error"]


def test_ask_notron_passes_writes_and_receipts_through(monkeypatch):
    seen = {}

    def fake_ask(request, *, writes, brain, after=None):
        seen.update(request=request, writes=writes, brain=brain, after=after)
        return {"answer": "ok", "results": [], "dry_run": not writes}
    monkeypatch.setattr(bridge, "ask", fake_ask)
    deliver = lambda: None
    brain = object()
    dry = mcp_server.build(writes=False, ask=True, brain_factory=lambda: brain, after_writes=deliver)
    _call(dry, "ask_notron", {"request": "hi"})
    assert seen == {"request": "hi", "writes": False, "brain": brain, "after": None}
    wet = mcp_server.build(writes=True, ask=True, brain_factory=lambda: brain, after_writes=deliver)
    _call(wet, "ask_notron", {"request": "hi"})
    assert seen["writes"] is True and seen["after"] is deliver


def test_config_names_this_interpreter_and_the_serve_command():
    block = json.loads(mcp_server.config())
    assert block == {"mcpServers": {"notron": {"command": sys.executable,
                                               "args": ["-m", "notron", "mcp", "serve"]}}}


def test_ask_notron_runs_on_the_main_thread(monkeypatch):
    """mcp 2.x runs a sync tool on a worker thread, and brain._deadline_guard
    refuses provider calls off the main thread (its deadline is a SIGALRM). So a
    sync ask_notron failed every Nemotron call under the real server, and with
    --writes the failed probe marked the worker unhealthy."""
    import threading
    seen = {}

    def fake_ask(request, *, writes, brain, after=None):
        seen["main"] = threading.current_thread() is threading.main_thread()
        return {"answer": "ok", "results": [], "dry_run": not writes}
    monkeypatch.setattr(bridge, "ask", fake_ask)
    app = mcp_server.build(writes=False, ask=True, brain_factory=lambda: object())
    assert _call(app, "ask_notron", {"request": "hi"})["answer"] == "ok"
    assert seen["main"] is True
