"""Working out what has been said to Notron and what she has not answered.

A note is a conversation if you treat it as one. People do not write neatly at
the bottom — they reply in the middle, add a line at the top, come back to an old
note and add a thought. So rather than assuming a position, Notron reads the whole
note as alternating turns and looks for any of yours that she has not answered.

Her own turns are marked at the top and closed with a horizontal rule, so a reply
that runs to several paragraphs — a heading, a list, a table — is read as one turn
rather than as a heading followed by three new questions from you. Yours are
anything outside those.
"""

from __future__ import annotations

import re
import hashlib
from dataclasses import dataclass, field

from . import markup, notedoc

SIGNATURE = "Notron:"
RULE = "———"
# The light break between your question and her reply. En dashes, not RULE's
# em dashes — it must never be confused with the rule that closes an exchange.
QA_RULE = "– – – – – – – – – –"
# What she answers to. Not just her name: macOS autocorrects "Notron" to
# "Norton" the first time you type it, and she is addressed by name in every
# note she is ever tagged in. A tag she does not recognise is not a small
# annoyance — it is silence, and from the user's side silence is
# indistinguishable from her being asleep or broken.
#
# The Mac half of this is fixed properly (onboarding teaches the system speller
# the word), but the phone keeps its own dictionary and there is no reaching it,
# so the misspellings are accepted everywhere instead. `\b` still applies: she
# does not answer to "@nortonantivirus", and **her own writing never changes** —
# she signs `**Notron:**`, and SIGNATURE is untouched.
NAMES = ("notron", "nortron", "norton", "notrn")
TAG = re.compile(r"(?:^|\s)[#@](?:" + "|".join(NAMES) + r")\b", re.I)

# Blank blocks allowed inside one turn. Lines typed together stay together;
# leave more space than this and it reads as a separate thought.
MAX_GAP = 1


@dataclass(frozen=True)
class Question:
    text: str
    after: int          # insert the reply after this block index
    before: int | None = None  # first source block; never re-find duplicate text


def _is_notron(text: str) -> bool:
    return text.lstrip().startswith(SIGNATURE)


def _is_blank(text: str, ignore: tuple[str, ...]) -> bool:
    """Empty space or a note's standing header — never a rule."""
    stripped = text.strip()
    if not stripped:
        return True
    if stripped == RULE or set(stripped) <= set("—-_ "):
        return False
    return any(stripped == line or stripped.startswith(line) for line in ignore)


def _is_furniture(text: str, ignore: tuple[str, ...]) -> bool:
    stripped = text.strip()
    if not stripped or stripped == RULE or set(stripped) <= set("—-_ "):
        return True
    # A note's own standing header is scenery, not something anyone said.
    return any(stripped == line or stripped.startswith(line) for line in ignore)


@dataclass(frozen=True)
class Turn:
    role: str
    text: str
    request_id: str | None = None


@dataclass(frozen=True)
class ConversationContext:
    thread_id: str
    turns: list[Turn] = field(default_factory=list)
    action_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Piece:
    role: str
    text: str
    before: int
    after: int
    complete: bool = True


def _filed(text: str) -> bool:
    parts = text.splitlines()
    spoke = [p for p in parts if TAG.search(p)] or parts
    return bool(spoke) and all(p.removeprefix("• ").startswith(notedoc.FILED) for p in spoke)


def _without_receipts(text: str) -> str:
    return "\n".join(line for line in text.splitlines()
                     if not line.removeprefix("• ").startswith(notedoc.FILED))


def _pieces(body_html: str, ignore: tuple[str, ...], *, exact_ignore: tuple[str, ...] = ()):
    """One source-indexed scan shared by detection and history selection.

    Signatures describe display structure only; they establish no authorship or
    operation identity. An unclosed reply remains answered for detection but is
    never a complete historical exchange.
    """
    texts = notedoc.texts(body_html)
    ignore = (*(line for line in ignore if line not in exact_ignore), QA_RULE)

    def furniture(text: str) -> bool:
        return text.strip() in exact_ignore or _is_furniture(text, ignore)

    i = 0
    while i < len(texts):
        start = i
        if _is_notron(texts[i]):
            parts = [texts[i].lstrip()[len(SIGNATURE):].strip()]
            i += 1
            while i < len(texts) and texts[i].strip() != RULE:
                parts.append(texts[i].strip())
                i += 1
            yield _Piece('assistant', "\n".join(p for p in parts if p).strip(), start, i,
                         complete=i < len(texts))
            i += 1
            continue
        if texts[i].strip() == 'New topic':
            yield _Piece('topic', '', i, i)
            i += 1
            continue
        if texts[i].strip() == RULE:
            yield _Piece('break', '', i, i)
            i += 1
            continue
        if furniture(texts[i]):
            i += 1
            continue
        parts, end, gap = [], i, 0
        while i < len(texts) and not _is_notron(texts[i]) and texts[i].strip() not in (RULE, 'New topic'):
            if furniture(texts[i]):
                gap += 1
                if gap > MAX_GAP:
                    break
            else:
                gap = 0
                parts.append(texts[i].strip())
                end = i
            i += 1
        text = "\n".join(parts).strip()
        if text:
            yield _Piece('filed' if _filed(text) else 'user', text, start, end)


def unanswered(
    body_html: str,
    *,
    ignore: tuple[str, ...] = (),
    require_tag: bool = False,
) -> list[Question]:
    """Find unanswered source-positioned user runs, including mid-note inserts."""
    pieces = list(_pieces(body_html, ignore))
    return [Question(piece.text, piece.after, piece.before)
            for index, piece in enumerate(pieces)
            if piece.role == 'user'
            and (index + 1 == len(pieces) or pieces[index + 1].role != 'assistant')
            and (not require_tag or TAG.search(piece.text))]


def _history_and_topic(body_html: str, question: Question, *, ignore: tuple[str, ...],
                       require_tag: bool, max_exchanges: int, max_chars: int):
    texts = notedoc.texts(body_html)
    # Notes' first body line is always its title, and its standing help is not a
    # conversation turn. Callers can supply additional registered furniture.
    title = next((text.strip() for text in texts if text.strip()), '')
    furniture = (*ignore, 'Type anything below this line')
    pieces = list(_pieces(body_html, furniture, exact_ignore=(title,) if title else ()))
    start = question.before
    if start is None:
        matches = [p for p in pieces if p.role == 'user' and p.after == question.after
                   and p.text == question.text]
        if len(matches) != 1:
            return [], 0
        start = matches[0].before
    exchanges = []
    pending = None
    topic = 0
    for piece in pieces:
        if piece.before >= start:
            break
        if piece.role == 'topic':
            exchanges.clear()
            pending = None
            topic += 1
        elif piece.role == 'user':
            if pending:
                exchanges.clear()
            pending = piece if not require_tag or TAG.search(piece.text) else None
        elif piece.role == 'assistant':
            if pending and piece.complete and piece.text and piece.after < start:
                exchanges.append((Turn('user', _without_receipts(pending.text)), Turn('assistant', piece.text)))
            else:
                exchanges.clear()
            pending = None
        else:
            if pending:
                exchanges.clear()
            pending = None
    if pending:
        exchanges.clear()
    selected, size = [], 0
    # Keep the most recent contiguous whole exchanges. Skipping an oversized
    # recent answer would make "that" silently refer to an older answer.
    for exchange in reversed(exchanges):
        chars = sum(len(turn.text) for turn in exchange)
        if len(selected) >= max(0, min(3, max_exchanges)) or size + chars > min(8000, max_chars):
            break
        selected.append(exchange)
        size += chars
    return [turn for exchange in reversed(selected) for turn in exchange], topic


def history_before(body_html: str, question: Question, *, max_exchanges: int = 3,
                   max_chars: int = 8000) -> list[Turn]:
    """At most three whole preceding exchanges in this topic, capped at 8,000 chars."""
    turns, _ = _history_and_topic(body_html, question, ignore=(), require_tag=False,
                                 max_exchanges=max_exchanges, max_chars=max_chars)
    return turns


def local_context_before(body_html: str, question: Question, *,
                         ignore: tuple[str, ...] = ()) -> str:
    """Local tagged-note material through the current thought, without history.

    Completed exchanges belong exclusively to bounded conversation history.
    Source positions select the captured question, so later text and duplicate
    questions cannot leak into its local context. The caller applies its local
    context size cap and normal outbound policy/redaction.
    """
    texts = notedoc.texts(body_html)
    title = next((text.strip() for text in texts if text.strip()), '')
    furniture = (*ignore, 'Type anything below this line')
    pieces = list(_pieces(body_html, furniture, exact_ignore=(title,) if title else ()))
    matches = [i for i, piece in enumerate(pieces)
               if piece.role == 'user' and piece.after == question.after
               and piece.text == question.text
               and (question.before is None or piece.before == question.before)]
    if len(matches) != 1:
        return ''
    current = matches[0]
    local = []
    for index, piece in enumerate(pieces[:current + 1]):
        if piece.role == 'topic':
            local.clear()
        elif piece.role == 'user':
            # Looking beyond the current piece is unnecessary: its full thought
            # is always included, even when called after a reply was inserted.
            if index == current or pieces[index + 1].role != 'assistant':
                local.append(_without_receipts(piece.text))
    return "\n\n".join(local)


def context_before(body_html: str, question: Question, *, note_id: str,
                   ignore: tuple[str, ...] = (), require_tag: bool = False,
                   max_exchanges: int = 3, max_chars: int = 8000) -> ConversationContext:
    """A note/topic identity stable across answer inserts and later appends.

    Topic ordinals change if boundaries are edited; durable clarification source
    revisions must additionally be checked before consenting to any action.
    Text never supplies trusted request IDs or action references.
    """
    turns, topic = _history_and_topic(body_html, question, ignore=ignore, require_tag=require_tag,
                                     max_exchanges=max_exchanges, max_chars=max_chars)
    thread_id = hashlib.sha256(f'{len(note_id)}:{note_id}:topic:{topic}'.encode()).hexdigest()
    return ConversationContext(thread_id, turns)


def turn(markdown: str) -> str:
    """Her turn, set as a different voice, as Markdown ready for `markup.to_html`.

    A note has no chat bubbles, so typography does the job instead: a light
    rule opens her turn, your words stay plain, hers are italic under a bold
    signature, and a heavier rule closes the turn. `SIGNATURE` and `RULE` are
    how `unanswered` recognises the turn as hers — keep them in step.
    """
    return f"{QA_RULE}\n\n**{SIGNATURE}**\n{markup.voice(markdown)}\n\n{RULE}\n"


def tagged_lines(text: str) -> str:
    """Just the lines that mention her.

    The turn she found may be a whole paragraph of your thinking with a tag on the
    end. The thinking is context, not the question — the question is the line you
    wrote to her.
    """
    lines = [l for l in text.split("\n") if TAG.search(l)]
    return "\n".join(lines).strip() or text


def strip_tag(text: str) -> str:
    return re.sub(r"\s{2,}", " ", TAG.sub(" ", text)).strip()
