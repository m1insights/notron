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

import pathlib
import re
import subprocess
from dataclasses import dataclass

from . import notes
from .notes import RS, US

#: Where a file pulled out of Notes lives once it is out. An attachment does
#: not change, so this is a cache in the strict sense: asked twice, Notes is
#: asked once. Beside every other piece of Notron's state.
CACHE = pathlib.Path(__file__).resolve().parents[1] / ".notron" / "attachments"

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


class NotAllowed(PermissionError):
    """This note is one she may not read. Invariant 11, said out loud."""


@dataclass(frozen=True)
class Attachment:
    id: str
    name: str
    kind: str      # "image" | "audio" | "pdf" | "text" | "other"
    note_id: str = ""   # the note it hangs off, so the library rule can be
                        # asked again later — a list made this morning is older
                        # than a choice made this afternoon
    modified: str = "" 

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
        out.append(Attachment(id=att_id, name=name, kind=_kind(name),
                              note_id=note_id, modified=modified))
    return out


_EXTRACT = """
on run argv
  tell application "Notes"
    save attachment id (item 1 of argv) in (POSIX file (item 2 of argv))
  end tell
  return "ok"
end run
"""


def _allowed(att: Attachment) -> None:
    """Ask the library again, at the moment of use.

    `index.search` re-checks the ignore list at query time because the index
    may be older than the user's choice. An `Attachment` handed around in a
    variable is older than the choice in exactly the same way, and pulling a
    file out of a note she has since been told never to read is worse than
    returning a stale search hit.
    """
    from . import library

    if att.note_id and library.state_of(att.note_id, att.modified) == library.IGNORE:
        raise NotAllowed(f"{att.name} hangs off a note Notron may not read")


def fetch(att: Attachment) -> pathlib.Path:
    """The attachment as a real file on disk. Cached; Notes is asked once."""
    _allowed(att)
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / (re.sub(r"[^A-Za-z0-9]+", "-", att.id).strip("-") + att.suffix)
    if not dest.exists():
        notes.run(_EXTRACT, att.id, str(dest.resolve()))
    return dest


#: How much of a text file the model sees. The same shape as
#: `watch.here_text`: head and tail, with the gap marked, so she can say what
#: she has not read rather than assume it is not there.
MAX_TEXT = 24_000
ELIDED = "\n\n[… the middle of this file is not shown …]\n\n"


def read_text(att: Attachment) -> str:
    """A text file dropped into a note, as the model should see it.

    A file someone drags into a note is exactly where a `.env`, a diagnostics
    dump or an export of a password manager arrives — in a note that holds no
    secrets of its own and would never be flagged. So the same redaction a note
    body gets is applied here, and a file *named* like a credential store gets
    the treatment `privacy.filter_passages` gives a vault note the user asked
    about by name: every value masked, every label kept. Nothing is dropped
    outright, because the user dropped this file in themselves and is asking
    about the note it is in — withholding it silently would be the dishonesty
    invariant 12 exists to prevent.
    """
    from . import privacy

    raw = fetch(att).read_bytes().decode("utf-8", errors="replace")
    if len(raw) > MAX_TEXT:
        half = MAX_TEXT // 2
        raw = raw[:half] + ELIDED + raw[-half:]
    return privacy.redact_vault(raw) if privacy.is_vault(att.name) else privacy.redact(raw)


# ------------------------------------------------------------- pictures

#: The longest edge a picture is sent at. A 1.4MB screenshot came down to
#: 282KB at this size with the text in it still readable, measured 2026-09-05.
#: The payload is base64 inside a JSON request, so the raw file size is the
#: request size plus a third.
MAX_PIXELS = 1024

#: What the vision endpoint will actually accept. Anything else — a HEIC off
#: an iPhone, a TIFF off a scanner — is converted on the way, by the same
#: `sips` that does the downscaling.
SENDABLE = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp"}

DESCRIBE = (
    "Transcribe every word of text in this image, verbatim and in reading "
    "order, before anything else. Then describe what the image shows in two "
    "or three sentences. If it holds no text, say so and describe it. Do not "
    "speculate about anything you cannot see."
)


def downscale(path: pathlib.Path) -> pathlib.Path:
    """The picture at a size worth sending, converted if it has to be.

    `sips` ships with macOS, so this adds no dependency — the same reasoning
    that keeps EventKit behind `osascript` rather than a compiled helper.
    """
    suffix = path.suffix.lower()
    out_png = suffix not in SENDABLE
    small = path.with_name(f"{path.stem}-{MAX_PIXELS}{'.png' if out_png else suffix}")
    if small.exists():
        return small
    cmd = ["sips", "-Z", str(MAX_PIXELS)]
    if out_png:
        cmd += ["-s", "format", "png"]
    cmd += [str(path), "--out", str(small)]
    try:
        subprocess.run(cmd, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        pass
    # A picture that will not convert is still worth sending as it is, unless
    # the endpoint would not take it at all.
    return small if small.exists() else path


def _mime(path: pathlib.Path) -> str:
    return SENDABLE.get(path.suffix.lower(), "image/png")


def describe(att: Attachment, brain, question: str = DESCRIBE) -> str:
    """What is in this picture, as words. Cached beside the file.

    The answer is model-authored text about the user's own picture, and it goes
    through `privacy.redact` before it goes anywhere else. A photo of a
    password is the one secret `privacy.py` cannot catch, because it only ever
    sees text — the moment the model reads it out it *is* text, and the
    existing scan works, but only if it is actually run.
    """
    from . import privacy

    path = fetch(att)
    cached = path.with_name(path.name + ".txt")
    if cached.exists():
        return cached.read_text()

    small = downscale(path)
    out = privacy.redact(brain.see(
        image=small.read_bytes(), mime=_mime(small), question=question).strip())
    if out:
        cached.write_text(out)
    return out
