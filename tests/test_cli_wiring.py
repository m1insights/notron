"""The CLI layer's calls into modules.

Nothing tested these, and it cost a real bug. `cmd_index` passed
`read_attachments=` to `index.build`, whose parameter is `extract=`, so
`notron index` raised TypeError on every single invocation. It survived a
1374-test suite, because the suite tests modules directly and the CLI is the one
place where a name in one file has to match a name in another.

`test_every_cli_call_into_a_module_uses_declared_parameters` parses the CLI's own
source and checks every `module.function(...)` call against the real signature.
That is the general form of the bug, not the single instance.
"""
import ast
import importlib
import inspect
import textwrap
from types import SimpleNamespace

from notron import cli

#: Every module a command may reach through its local import.
_MODULE_NAMES = (
    'attachments', 'brain', 'bridge', 'calendar', 'care', 'clarifications', 'conversation',
    'credentials', 'daily', 'eventkit', 'filer', 'graph', 'health', 'index',
    'layout', 'library', 'markup', 'mcp_server', 'mentions', 'migration', 'notedoc', 'notes',
    'operations', 'outbound', 'paths', 'permissions', 'persistence', 'policy',
    'privacy', 'reflect', 'reminders', 'requests', 'research', 'retention',
    'retrieval', 'rewrite', 'securestore', 'undo', 'watch', 'worker', 'workspace',
)


def _modules():
    found = {}
    for name in _MODULE_NAMES:
        try:
            found[name] = importlib.import_module(f'notron.{name}')
        except Exception:  # pragma: no cover - a module may not exist yet
            continue
    return found


def _commands():
    for name in dir(cli):
        if name.startswith('cmd_'):
            command = getattr(cli, name)
            yield name, getattr(command, '__wrapped__', command)


def test_every_cli_call_into_a_module_uses_declared_parameters():
    """A rename on one side of the CLI boundary fails here, not at 6am on
    somebody's real notes library with a TypeError mid-run."""
    modules = _modules()
    problems = []
    for name, function in _commands():
        try:
            source = textwrap.dedent(inspect.getsource(function))
            tree = ast.parse(source)
        except (OSError, TypeError, SyntaxError):  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if not node.keywords:
                continue
            base = node.func.value
            if not isinstance(base, ast.Name):
                continue
            module = modules.get(base.id)
            target = getattr(module, node.func.attr, None)
            if not callable(target):
                continue
            try:
                parameters = inspect.signature(target).parameters
            except (TypeError, ValueError):  # pragma: no cover
                continue
            # A **kwargs target accepts anything, so there is nothing to check.
            if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
                continue
            passed = {keyword.arg for keyword in node.keywords if keyword.arg}
            unexpected = passed - set(parameters)
            if unexpected:
                problems.append(
                    f'{name} -> {base.id}.{node.func.attr}() rejects '
                    f'{sorted(unexpected)}; it accepts {sorted(parameters)}')
    assert not problems, '\n'.join(problems)


def test_the_check_would_actually_catch_the_bug_it_exists_for(monkeypatch):
    """A guard nobody has seen fire is a guard nobody should trust. This injects
    the original defect into a copy of the CLI source and asserts the same
    analysis rejects it."""
    modules = _modules()
    defective = textwrap.dedent(inspect.getsource(cli.cmd_index.__wrapped__)).replace(
        'extract=args.attachments', 'read_attachments=args.attachments')
    tree = ast.parse(defective)

    unexpected = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            target = getattr(modules.get('index'), node.func.attr, None)
            if callable(target) and node.keywords:
                parameters = set(inspect.signature(target).parameters)
                unexpected |= {k.arg for k in node.keywords if k.arg} - parameters
    assert unexpected == {'read_attachments'}


def test_the_index_command_wires_its_flags_onto_the_build_signature(monkeypatch):
    """`--rebuild` must reach `force`, and `--attachments` must reach `extract`.
    The fake mirrors the real signature on purpose: a name the real function does
    not have raises here exactly as production did."""
    from notron import index

    seen = {}

    def fake_build(brain, *, on_progress=None, force=False, extract=False):
        seen.update(force=force, extract=extract)
        return {'notes': 0, 'embedded': 0, 'reused': 0}

    monkeypatch.setattr(index, 'build', fake_build)
    monkeypatch.setattr(cli, '_brain', lambda: object())
    cli.cmd_index.__wrapped__(SimpleNamespace(rebuild=True, attachments=True))
    assert seen == {'force': True, 'extract': True}


def test_the_fake_still_matches_the_real_signature():
    """If `index.build` is refactored, the test above must be updated with it
    rather than quietly drifting out of date."""
    from notron import index

    def fake_build(brain, *, on_progress=None, force=False, extract=False):
        return {}

    assert (list(inspect.signature(fake_build).parameters)
            == list(inspect.signature(index.build).parameters))


def test_a_one_shot_command_delivers_its_own_log_receipt(monkeypatch):
    """Review 2026-09-23: writes only queue receipts now, and with no listener
    running nothing would deliver a CLI command's receipt."""
    from notron import cli
    delivered = []
    monkeypatch.setattr(cli, '_deliver_receipts', lambda: delivered.append(1))
    monkeypatch.setattr(cli, 'cmd_ask', lambda args: None)
    for argv in (['ask', 'hello'], ['ask', 'hello', '--dry-run']):
        try:
            cli.main(argv)
        except SystemExit:
            pass
    assert delivered == [1]                 # the real run, not the dry run


def test_mcp_serve_wires_its_flags_and_the_cli_brain(monkeypatch, capsys):
    """`--no-ask` must reach `ask`, `--writes` must reach `writes`, and the brain
    stays lazy: `_brain` is handed over, never called, so reads need no key."""
    from notron import mcp_server
    seen = {}

    class App:
        def run(self):
            seen['ran'] = True

    def fake_build(*, writes, ask, brain_factory, after_writes=None):
        seen.update(writes=writes, ask=ask, brain_factory=brain_factory, after_writes=after_writes)
        return App()
    monkeypatch.setattr(mcp_server, 'build', fake_build)
    cli.main(['mcp', 'serve', '--writes', '--no-ask'])
    assert seen == {'writes': True, 'ask': False, 'brain_factory': cli._brain,
                    'after_writes': cli._deliver_receipts, 'ran': True}
    assert capsys.readouterr().out == '', 'stdout is the MCP wire'


def test_mcp_serve_without_the_sdk_says_how_to_install_it(monkeypatch, capsys):
    from notron import mcp_server
    import pytest

    def missing(**kw):
        raise mcp_server.SDKMissing(mcp_server.INSTALL)
    monkeypatch.setattr(mcp_server, 'build', missing)
    with pytest.raises(SystemExit) as stop:
        cli.main(['mcp', 'serve'])
    assert stop.value.code != 0
    out = capsys.readouterr()
    assert "pip install 'notron[mcp]'" in out.err and out.out == ''


def test_mcp_sdk_missing_is_reported_by_build_itself(monkeypatch):
    """The lazy import is the only thing between a bare install and a traceback."""
    import builtins
    import pytest
    from notron import mcp_server
    real = builtins.__import__

    def no_mcp(name, *a, **kw):
        if name == 'mcp' or name.startswith('mcp.'):
            raise ImportError(name)
        return real(name, *a, **kw)
    monkeypatch.setattr(builtins, '__import__', no_mcp)
    with pytest.raises(mcp_server.SDKMissing):
        mcp_server.build(writes=False, ask=True, brain_factory=lambda: None)


def test_mcp_config_prints_a_ready_to_paste_block(capsys):
    import json
    import sys
    cli.main(['mcp', 'config'])
    block = json.loads(capsys.readouterr().out)
    assert block['mcpServers']['notron'] == {'command': sys.executable,
                                             'args': ['-m', 'notron', 'mcp', 'serve']}
