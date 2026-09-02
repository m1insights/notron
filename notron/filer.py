"""The Filer: sorting a brain dump into the notes it belongs in.

One note — 🧠 Brain Dump — takes anything, one thought per line, with no
decision at the moment of writing. Later, when the dump has gone quiet or when
asked, Notron reads the unfiled lines, works out which of the user's own notes
each one belongs in, copies it there, and ticks the original:

    ✓ magnesium at night → Supplements

Four promises, every one kept in plain code rather than in a prompt:

  * **Nothing is ever deleted.** A filed line stays in the dump, ticked. The
    user clears the dump when they feel like it. (Native Notes checkboxes are
    off the table — scripting strips them — so the tick is text.)
  * **No fitting note means a question, not a guess.** She proposes a new note
    and waits for a `yes` typed under the proposal.
  * **Every filed line carries its receipt**, so undo is a matter of reading.
  * **The model only ever chooses a title.** Copying, ticking and creating go
    through the Executor, behind the Guard, like every other write.

`@notron file this` on a line in any note sends that line through the same
path; the dump is just the place where the tag is implicit on every line.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
from dataclasses import dataclass, field, replace
from datetime import datetime

from . import conversation, index, notedoc, notes, privacy, workspace
from .notedoc import FILED, RECEIPT

STATE = pathlib.Path(__file__).resolve().parents[1] / ".notron" / "filer.json"

#: Where a note she is allowed to create goes. Apple's own default folder.
FILING_FOLDER = os.environ.get("NOTRON_FILING_FOLDER", "Notes")

#: Lines per model call. Nano's JSON gets ragged past this.
MAX_LINES = 40
#: Candidate notes shown to the model, most recently touched first.
MAX_MASTERS = 200
GLIMPSE_CHARS = 100
#: Verdicts remembered, so a line she has already judged costs no second call.
MAX_JUDGED = 500

# "file this: …", "file these", "file: …" — the verb someone puts in front of a
# thought. In the dump the verb is implicit, so only an unmistakable one is
# stripped there: "sort out the garage" is a thought, not an instruction.
VERB = re.compile(r"^\s*(?:please\s+)?(?:file|sort)"
                  r"(?:\s+(?:this|these|that|it|them|away)\b(?:\s+(?:one|line|lines))?\s*[:,\-–—]?"
                  r"|\s*[:\-–—])\s*", re.I)
# Under a tag the verb is certain, so "@notron file magnesium" loses its "file".
VERB_TAGGED = re.compile(r"^\s*(?:please\s+)?(?:file|sort)\b"
                         r"(?:\s+(?:this|these|that|it|them|away))*\s*[:,\-–—]?\s*", re.I)
DUMP_WORDS = re.compile(r"\b(?:brain\s*dump|the\s+dump|my\s+dump)\b", re.I)

YES = {"yes", "y", "yes please", "yep", "yeah", "ok", "okay", "sure", "do it",
       "go", "go ahead", "make it", "create it", "please"}
NO = {"no", "nope", "skip", "no thanks", "don't", "dont", "leave it", "leave them"}

#: The dump note's own standing text — scenery, not something anyone said.
FURNITURE = (workspace.DUMP, "Throw anything in here", conversation.QA_RULE, conversation.RULE)


@dataclass(frozen=True)
class Item:
    """One line to file, and where it lives so it can be ticked afterwards.

    `run` groups adjacent lines: a blank line, a ticked line, furniture or one
    of Notron's turns starts a new run, and the model may only ever fold lines
    together inside one. `parts` are the lines folded under this one — the
    list beneath "the stack I took today:"."""
    text: str          # what gets copied — tag and "file this" verb stripped
    anchor: str        # the line as it reads in the note, to find it again
    near: int          # block index hint
    note_title: str
    folder: str
    run: int = 0
    parts: tuple["Item", ...] = ()

    def digest(self) -> str:
        return hashlib.sha1(re.sub(r"\s+", " ", self.text.strip().lower()).encode()).hexdigest()

    def as_dict(self) -> dict:
        d = {"text": self.text, "anchor": self.anchor, "near": self.near,
             "note_title": self.note_title, "folder": self.folder}
        if self.parts:
            d["parts"] = [p.as_dict() for p in self.parts]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Item":
        return cls(d["text"], d["anchor"], int(d.get("near", 0)), d["note_title"], d["folder"],
                   parts=tuple(cls.from_dict(p) for p in d.get("parts", [])))


@dataclass(frozen=True)
class Master:
    """A note of the user's that lines can be filed into."""
    title: str
    folder: str
    glimpse: str = ""


@dataclass
class Outcome:
    """What one pass actually did — the truth, after the Guard, never the plan."""
    filed: list[tuple[Item, str]] = field(default_factory=list)     # item, master title
    proposed: dict[str, list[Item]] = field(default_factory=dict)   # new title -> items
    created: list[str] = field(default_factory=list)
    declined: list[str] = field(default_factory=list)
    left: list[tuple[Item, str]] = field(default_factory=list)      # item, why
    results: list[str] = field(default_factory=list)                # ✓/✗ lines for the trace
    ticked: set[tuple[str, str]] = field(default_factory=set)       # (folder, title) notes she ticked in
    model_calls: int = 0

    @property
    def quiet(self) -> bool:
        return not (self.filed or self.proposed or self.created or self.declined)

    def summary(self) -> str:
        lines: list[str] = []
        if self.created:
            lines.append("Made " + ", ".join(f"“{t}”" for t in self.created) + ".")
        if self.filed:
            counts: dict[str, int] = {}
            for _, title in self.filed:
                counts[title] = counts.get(title, 0) + 1
            where = ", ".join(f"{t} ({n})" if n > 1 else t for t, n in counts.items())
            n = len(self.filed)
            lines.append(f"Filed {n} line{'s' if n != 1 else ''} → {where}.")
        for title, items in self.proposed.items():
            n = len(items)
            lines.append(f"Nothing you have fits {n} line{'s' if n != 1 else ''} — want a “{title}” note? "
                         f"Say yes in {workspace.DUMP}.")
        if self.declined:
            lines.append("Left alone: " + ", ".join(f"“{t}”" for t in self.declined) + ".")
        if self.left:
            lines.append(f"Left {len(self.left)} line{'s' if len(self.left) != 1 else ''} as they were "
                         f"({self.left[0][1]}).")
        return "\n".join(lines) or "Nothing to file."


# ------------------------------------------------------------- reading lines

def _is_furniture(text: str, furniture: tuple[str, ...]) -> bool:
    stripped = text.strip()
    if not stripped or set(stripped) <= set("—-–_ "):
        return True
    return any(stripped == f or stripped.startswith(f) for f in furniture)


def unfiled(body_html: str, *, note_title: str = workspace.DUMP,
            folder: str = workspace.FOLDER,
            furniture: tuple[str, ...] = FURNITURE) -> list[Item]:
    """Every line in the dump nobody has dealt with yet.

    Skips the note's own header, Notron's turns (a proposal is hers, not a
    thought to file), lines already ticked, and anything that looks like a
    credential — a password dumped here must not be copied anywhere, or shown
    to a model.
    """
    out: list[Item] = []
    in_turn = False
    run = 0
    for ln in notedoc.lines(body_html):
        text = ln.text.strip()
        if ln.after_gap:
            run += 1
        if in_turn:
            if text == conversation.RULE:
                in_turn = False
            run += 1
            continue
        if text.startswith(conversation.SIGNATURE) or text.startswith(f"**{conversation.SIGNATURE}**"):
            in_turn = True
            run += 1
            continue
        if _is_furniture(text, furniture) or text.startswith(FILED) or privacy.contains_secret(text):
            run += 1
            continue
        clean = VERB.sub("", conversation.strip_tag(text)).strip()
        out.append(Item(clean or text, text, ln.block, note_title, folder, run))
    dense: dict[int, int] = {}
    return [replace(it, run=dense.setdefault(it.run, len(dense))) for it in out]


def items_from_turn(source: str, *, title: str, folder: str, near: int) -> tuple[list[Item], list[str]]:
    """What to file out of a turn she was tagged in, and the bare pointer lines
    ("@notron file these") that only said so.

    A tag with words after it — `@notron file this: magnesium` — files that
    line and nothing else; the lines typed around it are context. A bare tag
    files the other lines of the turn. In the Ask note nothing is tagged and
    every line is the request. Bare lines get ticked without a receipt once
    something under them landed, so the turn reads as done.
    """
    tagged, untagged, bare = [], [], []
    for raw in source.split("\n"):
        anchor = raw.strip().removeprefix("• ").strip()
        if not anchor or privacy.contains_secret(anchor):
            continue
        has_tag = bool(conversation.TAG.search(anchor))
        verb = VERB_TAGGED if has_tag else VERB
        text = verb.sub("", conversation.strip_tag(anchor)).strip()
        if not text or DUMP_WORDS.search(text):
            bare.append(anchor)
        elif has_tag:
            tagged.append(Item(text, anchor, near, title, folder))
        else:
            untagged.append(Item(text, anchor, near, title, folder))
    return (tagged or untagged), bare


def mentions_dump(request: str) -> bool:
    return bool(DUMP_WORDS.search(request))


def _said(text: str) -> str:
    return re.sub(r"[^a-z ]", "", text.strip().lower()).strip()


# ----------------------------------------------------------------- masters

def masters(*, exclude: set[str] = frozenset()) -> list[Master]:
    """The notes lines may be filed into: the user's chosen homes, or — before
    any are chosen — every note of theirs she may read, newest first, with a
    glimpse of each from the index when there is one. Notron's own notes are
    never candidates, and neither is anything that holds credentials or reads
    as private."""
    glimpses = index.glimpses(GLIMPSE_CHARS)
    seen: set[str] = set()
    live = []
    from . import library

    lib = library.load()
    for n in library.user_notes(lib):
        if lib.homes and n.id not in lib.homes:
            continue                       # the user said where things go
        if not n.title.strip() or n.title in exclude:
            continue
        if privacy.is_vault(n.title) or privacy.is_private(n.title):
            continue
        if n.title in seen:
            continue
        seen.add(n.title)
        live.append(n)
    live.sort(key=lambda n: n.modified_at or datetime.min, reverse=True)
    return [Master(n.title, n.folder, privacy.redact(glimpses.get(n.id, "")))
            for n in live[:MAX_MASTERS]]


# ------------------------------------------------------------- the model

# Super, not Nano, on purpose. Measured 2026-09-01 on the same two-line prompt:
# Nano took 43s (235s for five lines) and spent 2,000 characters reasoning, then
# copied the glimpse into the title; Super answered in 1.3s with the title exact.
# Filing waits on this call, so the tier that answers in a breath wins.
TIER = "smart"

FILER_SYSTEM = """You file lines from someone's brain dump into their own notes.

You are given the notes they already have — each title in quotes, then a glimpse
of what is inside — and numbered lines they wrote. For each line, choose the one
note it belongs in.

Reply with JSON only:
{"filed": [{"line": 1, "note": "<the title between the quotes, copied exactly>"},
           {"line": 2, "new": "<a short Title Case name for a note that does not exist yet>"}]}

Rules:
- "note" is only ever the title between the quotes — never the glimpse, never a
  title you made up.
- A line belongs in a note when it is about the same subject. A supplement they
  took goes in their supplements note even if that supplement is not in the glimpse;
  a thought about their book goes in the book note.
- Use "new" only when none of their notes is about that subject. A wrong note is
  worse than a question — never force a line into the nearest bucket.
- Lines that belong together get the same "new" title, so one note can hold them.
- Every line appears exactly once. Output nothing but the JSON object."""


def _prompt(items: list[Item], candidates: list[Master]) -> str:
    listing = "\n".join(
        f'- "{m.title}"' + (f" — {m.glimpse}" if m.glimpse else "") for m in candidates
    ) or "(they have no notes yet)"
    numbered = "\n".join(f"{i}. {privacy.redact(it.text)}" for i, it in enumerate(items, start=1))
    return f"# Their notes\n{listing}\n\n# Lines to file\n{numbered}"


def _match(said: str, by_key: dict[str, str]) -> str | None:
    """The existing title the model meant, or None.

    Exact match first, then without quotes, then a title the model padded with
    its glimpse ("Groceries: oat milk" → Groceries) — seen live from Nano. The
    longest title wins so "Book" cannot steal "Book idea"."""
    said = said.strip().strip("\"'“”‘’").strip()
    key = said.casefold()
    if key in by_key:
        return by_key[key]
    best = None
    for k, title in by_key.items():
        rest = key[len(k):]
        padded = key.startswith(k) and rest.startswith((":", " —", " –", " -", " (", '"', "”", "'", "’"))
        if padded and (best is None or len(k) > len(best[0])):
            best = (k, title)
    return best[1] if best else None


def classify(brain, items: list[Item], candidates: list[Master]) -> list[tuple[str, str] | None]:
    """One verdict per item: ("note", existing title), ("new", proposed title),
    or None when the model said nothing usable about that line.

    The model proposes; this validates. A title that is not on the list is
    treated as a proposal, never as a place to write."""
    by_key = {m.title.casefold(): m.title for m in candidates}
    verdicts: list[tuple[str, str] | None] = [None] * len(items)
    for start in range(0, len(items), MAX_LINES):
        batch = items[start:start + MAX_LINES]
        out = brain.ask_json(system=FILER_SYSTEM, user=_prompt(batch, candidates),
                             tier=TIER, max_tokens=min(200 + 40 * len(batch), 1600))
        for row in out.get("filed") or []:
            if not isinstance(row, dict):
                continue
            try:
                n = int(row.get("line"))
            except (TypeError, ValueError):
                continue
            if not 1 <= n <= len(batch):
                continue
            note = str(row.get("note") or "").strip()
            new = str(row.get("new") or "").strip()
            hit = _match(note, by_key) if note else None
            if hit:
                verdicts[start + n - 1] = ("note", hit)
            elif note or new:
                verdicts[start + n - 1] = ("new", _title(new or note))
    return verdicts


def _title(text: str) -> str:
    """A proposed title, tidied: one line, no trailing punctuation, sane length."""
    text = re.sub(r"\s+", " ", text).strip(" .:;,-–—\"'“”")
    return text[:60] or "Unsorted"


# ------------------------------------------------------------------- state

def _state() -> dict:
    try:
        data = json.loads(STATE.read_text())
    except (OSError, json.JSONDecodeError):
        data = {}
    data.setdefault("judged", {})
    data.setdefault("proposals", {})
    return data


def _save(data: dict, *, dry_run: bool) -> None:
    if dry_run:
        return
    judged = data["judged"]
    if len(judged) > MAX_JUDGED:
        for key in list(judged)[: len(judged) - MAX_JUDGED]:
            judged.pop(key, None)
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(data, indent=1))
    except OSError:
        pass


def pending_proposals() -> dict[str, list[Item]]:
    return {t: [Item.from_dict(d) for d in p.get("items", [])]
            for t, p in _state()["proposals"].items()}


def worth_a_pass(body_html: str) -> bool:
    """Plain code, no model: is there anything in the dump she has not judged?

    Runs on every poll of the listener, so it has to be free. Lines she has
    already proposed a note for are waiting on the user, not on her."""
    judged = _state()["judged"]
    for it in unfiled(body_html):
        if _said(it.text) in YES | NO:
            return True
        if it.digest() not in judged:
            return True
    return False


# ------------------------------------------------------------------ passes

def _turn(markdown: str) -> str:
    return f"\n{conversation.turn(markdown)}\n"


def _bullets(items: list[Item]) -> str:
    stamp = f"{datetime.now():%-d %b}"
    return "\n" + "\n".join(f"- {stamp} — {it.text}" for it in items) + "\n"


def _tick(ex, by_note: dict[tuple[str, str], list[tuple[str, int, str]]], out: Outcome) -> None:
    """Tick lines note by note. A note that moved under her is skipped and the
    lines stay unticked — next pass sees them unfiled and the judged cache
    means no second model call."""
    for (folder, title), marks in by_note.items():
        r = ex.mark(title, marks, folder=folder)
        out.results.append(f"{'✓' if r.ok else '✗'} {title} — {r.reason}")
        if r.ok:
            out.ticked.add((folder, title))


def _file(ex, brain, items: list[Item], state: dict, out: Outcome, *,
          extra_marks: dict[tuple[str, str], list[tuple[str, int, str]]] | None = None,
          on_step=None) -> None:
    """The shared core: judge, copy, tick, propose."""
    say = on_step or (lambda m: None)
    judged: dict = state["judged"]
    proposals: dict = state["proposals"]
    pending_titles = {t.casefold(): t for t in proposals}

    candidates = masters(exclude={workspace.DUMP})
    known = {m.title: m for m in candidates}

    # 1. Verdicts — from memory where she already judged this exact line.
    verdicts: dict[int, tuple[str, str]] = {}
    ask: list[int] = []
    for i, it in enumerate(items):
        prior = judged.get(it.digest())
        if prior and prior.get("kind") == "declined":
            out.left.append((it, "you said no to a note for it"))
        elif prior and prior.get("kind") == "note" and prior.get("title") in known:
            verdicts[i] = ("note", prior["title"])
        elif prior and prior.get("kind") == "new" and prior.get("title", "").casefold() in pending_titles:
            verdicts[i] = ("new", pending_titles[prior["title"].casefold()])
        else:
            ask.append(i)

    if ask:
        say(f"judging {len(ask)} line(s) against {len(candidates)} notes")
        try:
            fresh = classify(brain, [items[i] for i in ask], candidates)
            out.model_calls += 1
        except Exception as e:
            say(f"could not judge them ({type(e).__name__}) — leaving them for next time")
            fresh = [None] * len(ask)
        for i, v in zip(ask, fresh):
            if v is None:
                out.left.append((items[i], "I couldn't decide where it goes"))
            else:
                verdicts[i] = v

    # 2. Copy into existing notes, then tick — only what actually landed.
    by_master: dict[str, list[int]] = {}
    for i, (kind, title) in verdicts.items():
        if kind == "note":
            by_master.setdefault(title, []).append(i)
    marks: dict[tuple[str, str], list[tuple[str, int, str]]] = {
        k: list(v) for k, v in (extra_marks or {}).items()}
    for title, idxs in by_master.items():
        master = known[title]
        group = [items[i] for i in idxs]
        r = ex.append(title, _bullets(group), folder=master.folder)
        out.results.append(f"{'✓' if r.ok else '✗'} {title} — {r.reason}")
        if not r.ok:
            for it in group:
                out.left.append((it, r.reason))
            continue
        for it in group:
            out.filed.append((it, title))
            judged[it.digest()] = {"kind": "note", "title": title}
            marks.setdefault((it.folder, it.note_title), []).append((it.anchor, it.near, f"{RECEIPT}{title}"))

    # 3. Propose new notes — once per title. Later lines for the same title
    #    join the pending proposal quietly rather than asking again.
    new_here: dict[str, list[Item]] = {}
    for i, (kind, title) in verdicts.items():
        if kind != "new":
            continue
        canonical = pending_titles.get(title.casefold(), title)
        it = items[i]
        judged[it.digest()] = {"kind": "new", "title": canonical}
        record = proposals.setdefault(canonical, {"asked": None, "items": []})
        if it.as_dict() not in record["items"]:
            record["items"].append(it.as_dict())
        if record["asked"] is None:
            new_here.setdefault(canonical, []).append(it)
    if new_here:
        out.proposed.update(new_here)
        r = ex.append(workspace.DUMP, _turn(_proposal_text(new_here)))
        out.results.append(f"{'✓' if r.ok else '✗'} {workspace.DUMP} — {r.reason}")
        if r.ok:
            for title in new_here:
                proposals[title]["asked"] = datetime.now().isoformat(timespec="minutes")

    if not out.filed:
        marks = {}      # a bare "file this" line only reads as done once something landed
    if marks:
        _tick(ex, marks, out)


def _proposal_text(new: dict[str, list[Item]]) -> str:
    parts = []
    for title, items in new.items():
        shown = "; ".join(it.text[:60] for it in items[:3])
        more = f" (+{len(items) - 3} more)" if len(items) > 3 else ""
        n = len(items)
        parts.append(f"Nothing you have fits {'this' if n == 1 else f'these {n}'}: {shown}{more}. "
                     f"Want a new note called “{title}”?")
    parts.append("Type **yes** under this and I'll make it and file them. Type **no** and I'll leave them alone.")
    return "\n\n".join(parts)


def _approve(ex, items: list[Item], state: dict, out: Outcome, *, on_step=None) -> list[Item]:
    """Handle yes/no lines typed under a proposal. Returns the items that were
    not answers — the actual thoughts still to file."""
    say = on_step or (lambda m: None)
    proposals: dict = state["proposals"]
    judged: dict = state["judged"]
    rest: list[Item] = []
    for it in items:
        said = _said(it.text)
        if said in YES:
            word, named = "yes", ""
        elif said in NO:
            word, named = "no", ""
        elif said.startswith("yes "):
            word, named = "yes", said[4:].strip()
        elif said.startswith("no "):
            word, named = "no", said[3:].strip()
        else:
            rest.append(it)
            continue
        if not proposals:
            out.left.append((it, "nothing was waiting on a yes"))
            continue
        chosen = [t for t in proposals if not named or t.casefold() in named or named in t.casefold()]
        if not chosen:
            chosen = list(proposals)
        receipts: list[str] = []
        for title in chosen:
            record = proposals.pop(title)
            waiting = [Item.from_dict(d) for d in record.get("items", [])]
            if word == "no":
                for w in waiting:
                    judged[w.digest()] = {"kind": "declined", "title": title}
                out.declined.append(title)
                receipts.append(f"left “{title}” alone")
                continue
            say(f"making “{title}” with {len(waiting)} line(s)")
            r = ex.append(title, _bullets(waiting), folder=FILING_FOLDER)
            out.results.append(f"{'✓' if r.ok else '✗'} {title} — {r.reason}")
            if not r.ok:
                proposals[title] = record            # keep the question open
                receipts.append(f"couldn't make “{title}”: {r.reason}")
                continue
            out.created.append(title)
            if r.note_id:
                from . import library
                library.add_home(r.note_id)
            marks: dict[tuple[str, str], list[tuple[str, int, str]]] = {}
            for w in waiting:
                out.filed.append((w, title))
                judged[w.digest()] = {"kind": "note", "title": title}
                marks.setdefault((w.folder, w.note_title), []).append((w.anchor, w.near, f"{RECEIPT}{title}"))
            _tick(ex, marks, out)
            receipts.append(f"made “{title}”, {len(waiting)} filed")
        _tick(ex, {(it.folder, it.note_title): [(it.anchor, it.near, f"{RECEIPT}{'; '.join(receipts)}")]}, out)
    return rest


def run(brain, *, dry_run: bool = False, on_step=None) -> Outcome:
    """File the whole dump: answers to proposals first, then every unfiled line."""
    from .executor import Executor

    say = on_step or (lambda m: None)
    out = Outcome()
    dump = notes.find_note(workspace.FOLDER, workspace.DUMP)
    if not dump:
        say(f"no {workspace.DUMP} note — run `notron setup`")
        out.results.append(f"✗ {workspace.DUMP} — missing; run notron setup")
        return out
    items = unfiled(notes.read_body(dump.id))
    if not items:
        say("nothing unfiled")
        return out

    ex = Executor(dry_run=dry_run)
    state = _state()
    items = _approve(ex, items, state, out, on_step=say)
    if items:
        _file(ex, brain, items, state, out, on_step=say)
    _save(state, dry_run=dry_run)
    return out


def file_items(brain, items: list[Item], *, bare: list[str] = (), dry_run: bool = False,
               on_step=None) -> Outcome:
    """File lines tagged in some other note. A bare "@notron file this" line
    above them is ticked too, without a receipt, once anything under it landed."""
    from .executor import Executor

    out = Outcome()
    if not items:
        return out
    ex = Executor(dry_run=dry_run)
    state = _state()
    extra = {}
    if bare:
        first = items[0]
        extra[(first.folder, first.note_title)] = [(b, first.near, "") for b in bare]
    _file(ex, brain, items, state, out, extra_marks=extra, on_step=on_step)
    _save(state, dry_run=dry_run)
    return out
