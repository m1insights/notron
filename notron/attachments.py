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
import base64
import hashlib
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass

from . import notes
from .notes import RS, US

#: A third separator, for the one query that answers about a whole folder:
#: note id, its attachment names, its attachment ids.
GS = "\x1d"

#: Where a file pulled out of Notes lives once it is out. An attachment does
#: not change, so this is a cache in the strict sense: asked twice, Notes is
#: asked once. Beside every other piece of Notron's state.
from .paths import DATA_DIR
CACHE = DATA_DIR / 'attachments.json'

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


# The folder-wide form. `name of every attachment of every note of f` does not
# flatten — it returns one sub-list per note, in the same order as `id of every
# note of f` — so zipping the three lists names the note each file hangs off.
# Measured 2026-09-05: 0.18s for a 222-note folder, against 87s of per-note
# calls for the same answer. The folder's own name comes back with it, because
# a cached index can address the folder next to the one asked for (see
# `notes.resolve`) and there is no error when it does.
_IN_FOLDER = f"""
on run argv
  set idx to (item 1 of argv) as integer
  tell application "Notes"
    set f to folder idx
    set fname to name of f
    set nids to id of every note of f
    set nms to name of every attachment of every note of f
    set aids to id of every attachment of every note of f
  end tell
  set rows to {{}}
  repeat with i from 1 to (count of nids)
    set safeNames to {{}}
    set safeIds to {{}}
    repeat with j from 1 to (count of (item i of nms))
      set v to item j of (item i of nms)
      if v is missing value then
        set end of safeNames to ""
      else
        set end of safeNames to v as text
      end if
      set w to item j of (item i of aids)
      if w is missing value then
        set end of safeIds to ""
      else
        set end of safeIds to w as text
      end if
    end repeat
    if (count of safeNames) > 0 then
      set text item delimiters to "{US}"
      set end of rows to ((item i of nids) as text) & "{GS}" & (safeNames as text) & "{GS}" & (safeIds as text)
    end if
  end repeat
  set text item delimiters to "{RS}"
  return fname & "{RS}" & (rows as text)
end run
"""


@dataclass(frozen=True)
class Attachment:
    id: str
    name: str
    kind: str      # "image" | "audio" | "pdf" | "text" | "other"
    note_id: str = ""   # the note it hangs off, so the library rule can be
                        # asked again later — a list made this morning is older
                        # than a choice made this afternoon
    modified: str = ""
    title: str = ""

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
    from . import policy, retention
    retention.require_ready()
    policy.require_ready()

    if library.state_of(note_id, modified) == library.IGNORE:
        # Invariant 11. Refused before the question is asked, not after the
        # answer comes back — the point is that Notes is never queried at all,
        # because a description of a photo is a read of the photo.
        return []
    live = notes.get_note(note_id)
    if live is None or not policy.current().readable(live):
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
                              note_id=note_id, modified=live.modified, title=live.title))
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

    from . import policy, retention
    retention.require_ready()
    policy.require_ready()
    if not att.note_id or not policy.current().readable(notes.Note(att.note_id, att.title, '', att.modified)):
        raise NotAllowed('Attachment source is not readable.')
    live = notes.get_note(att.note_id)
    if live is None or not policy.current().readable(live) or (att.modified and live.modified != att.modified):
        raise NotAllowed('Attachment source changed or is unavailable.')


def _key(att: Attachment) -> str:
    return hashlib.sha256((att.note_id + '\0' + att.id + '\0' + att.modified).encode()).hexdigest()


def _load() -> dict:
    from .securestore import read_json, IntegrityError
    data = read_json(CACHE)
    for row in data.values():
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in ('note_id', 'modified')):
            raise IntegrityError('Attachment cache invalid; processing paused.')
    return data


def purge(live: set[str] | None = None) -> None:
    from . import policy
    from .securestore import write_json
    snap = policy.require_ready()
    data = _load()
    safe = {key: row for key, row in data.items()
            if (live is None or row['note_id'] in live)
            and snap.readable(notes.Note(row['note_id'], row.get('title', ''), '', row['modified']))}
    if safe != data:
        write_json(CACHE, safe)


def _put(att: Attachment, **values) -> None:
    from .securestore import write_json
    _allowed(att)
    data = _load()
    row = data.setdefault(_key(att), {'note_id': att.note_id, 'modified': att.modified, 'title': att.title})
    row.update(values)
    write_json(CACHE, data)


@contextmanager
def _temporary(att: Attachment, data: bytes | None = None):
    """Private disposable path only for platform utilities requiring a file."""
    _allowed(att)
    with tempfile.TemporaryDirectory(prefix='notron-media-') as directory:
        path = pathlib.Path(directory) / ('attachment' + (att.suffix if att.suffix in IMAGE | AUDIO | PDF | TEXT else '.bin'))
        if data is not None:
            path.write_bytes(data)
            path.chmod(0o600)
        yield path


def known_text(att: Attachment) -> str:
    """What she has already worked out this file says — free.

    A picture described while answering one question, or a memo transcribed for
    another, is words on disk from then on. `notron index` reads them without
    asking Notes, a model, or the recogniser for anything.
    """
    _allowed(att)
    return _load().get(_key(att), {}).get('words', '')


def fetch(att: Attachment) -> bytes:
    """Decrypt an original into memory, extracting privately on first use."""
    _allowed(att)
    cached = _load().get(_key(att), {}).get('original')
    if cached is not None:
        return base64.b64decode(cached, validate=True)
    with _temporary(att) as dest:
        notes.run(_EXTRACT, att.id, str(dest.resolve()))
        data = dest.read_bytes()
    _put(att, original=base64.b64encode(data).decode('ascii'))
    return data


def in_folder(folder: str) -> dict[str, list[Attachment]]:
    """Files grouped by approved note; never list an excluded note's files."""
    # Bulk listing would read ignored notes' attachment metadata. List only
    # policy-approved notes, even though this costs more Apple events.
    from . import library
    return {note.id: found for note in library.user_notes()
            if note.folder == folder and (found := on_note(note.id, note.modified))}


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

    raw = fetch(att).decode("utf-8", errors="replace")
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
    if out_png and not small.exists():
        raise RuntimeError('The image could not be converted for vision.')
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

    known = known_text(att)
    if known:
        return known

    from .outbound import Passage
    with _temporary(att, fetch(att)) as path:
        small = downscale(path)
        out = privacy.redact(brain.see(
            image=small.read_bytes(), mime=_mime(small), question=question,
            source=Passage('', 'note', att.note_id, att.title, att.modified)).strip())
    if out:
        _put(att, words=out)
    return out


# ------------------------------------------------------------- recordings

#: Long enough for a long memo, short enough that a wedged recogniser cannot
#: hold the listener. Measured 2026-09-05: 0.37s for a 3.8s clip.
LISTEN_TIMEOUT = 120

#: On-device speech, reached through `osascript` the same way EventKit is.
#:
#: Three things were measured into this script on 2026-09-05, and each of them
#: is the difference between working and silently returning nothing:
#:
#: * **Never ask for authorization.** `SFSpeechRecognizer.requestAuthorization`
#:   under `osascript` never calls back — there is no
#:   `NSSpeechRecognitionUsageDescription` in its bundle, so no prompt appears
#:   and nothing returns. A design that waits for a grant hangs forever.
#:   Recognising a *file* on-device needs no grant: this runs with
#:   `authorizationStatus` sitting at 0 (notDetermined) and works.
#: * **`requiresOnDeviceRecognition` is the hackathon rule, not an optimisation.**
#:   Without it Apple may send the audio to its own servers, which would turn a
#:   checkbox into a second cloud provider. With it the audio never leaves the
#:   machine, which is the same standing as the Notes app turning handwriting
#:   into characters.
#: * **The run loop has to be pumped.** The result arrives in a callback and JXA
#:   has no `await` — `eventkit.py`'s trap, in a second place.
_LISTEN = """
function run(argv) {
var args = JSON.parse(argv[0]);
ObjC.import('Speech');
ObjC.import('Foundation');
function awaitDone(check, seconds) {
  var deadline = $.NSDate.dateWithTimeIntervalSinceNow(seconds);
  while (!check() && $.NSDate.date.compare(deadline) < 0) {
    $.NSRunLoop.currentRunLoop.runModeBeforeDate(
      $.NSDefaultRunLoopMode, $.NSDate.dateWithTimeIntervalSinceNow(0.02));
  }
  return check();
}
var out = {};
var rec = $.SFSpeechRecognizer.alloc.init;
out.available = rec.isAvailable && rec.supportsOnDeviceRecognition;
if (out.available) {
  var req = $.SFSpeechURLRecognitionRequest.alloc.initWithURL(
    $.NSURL.fileURLWithPath(args.path));
  req.requiresOnDeviceRecognition = true;
  var text = null, err = null;
  rec.recognitionTaskWithRequestResultHandler(req, function (result, error) {
    if (error && !error.isNil()) { err = String(error.localizedDescription.js); return; }
    if (result && !result.isNil() && result.isFinal) {
      text = String(result.bestTranscription.formattedString.js);
    }
  });
  awaitDone(function () { return text !== null || err !== null; }, args.seconds);
  out.text = text;
  out.error = err;
}
return JSON.stringify(out);
}
"""


def speech_available() -> bool:
    """Can this Mac transcribe on-device at all?

    Deliberately *not* the TCC number. Every other permission in Notron is read
    numerically because a query that comes back empty is indistinguishable from
    a free week — here the numeric read is the misleading one: it says
    `notDetermined` forever while transcription works perfectly.
    """
    import json

    try:
        raw = subprocess.run(
            ["osascript", "-l", "JavaScript", "-", json.dumps({'path': '', 'seconds': 0})],
            input=_LISTEN,
            capture_output=True, text=True, timeout=20)
        return bool(json.loads(raw.stdout.strip() or "{}").get("available"))
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def _listen(path: pathlib.Path) -> str:
    """One recording, transcribed on this machine. "" when there is no speech."""
    import json

    proc = subprocess.run(["osascript", "-l", "JavaScript", "-",
                           json.dumps({'path': str(path.resolve()), 'seconds': LISTEN_TIMEOUT})], input=_LISTEN,
                          capture_output=True, text=True, timeout=LISTEN_TIMEOUT + 30)
    if proc.returncode != 0:
        raise RuntimeError("The local recognizer failed.")
    out = json.loads(proc.stdout.strip() or "{}")
    if not out.get("available"):
        raise RuntimeError("this Mac cannot transcribe on-device")
    return (out.get("text") or "").strip()


def transcribe(att: Attachment) -> str:
    """What was said in this recording, as words. Cached beside the file.

    Nothing is cached for a recording that yielded no speech. The real
    `recording.m4a` in the developer's own library answers "No speech
    detected", and writing that down as its transcript would both read as
    having listened and heard nothing, and stop her ever trying again.
    """
    from . import privacy

    known = known_text(att)
    if known:
        return known

    with _temporary(att, fetch(att)) as path:
        said = privacy.redact(_listen(path))
    if said:
        _put(att, words=said)
    return said


def as_text(att: Attachment, brain=None) -> str:
    """This file as words, by whatever route its kind allows.

    With no `brain`, a picture stays unread rather than guessed at — the same
    posture `nodes._attached` takes. Anything already worked out comes back
    free either way.
    """
    if att.kind == "text":
        return read_text(att)
    if att.kind == "audio":
        return transcribe(att)
    if att.kind == "image" and brain is not None:
        return describe(att, brain)
    return ""
