"""Read and write the Apple Notes app.

Apple Notes derives a note's title from the first line of its HTML body, so
every write here is expected to lead with the title. Bodies are HTML; see
`juno.markup` for the subset Notes actually renders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .applescript import run

# A record separator that cannot appear in note HTML.
RS = "\x1e"
US = "\x1f"

_LIST = f'''
on run argv
  set folderName to item 1 of argv
  set out to ""
  tell application "Notes"
    set theFolder to missing value
    repeat with f in folders
      if name of f is folderName then
        set theFolder to f
        exit repeat
      end if
    end repeat
    if theFolder is missing value then return ""
    repeat with n in notes of theFolder
      set out to out & (id of n) & "{US}" & (name of n) & "{US}" & ((modification date of n) as string) & "{RS}"
    end repeat
  end tell
  return out
end run
'''

_LIST_ALL = f'''
on run argv
  set out to ""
  tell application "Notes"
    repeat with f in folders
      if name of f is not "Recently Deleted" then
        repeat with n in notes of f
          set out to out & (id of n) & "{US}" & (name of n) & "{US}" & (name of f) & "{US}" & ((modification date of n) as string) & "{RS}"
        end repeat
      end if
    end repeat
  end tell
  return out
end run
'''

_BODY = '''
on run argv
  tell application "Notes" to return body of note id (item 1 of argv)
end run
'''

_SET_BODY = '''
on run argv
  tell application "Notes" to set body of note id (item 1 of argv) to (item 2 of argv)
  return "ok"
end run
'''

_CREATE = '''
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
'''

_ENSURE_FOLDER = '''
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
'''

_FOLDERS = f'''
on run argv
  set out to ""
  tell application "Notes"
    repeat with f in folders
      set out to out & (name of f) & "{RS}"
    end repeat
  end tell
  return out
end run
'''


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


def _split(raw: str) -> list[list[str]]:
    return [r.split(US) for r in raw.split(RS) if r.strip()]


def folders() -> list[str]:
    return [f for f in run(_FOLDERS).split(RS) if f.strip()]


def ensure_folder(name: str) -> str:
    return run(_ENSURE_FOLDER, name)


def list_notes(folder: str) -> list[Note]:
    return [Note(id=r[0], title=r[1], folder=folder, modified=r[2]) for r in _split(run(_LIST, folder))]


def list_all_notes() -> list[Note]:
    """Every note outside Recently Deleted. Titles + timestamps only, no bodies."""
    return [Note(id=r[0], title=r[1], folder=r[2], modified=r[3]) for r in _split(run(_LIST_ALL, timeout=180))]


def read_body(note_id: str) -> str:
    return run(_BODY, note_id)


def write_body(note_id: str, body: str) -> None:
    run(_SET_BODY, note_id, body)


def create_note(folder: str, body: str) -> str:
    return run(_CREATE, folder, body)


def find_note(folder: str, title: str) -> Note | None:
    for n in list_notes(folder):
        if n.title == title:
            return n
    return None
