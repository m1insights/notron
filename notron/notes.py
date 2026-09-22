"""Read and write the Apple Notes app.

Apple Notes derives a note's title from the first line of its HTML body, so every
write here leads with the title. Bodies are HTML; see `notron.markup` for the subset
Notes actually renders.

**Two rules make this fast enough to poll, and both were learned the hard way.**

*Ask for whole lists, never one note at a time.* Looping over notes in AppleScript
costs an Apple event per property per note. On a real library of 358 notes that is
106 seconds. `name of every note of f` fetches the same thing in one event.

*Address a folder directly, never by walking `every folder`.* Holding a folder as
a loop variable and then asking it for its notes puts Notes back on the slow path —
20 seconds for the same folder that takes 0.2 seconds when addressed by position.
Folders are addressed by index here for a second reason too: Notes happily allows
two folders with the same name, and looking one up by name silently returns the
first one twice.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from .applescript import AppleScriptError, run

# The first request after Notes has been idle wakes the app, and that wake-up
# can take forty seconds while every later call takes a tenth of one. Nothing is
# wrong; it just has to be waited out once, patiently, rather than timed out and
# retried into a loop.
COLD_START_TIMEOUT = 150

# Folders change about once a month. Enumerating them is the gateway call for
# everything else, so its answer is worth holding on to.
FOLDER_CACHE_SECONDS = 600
_folders_cache: tuple[float, list[str]] | None = None

# Separators that cannot occur in note text.
RS = "\x1e"
US = "\x1f"

_FOLDER_NAMES = f"""
on run argv
  tell application "Notes" to set n to name of every folder
  set text item delimiters to "{US}"
  return n as text
end run
"""

_LIST_BY_INDEX = f"""
on run argv
  set idx to (item 1 of argv) as integer
  tell application "Notes"
    set f to folder idx
    set fname to name of f
    set i to id of every note of f
    set n to name of every note of f
    set m to modification date of every note of f
  end tell
  set text item delimiters to "{US}"
  return fname & "{RS}" & (i as text) & "{RS}" & (n as text) & "{RS}" & (m as text)
end run
"""

_BODY = """
on run argv
  tell application "Notes" to return body of note id (item 1 of argv)
end run
"""

_METADATA = f"""
on run argv
  tell application "Notes"
    if not (exists note id (item 1 of argv)) then return ""
    set n to note id (item 1 of argv)
    -- Bind the container to a variable FIRST. Notes refuses the inline form
    -- `name of container of n` on macOS 26.2 with -1700 / -1728 ("Can't get name
    -- of container of note id ..."), while `set c to container of n` followed by
    -- `name of c` returns the folder correctly. Measured against a real note on
    -- 2026-09-22: the two-step form returned the folder name, the inline form
    -- raised. Do not fold these back into one expression.
    set c to container of n
    return (id of n) & "{RS}" & (name of n) & "{RS}" & (name of c) & "{RS}" & (modification date of n as text)
  end tell
end run
"""

_SET_BODY = """
on run argv
  tell application "Notes" to set body of note id (item 1 of argv) to (item 2 of argv)
  return "ok"
end run
"""

_SHOW = """
on run argv
  tell application "Notes"
    activate
    show note id (item 1 of argv)
  end tell
  return "ok"
end run
"""

# Addressed by index, like every other read, so a write can never land in a
# different folder than the one reads come from — and, crucially, so a name
# that matches nothing cannot quietly `make new folder`. That fallback is how
# an empty second "Notes" folder appeared on 2026-09-03, and a duplicate
# folder shifts the index of every folder below it. Only `ensure_folder`
# creates a folder now, and it says so.
_CREATE_AT_INDEX = """
on run argv
  set idx to (item 1 of argv) as integer
  set theBody to item 2 of argv
  tell application "Notes"
    set n to make new note at folder idx with properties {body:theBody}
    return id of n
  end tell
end run
"""

_ENSURE_FOLDER = """
on run argv
  set folderName to item 1 of argv
  tell application "Notes"
    repeat with f in folders
      if name of f is folderName then return "exists"
    end repeat
    make new folder with properties {name:folderName}
    return "created"
  end tell
end run
"""

SKIP_FOLDERS = {"Recently Deleted"}


class FolderMissing(LookupError):
    """No folder by that name. Notron waits; it never invents one."""


@dataclass(frozen=True)
class Note:
    id: str
    title: str
    folder: str
    modified: str

    @property
    def modified_at(self) -> datetime | None:
        for fmt in ("%A, %d %B %Y at %H:%M:%S", "%A, %B %d, %Y at %I:%M:%S %p"):
            try:
                return datetime.strptime(self.modified, fmt)
            except ValueError:
                continue
        return None


def _rows(raw: str) -> list[list[str]]:
    """Parallel lists — ids, names, dates — zipped back into rows."""
    if not raw.strip():
        return []
    columns = [c.split(US) for c in raw.split(RS)]
    if len(columns) < 3 or not columns[0][0]:
        return []
    return [list(row) for row in zip(*columns)]


def warm_up() -> float:
    """Wake the Notes app and return how long it took.

    Worth calling once before anything time-sensitive: it absorbs the cold-start
    delay in a place that can wait, instead of inside a poll that cannot.
    """
    started = time.time()
    run(_FOLDER_NAMES, timeout=COLD_START_TIMEOUT, retries=0)
    return time.time() - started


def folders(*, refresh: bool = False) -> list[str]:
    """Folder names, in the order Notes holds them. Duplicates are possible."""
    global _folders_cache
    if not refresh and _folders_cache and time.time() - _folders_cache[0] < FOLDER_CACHE_SECONDS:
        return _folders_cache[1]
    names = [f for f in run(_FOLDER_NAMES).split(US) if f.strip()]
    if not names:
        # A timed-out or wedged request answers with nothing, and caching that
        # poisons every folder lookup for the next ten minutes. Forget it and
        # ask again next time instead.
        _folders_cache = None
        return []
    _folders_cache = (time.time(), names)
    return names


def folder_at(index: int) -> tuple[str, list[Note]]:
    """One folder's name and all its notes, in a single request."""
    raw = run(_LIST_BY_INDEX, str(index))
    name, _, rest = raw.partition(RS)
    return name, [Note(id=r[0], title=r[1], folder=name, modified=r[2]) for r in _rows(rest)]


def ensure_folder(name: str) -> str:
    """The one place in NOTRON that ever creates a folder."""
    result = run(_ENSURE_FOLDER, name)
    if result == "created":
        # Notes keeps folders in alphabetical order, so a new one shifts the
        # index of every folder below it. Re-ask now rather than address the
        # wrong folder for the rest of the cache window.
        folders(refresh=True)
    return result


def resolve(folder: str) -> tuple[int, list[Note]]:
    """The position of this folder and its notes — proven, not assumed.

    The index comes from a list that may be ten minutes old, and a folder
    created above this one shifts it down without any error being raised: the
    cached index then addresses the folder *next to* the one asked for and
    hands back its notes as if they were these. On 2026-09-03 that made her
    read Recently Deleted as her own folder for two minutes (see
    tests/test_notes.py). The name comes back in the same request as the
    notes, so checking it is free — and if it does not match, the list is
    stale by definition and worth re-asking for once.
    """
    for refresh in (False, True):
        for i, name in enumerate(folders(refresh=refresh), start=1):
            if name != folder:
                continue
            if refresh:
                actual, items = folder_at(i)
            else:
                try:
                    actual, items = folder_at(i)
                except AppleScriptError:
                    # A cached index past the end of a shrunken list. Stale,
                    # not broken — ask again before giving up on the folder.
                    break
            if actual == folder:
                return i, items
            break  # the list is stale — ask Notes again before believing it
    raise FolderMissing(folder)


def folder_exists(folder: str) -> bool:
    """Is there really a folder by this name? Asks Notes again before saying no.

    The honest answer to "I cannot see her folder" is to wait, not to build a
    replacement — so anything that would otherwise recreate a missing note
    checks here first.
    """
    try:
        resolve(folder)
    except FolderMissing:
        return False
    return True


def list_notes(folder: str) -> list[Note]:
    """Notes in the first folder with this name, or [] if there is no such folder."""
    try:
        return resolve(folder)[1]
    except FolderMissing:
        return []


def list_all_notes() -> list[Note]:
    """Every note outside Recently Deleted. Titles and timestamps, no bodies.

    Skipping by the *cached* name meant a stale index could skip the wrong
    folder and sweep Recently Deleted instead — deleted notes read for
    `#notron` tags and fed to the index. The name each folder reports is the
    one that decides now, and a full sweep is heavy enough that one fresh
    folder list costs nothing next to it.
    """
    out: list[Note] = []
    for i in range(1, len(folders(refresh=True)) + 1):
        name, items = folder_at(i)
        if name in SKIP_FOLDERS:
            continue
        out.extend(items)
    return out


def read_body(note_id: str) -> str:
    return run(_BODY, note_id)


def write_body(note_id: str, body: str) -> None:
    run(_SET_BODY, note_id, body, retries=0)


def show_note(note_id: str) -> None:
    """Bring this note up in the Notes app. The escape hatch behind the Mac
    app's preview panel, for when a glance is not enough."""
    run(_SHOW, note_id)


def create_note(folder: str, body: str) -> str:
    """Add a note to an existing folder. Never creates the folder itself."""
    index, _ = resolve(folder)
    return run(_CREATE_AT_INDEX, str(index), body, retries=0)


def find_note(folder: str, title: str) -> Note | None:
    for n in list_notes(folder):
        if n.title == title:
            return n
    return None


def get_note(note_id: str) -> Note | None:
    """Resolve current metadata by exact ID, without reading any other body."""
    raw = run(_METADATA, note_id)
    if not raw:
        return None
    fields = raw.split(RS)
    if len(fields) != 4 or fields[0] != note_id:
        raise AppleScriptError('Invalid note metadata response.')
    note = Note(*fields)
    return None if note.folder in SKIP_FOLDERS else note


def unique_note(folder: str, title: str) -> Note | None:
    """Title lookup is only a capture convenience; ambiguous titles never bind."""
    matches = [n for n in list_notes(folder) if n.title == title]
    return matches[0] if len(matches) == 1 else None
