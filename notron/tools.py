"""Read-only project tools a channel request may use.

The model chooses tools by name from this fixed catalogue; it never supplies a
command, a path or an argument. Each tool is a fixed argv run in the channel's
own repository with no shell, a short timeout and a stripped environment, and
its output is untrusted data on the way back — a commit message or an issue
title is text anyone could have written, and it is handed to the writer as
evidence, never as an instruction.

Every git tool ends in `-- .`, so a project that lives inside a bigger
repository (Synqology sits in the developer's whole `~/Dev` monorepo) sees its
own folder's history, not every other project's.

Nothing here writes. There is no `git commit`, `push`, `checkout`, `gh pr
merge` or `gh issue comment` in this file, and a test holds the line.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

#: Characters of one tool's output the model is shown. A busy log or PR list is
#: long; the tail of it is rarely what anyone asked about.
MAX_OUTPUT = 6000
TIMEOUT = 20


@dataclass(frozen=True)
class Tool:
    name: str
    needs: str          # "repo" | "github"
    describe: str
    argv: tuple[str, ...]


CATALOGUE: tuple[Tool, ...] = (
    Tool("git_status", "repo", "current branch, ahead/behind, uncommitted files",
         ("git", "status", "--short", "--branch", "--", ".")),
    Tool("git_log", "repo", "commits from the last 14 days, newest first",
         ("git", "log", "--since=14.days", "-n", "40", "--date=short", "--pretty=format:%h %ad %an: %s", "--", ".")),
    Tool("git_branches", "repo", "local branches by most recent commit",
         ("git", "branch", "--sort=-committerdate", "--format=%(refname:short) · %(committerdate:relative) · %(subject)")),
    Tool("git_diff_stat", "repo", "size of uncommitted changes, per file",
         ("git", "diff", "--stat", "HEAD", "--", ".")),
    Tool("todos", "repo", "TODO / FIXME comments in tracked files",
         ("git", "grep", "-n", "-I", "-E", "TODO|FIXME", "--", ".")),
    Tool("gh_prs", "github", "open pull requests",
         ("gh", "pr", "list", "--limit", "15", "--json", "number,title,author,headRefName,updatedAt,isDraft,reviewDecision")),
    Tool("gh_issues", "github", "open issues",
         ("gh", "issue", "list", "--limit", "15", "--json", "number,title,labels,updatedAt,assignees")),
    Tool("gh_ci", "github", "recent CI / GitHub Actions runs and whether they passed",
         ("gh", "run", "list", "--limit", "8", "--json", "displayTitle,workflowName,headBranch,status,conclusion,createdAt")),
)
BY_NAME = {t.name: t for t in CATALOGUE}


def available(channel) -> list[Tool]:
    """The tools this channel can actually use: what it granted and what it has."""
    if "read" not in channel.allow:
        return []
    have = {"repo": bool(channel.repo), "github": bool(channel.github)}
    return [t for t in CATALOGUE if have[t.needs]]


def menu(channel) -> str:
    return "\n".join(f"- {t.name}: {t.describe}" for t in available(channel))


def _env() -> dict:
    # No ambient secrets: only what git and gh need to find themselves and the
    # user's own gh login. GH_TOKEN/GITHUB_TOKEN are left out on purpose; gh
    # falls back to its keyring login, which the user set up themselves.
    keep = ("PATH", "HOME", "USER", "LANG", "TMPDIR")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update(GIT_PAGER="cat", PAGER="cat", GH_PAGER="cat", NO_COLOR="1",
               GH_PROMPT_DISABLED="1", GIT_TERMINAL_PROMPT="0",
               # `git status` otherwise takes index.lock, and now runs early on
               # every channel line (nodes.PREFETCH) — never block the user's git.
               GIT_OPTIONAL_LOCKS="0")
    return env


def _argv(tool: Tool, channel) -> list[str]:
    argv = list(tool.argv)
    if tool.needs == "github":
        argv += ["-R", channel.github]
    return argv


def _exec(argv: list[str], cwd: str | None) -> tuple[int, str]:
    """The only subprocess call in this module; tests replace it."""
    exe = shutil.which(argv[0], path=_env().get("PATH"))
    if exe is None:
        return 127, f"{argv[0]} is not installed"
    proc = subprocess.run([exe, *argv[1:]], cwd=cwd, env=_env(), capture_output=True,
                          text=True, timeout=TIMEOUT, stdin=subprocess.DEVNULL)
    return proc.returncode, (proc.stdout or proc.stderr or "").strip()


def run(name: str, channel) -> str:
    """One tool's output as text for the model — or a plain line saying why not."""
    tool = BY_NAME.get(name)
    if tool is None or tool not in available(channel):
        return f"{name}: not available in this channel"
    cwd = channel.repo or None
    try:
        code, out = _exec(_argv(tool, channel), cwd)
    except subprocess.TimeoutExpired:
        return f"{name}: timed out after {TIMEOUT}s"
    except OSError as exc:
        return f"{name}: could not run ({type(exc).__name__})"
    if code != 0:
        return f"{name}: failed — {out[:300] or f'exit {code}'}"
    if not out:
        return f"{name}: (nothing)"
    if len(out) > MAX_OUTPUT:
        out = out[:MAX_OUTPUT] + f"\n[… {len(out) - MAX_OUTPUT} more characters not shown]"
    return out
