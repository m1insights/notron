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

from .credentials import CredentialUnavailable
from .securestore import StorageError
from .policy import PolicyError

import hashlib
import json
import os
import pathlib
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime

from .outbound import Passage, sanitized

from . import conversation, index, layout, markup, notedoc, notes, privacy, workspace
from .notedoc import FILED, RECEIPT

from .paths import DATA_DIR
STATE = DATA_DIR / "filer.json"

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
    note_id: str | None = None
    modified: str = ""
    expected_revision: str | None = None

    def digest(self) -> str:
        return hashlib.sha1(re.sub(r"\s+", " ", self.text.strip().lower()).encode()).hexdigest()

    def as_dict(self) -> dict:
        d = {"text": self.text, "anchor": self.anchor, "near": self.near,
             "note_title": self.note_title, "folder": self.folder,
             "note_id": self.note_id, "modified": self.modified,
             "expected_revision": self.expected_revision}
        if self.parts:
            d["parts"] = [p.as_dict() for p in self.parts]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Item":
        return cls(d["text"], d["anchor"], int(d.get("near", 0)), d["note_title"], d["folder"],
                   parts=tuple(cls.from_dict(p) for p in d.get("parts", [])),
                   note_id=d.get("note_id"), modified=d.get("modified", ""),
                   expected_revision=d.get("expected_revision"))


@dataclass(frozen=True)
class Master:
    """A note of the user's that lines can be filed into."""
    title: str
    folder: str
    glimpse: str = ""
    note_id: str | None = None
    modified: str = ""


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
            lines.append(f"Filed {n} thought{'s' if n != 1 else ''} → {where}.")
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
            folder: str = workspace.FOLDER, note_id: str | None = None, modified: str = "",
            furniture: tuple[str, ...] = FURNITURE) -> list[Item]:
    """Every line in the dump nobody has dealt with yet.

    Skips the note's own header, Notron's turns (a proposal is hers, not a
    thought to file), lines already ticked, and anything that looks like a
    credential — a password dumped here must not be copied anywhere, or shown
    to a model.
    """
    from .executor import revision
    captured = revision(body_html)
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
        out.append(Item(clean or text, text, ln.block, note_title, folder, run, note_id=note_id, modified=modified, expected_revision=captured))
    dense: dict[int, int] = {}
    return [replace(it, run=dense.setdefault(it.run, len(dense))) for it in out]


def items_from_turn(source: str, *, title: str, folder: str, near: int,
                    note_id: str | None = None, modified: str = "") -> tuple[list[Item], list[str]]:
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
            tagged.append(Item(text, anchor, near, title, folder, note_id=note_id, modified=modified))
        else:
            untagged.append(Item(text, anchor, near, title, folder, note_id=note_id, modified=modified))
    return (tagged or untagged), bare


def mentions_dump(request: str) -> bool:
    return bool(DUMP_WORDS.search(request))


def _said(text: str) -> str:
    return re.sub(r"[^a-z ]", "", text.strip().lower()).strip()


# ----------------------------------------------------------------- masters

def masters(*, exclude: set[str] = frozenset()) -> list[Master]:
    """The notes lines may be filed into: only the user's chosen homes, newest first, with a
    glimpse of each from the index when there is one. Notron's own notes are
    never candidates, and neither is anything that holds credentials or reads
    as private."""
    glimpses = index.glimpses(GLIMPSE_CHARS)
    live = []
    from . import library

    lib = library.load()
    for n in library.user_notes(lib):
        if not lib.snapshot().can_file(n.id):
            continue                       # the user said where things go
        if not n.title.strip() or n.title in exclude:
            continue
        if privacy.is_vault(n.title) or privacy.is_private(n.title):
            continue
        live.append(n)
    from collections import Counter
    counts = Counter(n.title.casefold() for n in live)
    live = [n for n in live if counts[n.title.casefold()] == 1]
    live.sort(key=lambda n: n.modified_at or datetime.min, reverse=True)
    return [Master(n.title, n.folder, privacy.redact(glimpses.get(n.id, "")), n.id, n.modified)
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
           {"line": 2, "new": "<a short Title Case name for a note that does not exist yet>"},
           {"line": 3, "part_of": 2}],
 "shapes": {"<title>": "log" or "list", ...}}

Rules:
- "note" is only ever the title between the quotes — never the glimpse, never a
  title you made up.
- A line belongs in a note when it is about the same subject. A supplement they
  took goes in their supplements note even if that supplement is not in the glimpse;
  a thought about their book goes in the book note.
- Use "new" only when none of their notes is about that subject. A wrong note is
  worse than a question — never force a line into the nearest bucket.
- Lines that belong together get the same "new" title, so one note can hold them.
- Some lines are not thoughts of their own but belong under the line above them:
  the list under "the stack I took today:", the steps under a recipe, the items
  under "to pack:". Give each of those {"line": N, "part_of": M} where M is the
  line they hang from, and nothing else — they go wherever line M goes. Only a
  line directly below, in the same group; a blank line always separates thoughts.
- For every title you used, say in "shapes" what kind of note it is: "log" if it
  collects things that happen over time — what they took, ate, did, trained,
  felt, spent — or "list" if it collects things that simply exist — recipes,
  ideas, names, places, things to buy.
- Every line appears exactly once. Output nothing but the JSON object."""


def _prompt(items: list[Item], candidates: list[Master]) -> list[Passage]:
    # Prepare each source before it is shortened/formatted, retaining its ID
    # through the final Brain recheck. A candidate title is private input too.
    sources = sanitized("organize", [
        Passage(m.title + (f" — {m.glimpse}" if m.glimpse else ""),
                "note", m.note_id, m.title, m.modified) for m in candidates])
    listing = [replace(p, text=f'- "{p.text}"') for p in sources]
    numbered = []
    previous_run = None
    for i, it in enumerate(items, start=1):
        p = sanitized("organize", [Passage(it.text, "note", it.note_id,
                                          it.note_title, it.modified)])[0]
        gap = "\n" if previous_run is not None and it.run != previous_run else ""
        numbered.append(replace(p, text=f"{gap}{i}. {p.text}"))
        previous_run = it.run
    return [Passage("# Their notes", "diagnostic"), *listing,
            Passage("# Lines to file", "diagnostic"), *numbered]


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


def classify(brain, items: list[Item], candidates: list[Master]
             ) -> tuple[list[tuple[str, str] | None], dict[str, str]]:
    """One verdict per item — ("note", existing title), ("new", proposed title),
    ("part", index of its lead as a string) or None when the model said nothing
    usable — and the shape the model named for each title it used.

    The model proposes; this validates. A title that is not on the list is
    treated as a proposal, never as a place to write. A part may only hang from
    an earlier line in the same run; a part of a part hangs from the lead."""
    # The model sees sanitized labels. Resolve those locally to the original
    # destination; never mistake a redacted label for a request for a new note.
    labels: dict[str, list[str]] = {}
    for m in candidates:
        labels.setdefault(privacy.redact(m.title).casefold(), []).append(m.title)
    by_key = {key: titles[0] for key, titles in labels.items() if len(titles) == 1}
    ambiguous = {key: key for key, titles in labels.items() if len(titles) > 1}
    verdicts: list[tuple[str, str] | None] = [None] * len(items)
    shapes: dict[str, str] = {}
    for start in range(0, len(items), MAX_LINES):
        batch = items[start:start + MAX_LINES]
        out = brain.ask_json(system=FILER_SYSTEM, user=_prompt(batch, candidates), purpose="organize",
                             tier=TIER, max_tokens=min(240 + 40 * len(batch), 1600))
        parts: dict[int, int] = {}                   # batch index -> batch index it hangs from
        for row in out.get("filed") or []:
            if not isinstance(row, dict):
                continue
            try:
                n = int(row.get("line"))
            except (TypeError, ValueError):
                continue
            if not 1 <= n <= len(batch):
                continue
            if row.get("part_of") is not None:
                try:
                    m = int(row["part_of"])
                except (TypeError, ValueError):
                    continue
                if 1 <= m < n and batch[m - 1].run == batch[n - 1].run:
                    parts[n - 1] = m - 1
                continue
            note = str(row.get("note") or "").strip()
            new = str(row.get("new") or "").strip()
            # A shared redacted name cannot identify a home. In particular,
            # never let its shorter prefix select a different destination.
            if _match(note or new, ambiguous):
                continue
            hit = _match(note, by_key) if note else None
            if hit:
                verdicts[start + n - 1] = ("note", hit)
            elif note or new:
                verdicts[start + n - 1] = ("new", _title(new or note))
        for k, lead in parts.items():
            while lead in parts:            # a part of a part hangs from the lead; every hop
                lead = parts[lead]          # goes strictly backwards, so the walk always lands
            if verdicts[start + lead] is not None:
                verdicts[start + k] = ("part", str(start + lead))
        said_shapes_raw = out.get("shapes")
        if isinstance(said_shapes_raw, dict):
            for title, said in said_shapes_raw.items():
                if isinstance(title, str) and title.strip():
                    named = title.strip()
                    if _match(named, ambiguous):
                        continue
                    # keyed by the master's real title, resolved the same way a
                    # verdict is — the model echoes titles in its own case
                    shapes[_match(named, by_key) or named] = str(said)
    return verdicts, shapes


def _title(text: str) -> str:
    """A proposed title, tidied: one line, no trailing punctuation, sane length."""
    text = re.sub(r"\s+", " ", text).strip(" .:;,-–—\"'“”")
    return text[:60] or "Unsorted"


# ------------------------------------------------------------------- state

def _state() -> dict:
    from .securestore import read_json
    data = read_json(STATE)
    data.setdefault("judged", {})
    data.setdefault("proposals", {})
    data.setdefault("shapes", {})
    return data


def _save(data: dict, *, dry_run: bool) -> None:
    if dry_run:
        return
    judged = data["judged"]
    if len(judged) > MAX_JUDGED:
        for key in list(judged)[: len(judged) - MAX_JUDGED]:
            judged.pop(key, None)
    from .securestore import write_json
    write_json(STATE, data)


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


def _today() -> date:
    return date.today()


def _flatten_parts(items: list[Item], parts_of: dict[int, list[int]], judged: dict) -> dict[int, list[int]]:
    """Collapse a two-level chain onto its root.

    A remembered part (B under C, from a pass that judged them together) and a
    freshly judged one (C under A, because a new line landed above C this
    pass) can chain: `parts_of` then has C as both a lead and someone else's
    part, and C is never anyone's verdict — `_fold` would carry B nowhere,
    filed silently short one line. Every part walks to its ultimate root
    instead, in the order it was found, and `judged` is corrected to point
    a re-chained part straight at that root."""
    lead_of = {p: lead for lead, parts in parts_of.items() for p in parts}
    roots = [i for i in parts_of if i not in lead_of]

    def collect(i: int, into: list[int]) -> None:
        for p in parts_of.get(i, []):
            into.append(p)
            collect(p, into)

    flat: dict[int, list[int]] = {}
    for root in roots:
        collected: list[int] = []
        collect(root, collected)
        flat[root] = collected
        for p in collected:
            digest = items[p].digest()
            if judged.get(digest, {}).get("kind") == "part":
                judged[digest] = {"kind": "part", "of": items[root].digest()}
    return flat


def _fold(items: list[Item], parts_of: dict[int, list[int]]) -> list[Item]:
    """The same list, with each lead carrying its parts. Indices do not move —
    a part stays in the list, verdict-less, so nothing downstream reindexes."""
    return [replace(it, parts=tuple(items[j] for j in parts_of[i])) if i in parts_of else it
            for i, it in enumerate(items)]


def _marks_for(it: Item, receipt: str) -> list[tuple[str, int, str]]:
    """The lead gets the receipt; the lines under it get a bare tick."""
    return [(it.anchor, it.near, receipt)] + [(p.anchor, p.near, "") for p in it.parts]


def _shape_for(title: str, said: dict[str, str], state: dict) -> str:
    """The note's shape: what was decided the first time, else what the model
    just said (and that becomes the decision), else a log."""
    shapes: dict = state["shapes"]
    if title not in shapes:
        shapes[title] = layout.shape(said.get(title))
    return shapes[title]


def _entry_markdown(group: list[Item], *, shape: str, existing: str = '') -> str:
    entries = [(it.text, [p.text for p in it.parts]) for it in group]
    return layout.markdown(entries, shape=shape, existing_text=markup.to_text(existing), day=_today())


def _bind_items(items: list[Item]) -> list[Item]:
    """Bind legacy/direct source items before classification, never during ticking."""
    from .executor import capture_write
    bound = []
    for it in items:
        if it.note_id and it.expected_revision:
            bound.append(it)
            continue
        target = capture_write(it.note_title, folder=it.folder, note_id=it.note_id, mode='mark')
        bound.append(replace(it, note_id=target.note_id, expected_revision=target.expected_revision,
                             parts=tuple(_bind_items(list(it.parts)))))
    return bound


def _source_key(it: Item) -> tuple:
    return (it.note_id, it.folder, it.note_title, it.expected_revision)


def _tick(ex, by_note: dict[tuple, list[tuple[str, int, str]]], out: Outcome) -> None:
    from .state import Write
    for (note_id, folder, title, expected), marks in by_note.items():
        r = ex.apply_write(Write(title=title, folder=folder, note_id=note_id,
                                 expected_revision=expected, mode='mark', marks=marks, markdown=''))
        out.results.append(f"{'✓' if r.ok else '✗'} {title} — {r.reason}")
        if r.ok:
            out.ticked.add((folder, title))


def _file(ex, brain, items: list[Item], state: dict, out: Outcome, *,
          extra_marks: dict[tuple, list[tuple[str, int, str]]] | None = None,
          on_step=None) -> None:
    """The shared core: judge, copy, tick, propose."""
    say = on_step or (lambda m: None)
    judged: dict = state["judged"]
    proposals: dict = state["proposals"]
    pending_titles = {t.casefold(): t for t in proposals}

    from .executor import capture_write
    items = _bind_items(items)
    candidates = masters(exclude={workspace.DUMP})
    known = {m.title: m for m in candidates}
    targets, bodies = {}, {}
    for master in candidates:
        note = notes.get_note(master.note_id) if master.note_id else None
        from . import policy
        if note and policy.current().readable(note):
            body = notes.read_body(note.id)
            targets[master.title] = capture_write(master.title, folder=master.folder,
                                                  note_id=master.note_id, body=body, mode='append')
            bodies[master.title] = body
        else:
            from .state import Write
            targets[master.title] = Write(title=master.title, folder=master.folder, markdown='', mode='append')
    dump_target = capture_write(workspace.DUMP, mode='append')

    # 1. Verdicts — from memory where she already judged this exact line.
    #    A remembered part rejoins its lead if the lead is still here, in the
    #    same run; otherwise it is a line of its own again.
    by_digest = {it.digest(): i for i, it in enumerate(items)}
    parts_of: dict[int, list[int]] = {}
    verdicts: dict[int, tuple[str, str]] = {}
    ask: list[int] = []
    for i, it in enumerate(items):
        prior = judged.get(it.digest())
        if prior and prior.get("kind") == "part":
            lead = by_digest.get(prior.get("of", ""))
            if lead is not None and lead < i and items[lead].run == it.run:
                parts_of.setdefault(lead, []).append(i)
            else:
                ask.append(i)
        elif prior and prior.get("kind") == "declined":
            out.left.append((it, "you said no to a note for it"))
        elif (prior and prior.get("kind") == "note" and prior.get("title") in known
              and prior.get("note_id") == known[prior["title"]].note_id):
            verdicts[i] = ("note", prior["title"])
        elif prior and prior.get("kind") == "new" and prior.get("title", "").casefold() in pending_titles:
            verdicts[i] = ("new", pending_titles[prior["title"].casefold()])
        else:
            ask.append(i)

    said_shapes: dict[str, str] = {}
    if ask:
        say(f"judging {len(ask)} line(s) against {len(candidates)} notes")
        try:
            fresh, said_shapes = classify(brain, [items[i] for i in ask], candidates)
            out.model_calls += 1
        except (CredentialUnavailable, StorageError, PolicyError):
            raise
        except Exception as e:
            say(f"could not judge them ({type(e).__name__}) — leaving them for next time")
            fresh = [None] * len(ask)
        for i, v in zip(ask, fresh):
            if v is None:
                out.left.append((items[i], "I couldn't decide where it goes"))
            elif v[0] == "part":
                lead = ask[int(v[1])]
                parts_of.setdefault(lead, []).append(i)
                judged[items[i].digest()] = {"kind": "part", "of": items[lead].digest()}
            else:
                verdicts[i] = v
    parts_of = _flatten_parts(items, parts_of, judged)
    items = _fold(items, parts_of)

    # 2. Copy into existing notes, then tick — only what actually landed.
    by_master: dict[str, list[int]] = {}
    for i, (kind, title) in verdicts.items():
        if kind == "note":
            by_master.setdefault(title, []).append(i)
    marks: dict[tuple, list[tuple[str, int, str]]] = {
        k: list(v) for k, v in (extra_marks or {}).items()}
    for title, idxs in by_master.items():
        master = known[title]
        group = [items[i] for i in idxs]
        shape = _shape_for(title, said_shapes, state)
        checks = [(it.note_id, it.expected_revision, it.anchor, it.near)
                  for lead in group for it in (lead, *lead.parts)]
        r = ex.apply_write(replace(targets[title], source_checks=checks,
                                  rebase_append=shape != layout.LOG, markdown=
            _entry_markdown(group, shape=shape, existing=bodies.get(title, ""))))
        out.results.append(f"{'✓' if r.ok else '✗'} {title} — {r.reason}")
        if not r.ok:
            for it in group:
                out.left.append((it, r.reason))
            continue
        for it in group:
            out.filed.append((it, title))
            judged[it.digest()] = {"kind": "note", "title": title, "note_id": master.note_id}
            marks.setdefault(_source_key(it), []).extend(_marks_for(it, f"{RECEIPT}{title}"))

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
        if canonical not in state["shapes"] and canonical in said_shapes:
            state["shapes"][canonical] = layout.shape(said_shapes[canonical])
        # Matched on digest, not on the whole dict: the same lead read again
        # next pass keeps its digest but may carry different parts, and a dict
        # comparison would miss that and record the thought twice.
        digest = it.digest()
        stored = [Item.from_dict(d).digest() for d in record["items"]]
        if digest in stored:
            record["items"][stored.index(digest)] = it.as_dict()
        else:
            record["items"].append(it.as_dict())
        if record["asked"] is None:
            new_here.setdefault(canonical, []).append(it)
    if new_here:
        out.proposed.update(new_here)
        r = ex.apply_write(replace(dump_target, markdown=_turn(_proposal_text(new_here))))
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
    items = _bind_items(items)
    proposals: dict = state["proposals"]
    judged: dict = state["judged"]
    rest: list[Item] = []
    landed: set[str] = set()
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
            # Persisted proposal snapshots must not be rebound to today's source.
            # Legacy proposals without captured identity/revision refuse safely.
            waiting = [Item.from_dict(d) for d in record.get("items", [])]
            if word == "no":
                for w in waiting:
                    judged[w.digest()] = {"kind": "declined", "title": title}
                out.declined.append(title)
                receipts.append(f"left “{title}” alone")
                continue
            say(f"making “{title}” with {len(waiting)} line(s)")
            shape = _shape_for(title, {}, state)
            checks = [(source.note_id, source.expected_revision, source.anchor, source.near)
                      for lead in (*waiting, it) for source in (lead, *lead.parts)]
            r = ex.create_approved(title, _entry_markdown(waiting, shape=shape),
                                   folder=FILING_FOLDER, source_checks=checks)
            out.results.append(f"{'✓' if r.ok else '✗'} {title} — {r.reason}")
            if not r.ok:
                proposals[title] = record            # keep the question open
                receipts.append(f"couldn't make “{title}”: {r.reason}")
                continue
            out.created.append(title)
            if r.note_id:
                from . import library
                library.add_home(r.note_id)
            marks: dict[tuple, list[tuple[str, int, str]]] = {}
            for w in waiting:
                out.filed.append((w, title))
                judged[w.digest()] = {"kind": "note", "title": title, "note_id": r.note_id}
                marks.setdefault(_source_key(w), []).extend(_marks_for(w, f"{RECEIPT}{title}"))
                landed.add(w.digest())
                landed.update(p.digest() for p in w.parts)   # its parts landed with it
            _tick(ex, marks, out)
            receipts.append(f"made “{title}”, {len(waiting)} filed")
        _tick(ex, {_source_key(it): [(it.anchor, it.near, f"{RECEIPT}{'; '.join(receipts)}")]}, out)
    return [it for it in rest if it.digest() not in landed]


def run(brain, *, dry_run: bool = False, on_step=None) -> Outcome:
    """File the whole dump: answers to proposals first, then every unfiled line."""
    from .executor import Executor
    from . import policy
    policy.require_ready()
    from . import retention
    retention.reconcile()

    say = on_step or (lambda m: None)
    out = Outcome()
    dump = notes.find_note(workspace.FOLDER, workspace.DUMP)
    if not dump:
        say(f"no {workspace.DUMP} note — run `notron setup`")
        out.results.append(f"✗ {workspace.DUMP} — missing; run notron setup")
        return out
    if policy.current().system_notes.get(workspace.DUMP) != dump.id or not policy.current().readable(dump):
        raise policy.PolicyError('Brain Dump requires setup or permission recovery.')
    items = unfiled(notes.read_body(dump.id), note_id=dump.id, modified=dump.modified)
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
    from . import policy
    policy.require_ready()
    from . import retention
    retention.reconcile()

    out = Outcome()
    if not items:
        return out
    items = _bind_items(items)
    ex = Executor(dry_run=dry_run)
    state = _state()
    extra = {}
    if bare:
        first = items[0]
        extra[_source_key(first)] = [(b, first.near, "") for b in bare]
    _file(ex, brain, items, state, out, extra_marks=extra, on_step=on_step)
    _save(state, dry_run=dry_run)
    return out
