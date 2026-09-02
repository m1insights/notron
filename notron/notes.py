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

from .applescript import run

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

_CREATE = """
on run argv
  set folderName to item 1 of argv
  set theBody to item 2 of argv
  tell application "Notes"
    set theFolder to missing value
    repeat with f in folders
      if name of f is folderName then
        set theFolder to f
        exit repeat
      end if
    end repeat
    if theFolder is missing value then set theFolder to make new folder with properties {name:folderName}
    set n to make new note at theFolder with properties {body:theBody}
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
    _folders_cache = (time.time(), names)
    return names


def folder_at(index: int) -> tuple[str, list[Note]]:
    """One folder's name and all its notes, in a single request."""
    raw = run(_LIST_BY_INDEX, str(index))
    name, _, rest = raw.partition(RS)
    return name, [Note(id=r[0], title=r[1], folder=name, modified=r[2]) for r in _rows(rest)]


def ensure_folder(name: str) -> str:
    return run(_ENSURE_FOLDER, name)


def list_notes(folder: str) -> list[Note]:
    """Notes in the first folder with this name."""
    for i, name in enumerate(folders(), start=1):
        if name == folder:
            return folder_at(i)[1]
    return []


def list_all_notes() -> list[Note]:
    """Every note outside Recently Deleted. Titles and timestamps, no bodies."""
    out: list[Note] = []
    for i, name in enumerate(folders(), start=1):
        if name in SKIP_FOLDERS:
            continue
        out.extend(folder_at(i)[1])
    return out


def read_body(note_id: str) -> str:
    return run(_BODY, note_id)


def write_body(note_id: str, body: str) -> None:
    run(_SET_BODY, note_id, body)


def show_note(note_id: str) -> None:
    """Bring this note up in the Notes app. The escape hatch behind the Mac
    app's preview panel, for when a glance is not enough."""
    run(_SHOW, note_id)


def create_note(folder: str, body: str) -> str:
    return run(_CREATE, folder, body)


def find_note(folder: str, title: str) -> Note | None:
    for n in list_notes(folder):
        if n.title == title:
            return n
    return None
