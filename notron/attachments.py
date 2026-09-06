"""Pictures, recordings and files — the part of a note that is not text.

Apple Notes keeps an attachment completely out of the note's HTML body. A note
holding a voice memo reads back as `<div><br><br></div>` and nothing else, so
every part of Notron that works from a body — the mention sweep, the index, the
Filer — is blind to it by construction. That blindness is not neutral: asked
about a photo she cannot see, she answers as though the photo were not there,
which sounds exactly like having looked. This module is the only place that asks
Notes the second question.

Two rules, both inherited from `notes.py` and both measured again here on
2026-09-05, against the developer's own library:

*Ask with `every`.* `attachments of nt` hands back a set, and asking a set for
`name` raises `-1728: Can't get name of {attachment id …}` — the identical
failure `CLAUDE.md` records for Reminders. `name of every attachment of nt`
returns the list, and one note's worth costs 0.30s.

*Never loop over notes.* Walking a folder note by note asking each for its
attachments costs 6.69s where the bulk form costs 0.18s — 37×. The bulk form
flattens, though: `id of container of attachments of every note of f` comes
back `missing value`, so it can say *whether* a folder holds files but never
*which note* holds them. Survey in bulk, resolve one note at a time.

Nothing here writes. Notes will happily delete an attachment; no code in this
module can.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import notes
from .notes import RS, US

# Notes models an inline table as an attachment with no name and no file. The
# developer's library holds 41 of them against 17 real files, so this is the
# common case, not an edge one.
_NOT_A_FILE = {"missing value", "", "."}

IMAGE = {".png", ".jpg", ".jpeg", ".heic", ".gif", ".tiff", ".webp"}
AUDIO = {".m4a", ".mp3", ".wav", ".aiff", ".caf"}
PDF = {".pdf"}
TEXT = {".txt", ".md", ".csv", ".json", ".log"}

# The two lists are coerced to text item by item rather than with a bare
# `nm as text`: a table's name really is `missing value`, and coercing a list
# holding one raises -1700 and loses the whole answer, including the real files
# alongside it. The loop walks lists that are already in memory — the two Apple
# events have been paid for by then — so it costs nothing.
_ON_NOTE = f"""
on run argv
  tell application "Notes"
    set nt to note id (item 1 of argv)
    set nm to name of every attachment of nt
    set ai to id of every attachment of nt
  end tell
  set safeNames to {{}}
  repeat with i from 1 to (count of nm)
    set v to item i of nm
    if v is missing value then
      set end of safeNames to ""
    else
      set end of safeNames to v as text
    end if
  end repeat
  set safeIds to {{}}
  repeat with i from 1 to (count of ai)
    set v to item i of ai
    if v is missing value then
      set end of safeIds to ""
    else
      set end of safeIds to v as text
    end if
  end repeat
  set text item delimiters to "{US}"
  return (safeNames as text) & "{RS}" & (safeIds as text)
end run
"""


@dataclass(frozen=True)
class Attachment:
    id: str
    name: str
    kind: str      # "image" | "audio" | "pdf" | "text" | "other"

    @property
    def suffix(self) -> str:
        return "." + self.name.rsplit(".", 1)[-1].lower() if "." in self.name else ""


def _kind(name: str) -> str:
    dot = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if dot in IMAGE:
        return "image"
    if dot in AUDIO:
        return "audio"
    if dot in PDF:
        return "pdf"
    if dot in TEXT:
        return "text"
    return "other"


def on_note(note_id: str, modified: str = "") -> list[Attachment]:
    """Every real file hanging off one note. Tables and other bodiless
    attachments are dropped — they are structure, not content.

    `modified` is the note's timestamp, and it matters: the library's "start
    from 2026" cutoff hides a note as completely as an explicit ignore does,
    and an id on its own cannot answer that question. Callers who have the
    `Note` should pass it; callers who do not get the safe reading of an
    unknown date, which is to treat the note as readable.
    """
    from . import library

    if library.state_of(note_id, modified) == library.IGNORE:
        # Invariant 11. Refused before the question is asked, not after the
        # answer comes back — the point is that Notes is never queried at all,
        # because a description of a photo is a read of the photo.
        return []
    raw = notes.run(_ON_NOTE, note_id)
    if RS not in raw:
        return []
    names, ids = (part.split(US) for part in raw.split(RS, 1))
    out = []
    for name, att_id in zip(names, ids):
        if name.strip() in _NOT_A_FILE or not att_id.strip():
            continue
        out.append(Attachment(id=att_id, name=name, kind=_kind(name)))
    return out
