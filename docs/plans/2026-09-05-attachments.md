# Attachments: pictures, recordings and files Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Notron can see a picture, hear a voice memo, and read a file that someone
dropped into a note — and, until she can, says so instead of answering as though
it were not there.

**Architecture:** One new module, `notron/attachments.py`, owns everything about
non-text content: asking Notes what a note carries, pulling a file out to a cache
directory, and turning it into text. Nothing else in the codebase gains an
`osascript` call. Discovery is metadata only and costs one 0.3s query; extraction
happens only when a question is actually about that note. Whatever comes out of a
file is treated as untrusted text and goes through `privacy.py` on the way in, the
same as any note body. Reading an attachment is a read: no attachment is ever
created, moved, renamed or deleted, and there is no code here that could.

**Tech stack:** AppleScript (`attachments of note`, `save … in POSIX file`), the
Nebius Token Factory vision endpoint (`openbmb/MiniCPM-V-4_5`), macOS `sips` for
downscaling, macOS on-device Speech for audio. All reasoning stays on Nebius;
Nemotron remains the model that thinks.

---

## What was measured first (2026-09-05, on the developer's own library)

Every number below came from a live probe against the real Notes app and the real
Nebius account. **Do not re-derive these; do not design against a guess.**

| Fact | Evidence |
|---|---|
| **An attachment leaves no trace in the note body.** | Note `…/ICNote/p646` ("New Recording") holds a real `recording.m4a`. Its `body` is 39 characters: `<div><br><br></div>\n<div><br><br></div>`. `markup.to_text` returns `''`. Nothing in the current pipeline can know the file is there. |
| **Per-note lookup is cheap: 0.30s.** | `name of every attachment of note id X` + `id of every attachment of …`, one request, on a note with 7 attachments. |
| **The `every` form is mandatory.** | `set atts to attachments of nt` then `name of atts` raises `-1728: Can't get name of {attachment id …}` — the identical failure `CLAUDE.md` records for Reminders. Ask for `name of every attachment of nt`. |
| **A per-note loop over a folder is 37× slower.** | `repeat with nt in (every note of f)` → **6.69s**; `attachments of every note of f` → **0.18s**. The rule in `CLAUDE.md` holds here too. |
| **~~But the bulk form flattens.~~ Corrected 2026-09-05: it does not.** | `name of every attachment of every note of f` returns **one sub-list per note, in note order** — 222 notes in, 222 entries out, 8 of them non-empty — so zipping it against `id of every note of f` names the note after all. `id of container of …` does return `missing value`, which is what the first probe read as flattening. A whole folder's attachment map therefore costs **0.18s**, not 222 × 0.39s = **87s**, measured. The per-note query stays the right call for one note (a tagged note in the sweep); the folder map is the right call for anything that walks the library. |
| **Most attachments are tables, not files.** | Notes models an inline table as an attachment with `name = missing value` and no `contents`. Re-measured 2026-09-05 on the live library: of the 8 notes in `Notes` that carry attachments, **7 carry nothing but tables** and one carries the real `recording.m4a`. Filter on a real name or the Filer will try to transcribe a table. |
| **What is actually in the library today:** | 15 `.png` screenshots, 2 `.txt`, 1 `.m4a`. Re-measured 2026-09-05 after Task 1 landed: the `.png`s and `.txt`s are no longer in the library (the notes holding them are gone), leaving `recording.m4a` in "New Recording" (`…/ICNote/p646`) as the one real file. Stage C therefore has to be tried on a picture dropped in on purpose. |
| **Extraction works and is fast.** | `save a in (POSIX file "/abs/path.png")` wrote a 1,395,644-byte PNG in **0.3s**. |
| **An attachment's properties** | `name`, `id`, `content identifier` (`cid:…@icloud.apple.com`), `contents` (a file reference into the Notes group container), `container` (the note), `modification date`, `shared`. |
| **Nebius can see.** | `openbmb/MiniCPM-V-4_5`: **3.5s**, read a screenshot correctly. `google/gemma-3-27b-it`: **14.1s**, also correct, cleaner output. Sent as an OpenAI-style `image_url` with a `data:image/png;base64,…` URI. |
| **MiniCPM has Nemotron's disease.** | It emits a `<think>` block billed against `max_tokens`. Its 300-token reply was 100% reasoning and 0% answer. `brain.REASONING_HEADROOM` already exists for exactly this — reuse it. |
| **Nebius cannot hear.** | 22 models on the account, none of them speech. There is no Whisper to call. |
| **`sips` ships with macOS.** | `sips -Z 1024 in.png --out small.png` took a 1.4MB screenshot to 282KB. No new dependency. |

### The hackathon rule

All *inference* runs on Nebius and at least one NVIDIA open model is used —
Nemotron, throughout. Vision runs on Nebius too (MiniCPM-V), so images are
uncontroversial. Audio has no Nebius option, so transcription is done by the
operating system, the same way the Notes app turns handwriting into characters:
an input device, not a second inference provider. Every judgement about what a
recording *means* still happens on Nemotron. Say this plainly in the submission —
it is a non-issue stated, and only looks like one if it is hidden. Do **not**
reach for OpenAI's Whisper API or any other cloud; that would be a real breach.

### New invariants

These join the numbered list in `CLAUDE.md` when Stage A lands.

10. **An attachment is only ever read.** Nothing in `attachments.py` creates,
    renames, moves or deletes one. Notes offers `delete`; it is never called.
11. **An ignored note's attachments are never touched** — not listed, not
    extracted, not described. Invariant 9 covers the note; this covers what
    hangs off it.
12. **She never implies she has seen something she has not.** If a note carries
    a file she cannot read yet, the prompt says so in words, and the answer says
    so to the user.

---

## Stage A — She knows they are there

The whole point of Stage A: today she answers a question about a photo as if the
photo does not exist. That is the same failure as the 4,000-character truncation
fixed on 2026-09-05 — sounding like she looked when she did not. This stage is
worth landing on its own even if nothing after it ever ships.

### Task 1: `attachments.py` — ask a note what it carries

**Files:**
- Create: `notron/attachments.py`
- Test: `tests/test_attachments.py`
- Modify: `tests/conftest.py` (teach `FakeNotesApp` the new script)

**Step 1: Write the failing test**

```python
# tests/test_attachments.py
"""What a note carries besides its text."""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from notron import attachments


def test_a_table_is_not_a_file(_notes_is_never_the_real_one):
    """Notes models an inline table as an attachment with no name — 41 of the
    58 attachments in the developer's own library are tables. Transcribing one
    is nonsense, so they never reach the caller."""
    app = _notes_is_never_the_real_one
    app.attachments["Notes/Recipes"] = [
        ("missing value", "att/1"),
        ("pasta.png", "att/2"),
    ]
    found = attachments.on_note("Notes/Recipes")
    assert [a.name for a in found] == ["pasta.png"]


def test_each_file_is_classified_by_what_she_could_do_with_it(_notes_is_never_the_real_one):
    app = _notes_is_never_the_real_one
    app.attachments["Notes/Recipes"] = [
        ("board.png", "att/1"), ("memo.m4a", "att/2"),
        ("spec.pdf", "att/3"), ("log.txt", "att/4"), ("thing.zip", "att/5"),
    ]
    assert [a.kind for a in attachments.on_note("Notes/Recipes")] == [
        "image", "audio", "pdf", "text", "other"]


def test_a_note_with_nothing_attached_costs_one_empty_answer(_notes_is_never_the_real_one):
    assert attachments.on_note("Notes/Parking Garages") == []
```

**Step 2: Teach the fake Notes app the script**

In `tests/conftest.py`, add `self.attachments: dict[str, list[tuple[str, str]]] = {}`
to `FakeNotesApp.__init__`, and this branch to `run`, above the final `raise`:

```python
        if script is attachments._ON_NOTE:
            self.calls.append("attachments")
            rows = self.attachments.get(args[0], [])
            names = notes.US.join(n for n, _ in rows)
            ids = notes.US.join(i for _, i in rows)
            return f"{names}{notes.RS}{ids}"
```

Import `attachments` at the top of `conftest.py` alongside the others. The fake
answering the real script is the whole point of `FakeNotesApp` — a stub here
would not exercise the `every attachment` form that the real app requires.

**Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_attachments.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'notron.attachments'`

**Step 4: Write the implementation**

```python
# notron/attachments.py
"""Pictures, recordings and files — the part of a note that is not text.

Apple Notes keeps an attachment completely out of the note's HTML body. A note
holding a voice memo reads back as `<div><br><br></div>` and nothing else, so
every part of Notron that works from a body — the mention sweep, the index, the
Filer — is blind to it by construction. That blindness is not neutral: asked
about a photo she cannot see, she answers as though the photo were not there,
which sounds exactly like having looked. This module is the only place that asks
Notes the second question.

Two rules, both inherited from `notes.py` and both measured again here:

*Ask with `every`.* `attachments of nt` hands back a set, and asking a set for
`name` raises `-1728` — the same failure Reminders has. `name of every
attachment of nt` returns the list.

*Never loop over notes.* Walking a folder note by note asking each for its
attachments costs 6.7s where the bulk form costs 0.18s. The bulk form flattens,
though — it cannot say which note an attachment came from — so it is only good
for surveying a folder. One note at a time, addressed by id, is 0.30s, and that
is what an answer actually needs.

Nothing here writes. Notes will happily delete an attachment; no code in this
module can.
"""

from __future__ import annotations

from dataclasses import dataclass

from .notes import RS, US, run

# Notes models an inline table as an attachment with no name and no file. The
# developer's library holds 41 of them against 17 real files, so this is the
# common case, not an edge one.
_NOT_A_FILE = {"missing value", "", "."}

IMAGE = {".png", ".jpg", ".jpeg", ".heic", ".gif", ".tiff", ".webp"}
AUDIO = {".m4a", ".mp3", ".wav", ".aiff", ".caf"}
PDF = {".pdf"}
TEXT = {".txt", ".md", ".csv", ".json", ".log"}

_ON_NOTE = f"""
on run argv
  tell application "Notes"
    set nt to note id (item 1 of argv)
    set nm to name of every attachment of nt
    set ai to id of every attachment of nt
  end tell
  set text item delimiters to "{US}"
  return (nm as text) & "{RS}" & (ai as text)
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


def on_note(note_id: str) -> list[Attachment]:
    """Every real file hanging off one note. Tables and other bodiless
    attachments are dropped — they are structure, not content."""
    raw = run(_ON_NOTE, note_id)
    if RS not in raw:
        return []
    names, ids = (part.split(US) for part in raw.split(RS, 1))
    out = []
    for name, att_id in zip(names, ids):
        if name.strip() in _NOT_A_FILE or not att_id.strip():
            continue
        out.append(Attachment(id=att_id, name=name, kind=_kind(name)))
    return out
```

**Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_attachments.py -q`
Expected: 3 passed

**Step 6: Run the whole suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 370 passed

**Step 7: Commit**

```bash
git add notron/attachments.py tests/test_attachments.py tests/conftest.py
git commit -m "feat(attachments): ask a note what it carries besides text"
```

---

### Task 2: An ignored note's attachments are never listed

**Files:**
- Modify: `notron/attachments.py`
- Test: `tests/test_attachments.py`

**Step 1: Write the failing test**

```python
def test_an_ignored_note_is_not_even_asked_what_it_carries(_notes_is_never_the_real_one, monkeypatch):
    """Invariant 9 says an ignored note is never read. A photo inside one is
    still inside it — and a description of a photo is a read of the photo."""
    from notron import library
    app = _notes_is_never_the_real_one
    app.attachments["Notes/Private"] = [("passport.png", "att/9")]
    monkeypatch.setattr(library, "state_of", lambda note_id: library.IGNORE)
    assert attachments.on_note("Notes/Private") == []
    assert "attachments" not in app.calls      # not filtered afterwards — never asked
```

**Step 2: Run it, watch it fail.** Expected: the call is made and the passport comes back.

**Step 3: Implement.** Add a `library.state_of(note_id) -> str` helper if one does not
exist (read `library.Library().state_of`; `library.py:57` already has
`is_ignored(note)` taking a `Note`, so the id-only wrapper is the new part), and
guard the top of `on_note`:

```python
    from . import library
    if library.state_of(note_id) == library.IGNORE:
        # Invariant 11. Refused before the question is asked, not after the
        # answer comes back — the point is that Notes is never queried at all.
        return []
```

**Step 4: Tests pass. Step 5: Commit.**

```bash
git commit -am "feat(attachments): an ignored note's files are never even listed"
```

---

### Task 3: She says what she cannot read

**Files:**
- Modify: `notron/state.py` (one field)
- Modify: `notron/watch.py:sweep_mentions` (fill it)
- Modify: `notron/nodes.py:_prompt` (say it)
- Test: `tests/test_watch.py`, `tests/test_nodes.py`

**Step 1: Write the failing tests**

```python
# tests/test_nodes.py
def test_she_is_told_about_a_file_she_cannot_read_yet():
    from notron import nodes
    from notron.state import State
    s = State(request="what does this say?", here="see attached",
              carried=[("image", "whiteboard.png")])
    p = nodes._prompt(s)
    assert "whiteboard.png" in p
    assert "cannot read" in p.lower()
```

**Step 2: Run it, watch it fail** (`State` has no `carried`).

**Step 3: Implement.**

`notron/state.py`, next to `here`:

```python
    carried: list[tuple[str, str]] = field(default_factory=list)
                                     # (kind, filename) of files hanging off the
                                     # note she was tagged in. Apple Notes keeps
                                     # them out of the body entirely, so without
                                     # this she answers as if they did not exist.
```

`notron/watch.py`, in `sweep_mentions`, beside the `here_text` call:

```python
            carried = [(a.kind, a.name) for a in attachments.on_note(m.note_id)]
```
and pass `carried=carried` through `_answer` into `graph.run`. Thread the same
parameter through `graph.run`'s signature into `State(...)`.

`notron/nodes.py`, in `_prompt`, immediately after the `state.here` block so it
reads as part of the note:

```python
    if state.carried:
        listed = "\n".join(f"- {name} ({kind})" for kind, name in state.carried)
        parts.append(
            "# Files attached to that note that you cannot read yet\n"
            f"{listed}\n"
            "You have NOT seen these. If the request is about one of them, say "
            "plainly that you cannot open it yet — never guess at what it holds "
            "and never answer as though it were not there."
        )
```

**Step 4: Tests pass. Step 5: Run the whole suite. Step 6: Commit.**

```bash
git commit -am "feat(watch): she names the files in a note she cannot read yet"
```

**Step 7: Try it for real.** Tag her in the note holding `recording.m4a`
(`…/ICNote/p646`) and ask "what's in this recording?". Restart the listener first:
`launchctl kickstart -k gui/$(id -u)/io.m1labs.notron.listen`. Expected: she names
`recording.m4a` and says she cannot listen to it yet. **That sentence is Stage A's
whole deliverable.**

---

## Stage B — Files that are already text

Cheapest real capability in the plan: no model, no new provider, no new permission.

### Task 4: Pull a file out of Notes, once, into a cache

**Files:**
- Modify: `notron/attachments.py`
- Test: `tests/test_attachments.py`

`save a in (POSIX file "/abs/path")` is verified at 0.3s for 1.4MB. Cache to
`.notron/attachments/<attachment id sanitised><suffix>` and reuse it when the file
is already there — an attachment is immutable in practice, and a note asked about
twice should not pay twice.

```python
CACHE = pathlib.Path(".notron/attachments")

_EXTRACT = """
on run argv
  tell application "Notes"
    save attachment id (item 1 of argv) in (POSIX file (item 2 of argv))
  end tell
  return "ok"
end run
"""

def fetch(att: Attachment) -> pathlib.Path:
    """The attachment as a real file on disk. Cached; Notes is asked once."""
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / (re.sub(r"[^A-Za-z0-9]+", "-", att.id).strip("-") + att.suffix)
    if not dest.exists():
        run(_EXTRACT, att.id, str(dest.resolve()))
    return dest
```

Test with the fake app answering `_EXTRACT` by writing bytes to `args[1]`, plus a
test that a second `fetch` makes no second call (`app.calls.count("extract") == 1`).

Commit: `feat(attachments): pull a file out of Notes, once`

### Task 5: Read a text file into her context

**Files:** `notron/attachments.py`, `notron/nodes.py:retriever`, tests.

`read_text(att)` for the `text` kind: decode as UTF-8 with `errors="replace"`, cap
at `attachments.MAX_TEXT` (24,000 chars — the same shape as `watch.here_text`, head
and tail with a marker), and run it through `privacy.redact` before returning.
**A dropped-in file is exactly where a `.env` or a key dump arrives**, and the two
`synq-*-diagnostics.txt` files already in the library are the benign version of
that. Redaction is not optional here.

Feed the result into `state.context` as a passage titled `filename (attached to
<note title>)`, so citations name the file.

Commit: `feat(attachments): she can read a text file dropped into a note`

---

## Stage C — Images

### Task 6: `brain.see` — one image, one question, on Nebius

**Files:** `notron/brain.py`, `tests/test_brain.py` (or wherever `Brain` is faked).

```python
VISION_MODEL = "openbmb/MiniCPM-V-4_5"   # 3.5s on a screenshot, measured 2026-09-05.
                                          # google/gemma-3-27b-it is the fallback:
                                          # cleaner output, 14.1s. Override with
                                          # NOTRON_MODEL_VISION.

def see(self, *, image: bytes, mime: str, question: str, max_tokens: int = 1200) -> str:
    """Ask the vision model about one picture.

    MiniCPM reasons in a <think> block billed against max_tokens exactly the way
    Nemotron does — a 300-token budget came back 100% reasoning and 0% answer on
    the first live test. REASONING_HEADROOM is the same fix.
    """
```

Body identical in shape to `ask`: `REASONING_HEADROOM` added to the budget, one
retry at double if the content comes back empty with reasoning present. The message
is the OpenAI multimodal form — a `text` part then an `image_url` part carrying
`data:{mime};base64,{b64}`. Record usage through `self._record` like every other
call so `.notron/usage.json` stays honest.

Commit: `feat(brain): she can look at a picture, on Nebius`

### Task 7: Describe and transcribe an attached image

**Files:** `notron/attachments.py`, `notron/nodes.py`, tests.

- `downscale(path)` shells out to `sips -Z 1024` (macOS built-in; 1.4MB → 282KB
  measured) into the cache. Keeps a base64 payload from dominating the request.
- `describe(att, brain, question)` → `brain.see(...)`, prompt: transcribe any text
  verbatim first, then describe. Cache the result next to the file as `.txt` —
  a picture asked about twice should cost one call, and the cached text is what
  Stage E indexes.
- The result is model-authored text about the user's own picture. Run it through
  `privacy.redact` before it enters the prompt: **a photo of a password is the one
  thing `privacy.py` cannot currently catch**, because it only ever sees text.
  Once the model has read the password out of the picture, it *is* text, and the
  existing scan works — but only if it is actually run.
- `state.carried` entries whose text is now available move out of the "cannot read
  yet" block and into context. The honesty rule in Task 3 must keep applying to
  whatever is left.

Commit: `feat(attachments): she reads the text in a photo and describes it`

**Try it for real:** tag her in "M1 Skincare - Patient Journey" (15 screenshots)
and ask what one of them shows.

---

## Stage D — Voice memos

### Task 8: Spike — can `osascript` reach on-device Speech? (timebox: 90 minutes)

This is the one genuinely unknown piece, so it is a spike with a decision at the
end, not an implementation task. The constraint that shapes it is already in
`CLAUDE.md`: **do not add a compiled Swift helper.** Every rebuild changes an
unsigned binary's identity, macOS re-prompts for permission per binary, and a
background listener can never answer a prompt. `osascript` inherits the terminal's
stable identity, which is why EventKit is reached through JXA.

Try, in order, and stop at the first that works:

1. **JXA + ObjC bridge to `SFSpeechRecognizer`.** Same shape as `eventkit.py`,
   including its trap: the callback is asynchronous and JXA has no `await`, so the
   script must pump `NSRunLoop.runModeBeforeDate` or it exits before the result
   arrives, silently, every time. `eventkit.py` is the working reference.
2. **`shortcuts run` with a "Transcribe Audio" shortcut.** `/usr/bin/shortcuts`
   exists on this machine. Costs the user a one-time manual setup, which is a
   real cost for a product whose whole pitch is that there is nothing to set up.
3. **Neither.** Then Stage A's honest "I can't listen to this yet" is the shipped
   behaviour for audio, and that is an acceptable place to stop.

Write the outcome into this file under a `## Spike result` heading before writing
any implementation. Record which macOS Speech permission is required — it is a
fourth TCC permission with a fourth silent failure, and `notron permissions` must
learn to report it either way.

## Spike result (2026-09-05): path 1 works

**JXA + ObjC bridge to `SFSpeechRecognizer`, on-device, no new permission.**
Measured on this machine, in this order:

| What was tried | What happened |
|---|---|
| `SFSpeechRecognizer.requestAuthorization` under `osascript` | The callback **never fires**. Twenty seconds of pumped run loop, `status_after: null`. `osascript` has no `NSSpeechRecognitionUsageDescription`, so there is no prompt to answer and nothing comes back. A design that waits for a grant here hangs forever. |
| Recognising a file anyway, with `requiresOnDeviceRecognition = true` | **Works.** `isAvailable: true`, `supportsOnDeviceRecognition: true`, and a real result — while `authorizationStatus` is still `0` (notDetermined). The entitlement gates microphone capture and Apple's *server* recogniser; recognising a local file on-device needs neither. |
| A 3.8s clip of known speech | `"Remember to pay the plumber on Friday and buy a magnesium glycinate"` in **0.37s** wall clock, spoken text back verbatim. |
| The real `recording.m4a` in "New Recording" | `No speech detected` — that recording genuinely holds no speech. Not a failure of the path. |

Three things this fixes in the design:

* **Do not ask for authorization, and never gate on its status.** The request
  never returns and the status stays `0` forever while transcription works
  perfectly. `notron permissions` must report what is *available*
  (`isAvailable` + `supportsOnDeviceRecognition`), not the TCC number — the
  numeric read that is right for Calendar and Reminders is actively misleading
  here, which is the opposite of every other permission in this codebase.
* **`requiresOnDeviceRecognition = true` is not optional, it is the hackathon
  rule.** Without it Apple may send the audio to its own servers, which would
  make a second cloud provider out of a checkbox. With it, the audio never
  leaves the machine — the same standing as the Notes app turning handwriting
  into characters.
* **The run loop still has to be pumped.** `recognitionTaskWithRequestResultHandler`
  calls back exactly the way EventKit does, and JXA still has no `await`. This
  is `eventkit.py`'s trap, in a second place.

No Swift helper, no `shortcuts run`, no setup for the user. Path 2 was not needed
and path 3 does not apply.

---

### Task 9: Transcribe a voice memo

Only if the spike found a path. `transcribe(att)` → cached `.txt` beside the file,
same cache shape as Task 7, same `privacy.redact` on the way in — someone reads a
password aloud sooner or later. Extend `notron permissions` with the Speech status
read numerically, never inferred from a query returning nothing.

Commit: `feat(attachments): she listens to a voice memo`

---

## Stage E — Making it searchable

### Task 10: Attachment text joins the index

**Files:** `notron/index.py`, `notron/library.py`, tests.

Once a picture or a recording has become text, it should answer questions asked
anywhere, not only in the note it hangs off. Index each cached transcript as a
chunk titled `<filename> (attached to <note title>)`.

Two gates, both already load-bearing elsewhere:
- `library.user_notes()` remains the only enumeration, so an ignored note's files
  never enter the index (invariant 11).
- `index.search` already re-checks the ignore list at query time because the index
  may be older than the choice. That check must cover attachment chunks too.

Cost control: the folder-wide survey (`attachments of every note of f`, 0.18s) says
which folders hold files at all, so `notron index` skips the per-note query for the
folders that hold none. Nothing is described or transcribed during `index` without
`--attachments`; a first index run should not silently spend a vision call per
screenshot in a library with fifteen of them.

Commit: `feat(index): what she saw and heard is searchable`

---

## Rollout

Land Stage A on its own and live with it for a few days. It converts a confident
wrong answer into an honest one, which is the actual bug; everything after it is
new capability, and new capability on top of a dishonest answer is worth less than
it looks.

Update `CLAUDE.md` when Stage A lands: invariants 10–12, a module row for
`attachments.py`, and the measured numbers table above into the Performance
section — the 6.69s-vs-0.18s and the `-1728` failure belong next to the ones
already there.
