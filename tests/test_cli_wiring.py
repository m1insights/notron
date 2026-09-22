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
    'attachments', 'brain', 'calendar', 'care', 'clarifications', 'conversation',
    'credentials', 'daily', 'eventkit', 'filer', 'graph', 'health', 'index',
    'layout', 'library', 'markup', 'mentions', 'migration', 'notedoc', 'notes',
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
