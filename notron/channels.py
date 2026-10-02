"""Project channels — a note per project that is addressed to her, line by line.

A channel is an ordinary note in her folder, named `Notron <Project>`, bound to
one of the user's projects: a local repository and, optionally, its GitHub
repository. Every new line in it is a request. No tag is needed, and that is the
point: "Hey Siri, add *is CI green* to my Notron Synqology note" works from a
phone, a watch or a car, because Siri can already append a line to a named note
and iCloud carries it to the Mac. Measured 2026-09-22 on the developer's iPhone:
the line arrived as a plain `<div>` under the title, in the right note, within
seconds. There is no App Intent and no relay server in that path.

What a channel may touch is decided here, in plain code, never by the note's
text. Anyone who can dictate a line can write to the note, and the model reads
that line — so the repository path, the GitHub slug and the allowed tool classes
live in this registry, set from the terminal with `notron channel add`, and the
note carries nothing that grants anything.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from . import paths

#: What a channel may do. `read` is the local repository and GitHub, read-only;
#: `research` is a web search; `run` lets Nemotron hand an approved brief to a
#: coding agent in a throwaway copy of the repository (`handoff.py`). `run`
#: never skips the approval, and it needs a named agent. Without a repository
#: the channel is a *workspace*: the agent writes drafts, plans and CSVs into an
#: empty folder of its own, never into anything of the user's.
ALLOW = ("read", "research", "run")

#: The coding agents a channel may hand work to. Bring-your-own: the user's own
#: install and login, disclosed, never the one making a decision.
HANDS = ("claude", "codex")

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}$")
GITHUB = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")

#: The standing line under the title. It starts with the same words as the Ask
#: note's, which `conversation` already treats as furniture, so it is never read
#: as something the user said.
HELP = ("Type anything below this line — or tell Siri “add … to my {title} note” — "
        "and Notron answers underneath, using {project} only.")


class ChannelError(ValueError):
    pass


@dataclass(frozen=True)
class Channel:
    name: str
    note_id: str
    repo: str = ""
    github: str = ""
    allow: tuple[str, ...] = ("read",)
    hand: str = ""
    #: The project's own test command, as argv, set by the user at the terminal.
    #: After a hand-off, Notron runs it in the fence (network off) and shows the
    #: result to Nemotron's review. Never from a note, never from a model.
    test: tuple[str, ...] = ()
    #: MCP servers (`connectors.py`) whose approved tools Nemotron may pick
    #: here, by their registered name. Set at the terminal, never from a note.
    connectors: tuple[str, ...] = ()

    @property
    def title(self) -> str:
        return title_for(self.name)

    @property
    def workspace(self) -> bool:
        """No repository: work here is documents, not code."""
        return not self.repo


def title_for(name: str) -> str:
    return f"Notron {name}"


def _path() -> Path:
    # Resolved at call time, not import, so NOTRON_DATA_DIR always wins.
    return paths.data_dir() / "channels.json"


def _validate(ch: Channel) -> Channel:
    if not NAME.match(ch.name):
        raise ChannelError("A channel name is letters, numbers, spaces, dots and dashes, up to 40.")
    if not ch.note_id:
        raise ChannelError("A channel needs its note.")
    if ch.repo:
        repo = Path(ch.repo)
        if not repo.is_absolute():
            raise ChannelError("The repository path must be absolute.")
    if ch.github and not GITHUB.match(ch.github):
        raise ChannelError("GitHub repository must look like owner/name.")
    if not ch.allow or any(a not in ALLOW for a in ch.allow):
        raise ChannelError(f"Allowed tools are {', '.join(ALLOW)}.")
    if ch.hand and ch.hand not in HANDS:
        raise ChannelError(f"The coding agent is one of {', '.join(HANDS)}.")
    if "run" in ch.allow and not ch.hand:
        raise ChannelError("`run` needs an agent: --hand claude|codex.")
    if ch.test and (not ch.repo or not all(isinstance(a, str) and a for a in ch.test)):
        raise ChannelError("A test command needs a repository, e.g. --test \".venv/bin/python -m pytest -q\".")
    # Only the shape here, not membership in connectors.json: `load` runs this
    # on every channel, and a damaged connector registry must not stop her
    # answering in every channel. Names are checked against the registry when
    # a grant is added (`_registered`), and a name that later leaves the
    # registry is inert: `connectors` only ever grants servers it still holds.
    if not isinstance(ch.connectors, tuple) or not all(isinstance(c, str) and c for c in ch.connectors):
        raise ChannelError("Connectors are named by their registered names.")
    if ch.connectors and "read" not in ch.allow:
        raise ChannelError("Connectors are reads in v1: the channel needs --allow read.")
    return ch


def _registered(names) -> tuple[str, ...]:
    """Each name as the registry spells it, or an error naming the first unknown.

    The registered casing is stored so the grant reads the same in `channel
    list` as in `connect list`. A damaged registry is an error here, on the
    write path, where the user is at the terminal to fix it.
    """
    from . import connectors
    if not names:
        return ()
    try:
        known = {s.name.lower(): s.name for s in connectors.load()}
    except connectors.ConnectorError as problem:
        raise ChannelError(str(problem)) from None
    out = []
    for n in names:
        name = known.get(str(n).strip().lower())
        if name is None:
            raise ChannelError(f"No connector called {str(n).strip()}. Try: notron connect add {str(n).strip()} -- <command>")
        out.append(name)
    return tuple(dict.fromkeys(out))


def load() -> list[Channel]:
    """Every registered channel. A damaged file is an error, never an empty list:
    an empty list would read as "no channels" and quietly stop her answering."""
    path = _path()
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise ChannelError("The channel registry is unreadable; fix or remove it.") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(raw.get("channels"), list):
        raise ChannelError("The channel registry is unreadable; fix or remove it.")
    out = []
    for row in raw["channels"]:
        # A bare string would become one grant per letter through `tuple()`.
        if not isinstance(row, dict) or not isinstance(row.get("connectors", []), list):
            raise ChannelError("The channel registry is unreadable; fix or remove it.")
        try:
            out.append(_validate(Channel(name=row["name"], note_id=row["note_id"],
                                         repo=row.get("repo", ""), github=row.get("github", ""),
                                         allow=tuple(row.get("allow", ("read",))),
                                         hand=row.get("hand", ""),
                                         test=tuple(row.get("test") or ()),
                                         connectors=tuple(row.get("connectors", ())))))
        except (KeyError, TypeError) as exc:
            raise ChannelError("The channel registry is unreadable; fix or remove it.") from exc
    return out


def _save(channels: list[Channel]) -> None:
    from .persistence import atomic_write_json
    from .securestore import private_directory
    path = _path()
    private_directory(path.parent)
    atomic_write_json(path, {"version": 1, "channels": [
        {**asdict(c), "allow": list(c.allow), "test": list(c.test),
         "connectors": list(c.connectors)} for c in channels]})


def for_note(note_id: str | None) -> Channel | None:
    if not note_id:
        return None
    return next((c for c in load() if c.note_id == note_id), None)


def add(name: str, *, repo: str = "", github: str = "", allow: tuple[str, ...] = ("read",),
        hand: str = "", test: tuple[str, ...] = (),
        connectors: tuple[str, ...] = ()) -> tuple[Channel, str]:
    """Create (or adopt) `Notron <name>` in her folder and register it.

    Adopts an existing note of that exact title in her folder rather than making
    a second one — Siri addresses notes by name, and two notes with one name is
    exactly the ambiguity that sends a dictated line to the wrong place.
    """
    from . import library, markup, notes, policy, workspace
    name = name.strip()
    if repo:
        repo = str(Path(repo).expanduser().resolve())
        if not Path(repo).is_dir():
            raise ChannelError(f"No folder at {repo}.")
    existing = load()
    if any(c.name.lower() == name.lower() for c in existing):
        raise ChannelError(f"There is already a channel called {name}.")
    connectors = _registered(connectors)
    _validate(Channel(name, "pending", repo, github, tuple(allow), hand, tuple(test), connectors))
    lib = library.load()
    if not lib.configured:
        raise policy.PolicyError("Set up note permissions (notron library) before adding a channel.")
    title = title_for(name)
    notes.ensure_folder(workspace.FOLDER)
    found = notes.find_note(workspace.FOLDER, title)
    if found:
        note_id, state = found.id, "adopted"
    else:
        project = f"**{github or Path(repo).name or name}**"
        note_id = notes.create_note(workspace.FOLDER, markup.render(
            title, HELP.format(title=title, project=project) + "\n\n———\n"))
        state = "created"
    channel = _validate(Channel(name, note_id, repo, github, tuple(allow), hand, tuple(test), connectors))
    # The grant first, then the registry. If the grant cannot be saved (secure
    # storage locked, say) the registry never names a note she may not read —
    # the first live run did it the other way round and left exactly that.
    lib.channels.add(note_id)
    library.save(lib)
    _save([*existing, channel])
    return channel, state


def remove(name: str) -> Channel:
    """Unregister a channel. The note stays — it is the user's record."""
    from . import library
    existing = load()
    gone = next((c for c in existing if c.name.lower() == name.strip().lower()), None)
    if gone is None:
        raise ChannelError(f"No channel called {name}.")
    # Revoke first: a crash between the two leaves a registry entry with no
    # grant, which is inert, never a grant with no way to remove it.
    lib = library.load()
    lib.channels.discard(gone.note_id)
    library.save(lib)
    _save([c for c in existing if c is not gone])
    return gone


def update(name: str, *, github: str | None = None, allow: tuple[str, ...] | None = None,
           hand: str | None = None, test: tuple[str, ...] | None = None,
           connect=(), disconnect=()) -> Channel:
    """Change what an existing channel may use. The note and its grant stay put.

    `connect` adds connector grants and `disconnect` removes them, by name, any
    casing. Only the newly added names are checked against the registry, so a
    server already removed from it can still be disconnected, and an edit that
    adds none never needs connectors.json at all.
    """
    from dataclasses import replace
    existing = load()
    old = next((c for c in existing if c.name.lower() == name.strip().lower()), None)
    if old is None:
        raise ChannelError(f"No channel called {name}.")
    held = {c.lower() for c in old.connectors}
    added = _registered([n for n in connect if str(n).strip().lower() not in held])
    gone = {str(n).strip().lower() for n in disconnect}
    granted = tuple(c for c in (*old.connectors, *added) if c.lower() not in gone)
    new = _validate(replace(old, connectors=granted,
                            **{k: v for k, v in (("github", github), ("hand", hand),
                                                 ("allow", tuple(allow) if allow is not None else None),
                                                 ("test", tuple(test) if test is not None else None))
                               if v is not None}))
    _save([new if c is old else c for c in existing])
    return new
