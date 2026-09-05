"""Notron's self-improvement loop — she learns from the answers that missed.

Once a day she re-reads her own conversation and looks for the moments that
went wrong: you corrected her, or asked the same thing again because the first
answer wasn't it. From those she distils short standing rules — "lessons" —
which every future answer is given alongside your instructions.

The loop is built the way a loop has to be to be trusted:

  * **Plain code finds the evidence.** Which exchanges were corrected or
    re-asked is measured deterministically, never guessed by a model. No
    misses, no model calls, no cost.
  * **The proposer and the verifier are different calls.** A model is a poor
    judge of its own output, so one call drafts lessons and a second, separate
    call judges them against your instructions and what she already knows.
  * **Code checks the receipts.** A lesson must quote the exchange it came
    from, and the quote must actually be in the transcript — checked by string
    matching, not by trust.
  * **The Guard applies it.** Lessons land in a note she owns, through the same
    write path as everything else, logged like everything else.
  * **`📌 About Me` always wins.** Lessons sit below your instructions in every
    prompt, and the verifier drops any candidate that contradicts them.
"""

from __future__ import annotations

from .credentials import CredentialUnavailable
from .securestore import StorageError
from .policy import PolicyError

import hashlib
import json
import pathlib
import re
from dataclasses import dataclass
from datetime import datetime

from . import conversation, markup, notes, workspace
from .outbound import Passage, sanitized

from .paths import DATA_DIR
STATE = DATA_DIR / "reflect.json"

MAX_LESSONS = 12          # the note is read on every run; keep it a page, not a scroll
MAX_NEW_PER_RUN = 3       # slow learning compounds; fast learning thrashes
MAX_RUNS_KEPT = 50        # the append-only record of what each run did

# The words a correction starts with. Deliberately dumb and visible — a missed
# marker costs one unlearned lesson, a clever classifier costs a model call on
# every quiet day.
CORRECTION = re.compile(
    r"^(no\b|not\b|nope\b|wrong\b|that's (not|wrong)|i meant\b|actually\b|"
    r"i (said|asked)\b|again[,:]?\s|try again\b|still\b)", re.I,
)


@dataclass(frozen=True)
class Exchange:
    question: str
    answer: str


@dataclass(frozen=True)
class Miss:
    exchange: Exchange
    followup: str          # what the user said next that shows the answer missed
    why: str               # "corrected" | "re-asked"


# ------------------------------------------------------------- reading the past

def exchanges(ask_body_html: str) -> list[Exchange]:
    """The Ask note as (question, answer) pairs, oldest first."""
    lines = markup.to_text(ask_body_html).split("\n")
    out: list[Exchange] = []
    q: list[str] = []
    a: list[str] | None = None
    for line in lines[1:]:                       # first line is the title
        stripped = line.strip()
        if stripped.startswith("Notron:"):
            a = [stripped.removeprefix("Notron:").strip()]
            continue
        if stripped == "———" or set(stripped) <= set("—-_ ") and stripped:
            if a is not None and q:
                out.append(Exchange("\n".join(q).strip(), "\n".join(x for x in a if x).strip()))
            q, a = [], None
            continue
        if not stripped or any(stripped.startswith(f) for f in
                               (workspace.ASK, "Type anything below this line",
                                conversation.QA_RULE)):
            continue
        (q if a is None else a).append(stripped)
    if a is not None and q:
        out.append(Exchange("\n".join(q).strip(), "\n".join(x for x in a if x).strip()))
    return [e for e in out if e.question and e.answer]


def _words(text: str) -> set[str]:
    # Question furniture carries no meaning — "what's on my calendar" and
    # "what is on my calendar" must compare as the same words.
    return set(re.findall(r"[a-z]+", text.lower().replace("'", ""))) - {
        "the", "a", "an", "is", "it", "to", "of", "in", "on", "my", "me", "i", "you",
        "what", "whats", "for", "s", "at", "do", "does", "when", "how", "can",
    }


def _similar(a: str, b: str) -> bool:
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= 0.6


def misses(history: list[Exchange]) -> list[Miss]:
    """The exchanges the next thing you typed proved wrong. Plain code, no model."""
    out: list[Miss] = []
    for prev, nxt in zip(history, history[1:]):
        first = nxt.question.strip().split("\n")[0]
        if CORRECTION.match(first):
            out.append(Miss(prev, nxt.question, "corrected"))
        elif _similar(prev.question, nxt.question):
            out.append(Miss(prev, nxt.question, "re-asked"))
    return out


# ---------------------------------------------------------- propose and verify

PROPOSER_SYSTEM = """You review a personal assistant's recent conversation and write lessons —
short standing rules that would have prevented the answers that missed.

You are given the exchanges that went wrong: the user corrected the answer, or
asked again because the first answer wasn't it.

Reply with JSON only:
{"lessons": [{"rule": "imperative, under 20 words, general enough to reuse",
              "evidence": "a short exact quote from the transcript that taught it"}]}

- At most %d lessons. Zero is a fine answer — only write one when the pattern
  would clearly recur.
- A rule is about HOW to answer (format, sources, assumptions, tone), never a
  fact about the user — facts belong in memory, not here.
- evidence must be copied verbatim from the transcript you were given."""

VERIFIER_SYSTEM = """You judge proposed lessons for a personal assistant. You are not the model
that wrote them, and your job is to reject freely.

You are given the user's standing instructions, the lessons already learned, and
the candidates. Reply with JSON only: {"keep": [0, 2]} — the indices of the
candidates worth keeping.

Reject a candidate that:
- contradicts the standing instructions (those always win),
- repeats an existing lesson or another candidate,
- states a fact about the user rather than a rule about answering,
- is too vague to change any future answer ("be more helpful")."""


def current_lessons() -> list[str]:
    return _read_lessons(workspace.readable_system_note(workspace.LESSONS))


def _read_lessons(n, body=None) -> list[str]:
    if not n:
        return []
    text = markup.to_text(notes.read_body(n.id) if body is None else body)
    return [l.strip("•- ").strip() for l in text.split("\n")
            if l.strip().startswith(("•", "-")) and len(l.strip()) > 3]


def _grounded(evidence: str, transcript: str) -> bool:
    squash = lambda s: re.sub(r"\s+", " ", s).strip().lower()
    return bool(evidence) and squash(evidence) in squash(transcript)


def run(brain, *, dry_run: bool = False, on_step=None) -> dict:
    """One turn of the loop: measure, propose, verify, apply, record."""
    from . import policy
    policy.require_ready()
    from . import retention
    retention.reconcile()
    say = on_step or (lambda m: None)
    out: dict[str, object] = {"at": datetime.now().isoformat(timespec="minutes"),
                              "misses": 0, "proposed": 0, "kept": [], "skipped": ""}

    ask = workspace.readable_system_note(workspace.ASK)
    if not ask:
        out["skipped"] = "no Ask note"
        return out
    body = notes.read_body(ask.id)

    # Nothing new since last reflection — don't pay to re-learn the same day.
    digest = hashlib.sha256(body.encode()).hexdigest()[:16]
    state = _state()
    if state.get("last_digest") == digest and not dry_run:
        out["skipped"] = "nothing new since last reflection"
        return out

    history = exchanges(body)
    found = misses(history)
    out["misses"] = len(found)
    if not found:
        say("no corrections or re-asks — nothing to learn")
        _record(state, digest, out, dry_run)
        return out

    say(f"{len(found)} answer(s) missed — thinking about why")
    transcript = "\n\n".join(
        f"You: {m.exchange.question}\nNotron: {m.exchange.answer}\n"
        f"You ({m.why}): {m.followup}" for m in found[-8:]
    )
    lesson_note = workspace.readable_system_note(workspace.LESSONS)
    from .executor import capture_write
    from dataclasses import replace
    lesson_body = notes.read_body(lesson_note.id) if lesson_note else None
    target = capture_write(workspace.LESSONS, note_id=lesson_note.id if lesson_note else None,
                           body=lesson_body, mode="replace")
    known = _read_lessons(lesson_note, lesson_body)
    lesson_passages = ([Passage.from_note("\n".join(known), lesson_note, "lesson")]
                       if lesson_note else [])
    transcript_passage = sanitized("reflect", [Passage.from_note(transcript, ask, "history")])[0]
    transcript = transcript_passage.text

    try:
        proposed = brain.ask_json(
            system=PROPOSER_SYSTEM % MAX_NEW_PER_RUN,
            user=[transcript_passage, *lesson_passages], purpose="reflect",
            tier="smart", max_tokens=600,
        ).get("lessons", [])
    except (CredentialUnavailable, StorageError, PolicyError):
        raise
    except Exception as e:
        out["skipped"] = f"proposer failed ({type(e).__name__})"
        return out

    candidates = [c for c in proposed
                  if isinstance(c, dict) and c.get("rule")
                  and _grounded(c.get("evidence", ""), transcript)][:MAX_NEW_PER_RUN]
    out["proposed"] = len(candidates)
    if not candidates:
        say("nothing worth keeping")
        _record(state, digest, out, dry_run)
        return out

    about = ""
    about_note = workspace.readable_system_note(workspace.ABOUT)
    if about_note:
        about = markup.to_text(notes.read_body(about_note.id))
    try:
        verdict = brain.ask_json(
            system=VERIFIER_SYSTEM,
            user=([Passage.from_note(about, about_note, "standing")] if about_note else [])
                 + lesson_passages + [transcript_passage,
                   Passage("# Candidates\n" + "\n".join(f"{i}. {c['rule']}" for i, c in enumerate(candidates)),
                           "model")], purpose="reflect",
            tier="fast", max_tokens=300,
        )
        keep = {i for i in verdict.get("keep", []) if isinstance(i, int)}
    except (CredentialUnavailable, StorageError, PolicyError):
        raise
    except Exception:
        keep = set()          # a verifier that fails keeps nothing; next run retries

    kept = [candidates[i]["rule"].strip() for i in sorted(keep) if i < len(candidates)]
    kept = [r for r in kept if r not in known]
    out["kept"] = kept
    if kept and not dry_run:
        merged = (kept + known)[:MAX_LESSONS]
        from .executor import Executor
        body_md = (workspace.SEEDS[workspace.LESSONS].split("\n")[0] + "\n\n"
                   + "\n".join(f"- {l}" for l in merged) + "\n")
        r = Executor().apply_write(replace(target, markdown=body_md, content_sources=
            [note.id for note in (ask, lesson_note, about_note) if note is not None]))
        out["written"] = r.ok
        say(f"learned: {'; '.join(kept)}")
    elif kept:
        say(f"would learn (dry run): {'; '.join(kept)}")

    _record(state, digest, out, dry_run)
    return out


# ------------------------------------------------------------------ run record

def _state() -> dict:
    from .securestore import read_json
    return read_json(STATE)


def _record(state: dict, digest: str, out: dict, dry_run: bool) -> None:
    if dry_run:
        return
    state["last_digest"] = digest
    state.setdefault("runs", []).append(out)
    state["runs"] = state["runs"][-MAX_RUNS_KEPT:]
    from .securestore import write_json
    write_json(STATE, state)
