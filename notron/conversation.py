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
from dataclasses import dataclass

from . import markup, notedoc

SIGNATURE = "Notron:"
RULE = "———"
# The light break between your question and her reply. En dashes, not RULE's
# em dashes — it must never be confused with the rule that closes an exchange.
QA_RULE = "– – – – – – – – – –"
TAG = re.compile(r"(?:^|\s)[#@]notron\b", re.I)

# Blank blocks allowed inside one turn. Lines typed together stay together;
# leave more space than this and it reads as a separate thought.
MAX_GAP = 1


@dataclass(frozen=True)
class Question:
    text: str
    after: int          # insert the reply after this block index


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


def unanswered(
    body_html: str,
    *,
    ignore: tuple[str, ...] = (),
    require_tag: bool = False,
) -> list[Question]:
    """Every turn of yours that Notron has not replied to yet.

    A turn is a run of lines you wrote together. Lines typed one after another
    belong to the same thought and stay together; a real gap between paragraphs
    starts a new one. Getting this wrong loses messages: a question typed above
    an older exchange was swallowed into it, saw Notron's old reply sitting
    underneath, and concluded it had already been answered.

    `ignore` lists the note's standing header lines, which are scenery rather
    than anything anyone said. `require_tag` restricts this to turns that
    mention her, which is how she behaves in notes that are not hers — she stays
    quiet unless spoken to.
    """
    texts = notedoc.texts(body_html)
    out: list[Question] = []
    i = 0
    while i < len(texts):
        if _is_notron(texts[i]):
            # Everything through to her closing rule is one reply of hers.
            i += 1
            while i < len(texts) and texts[i].strip() != RULE:
                i += 1
            i += 1
            continue

        if _is_furniture(texts[i], ignore):
            i += 1
            continue

        parts, end, gap = [], i, 0
        while i < len(texts) and not _is_notron(texts[i]) and texts[i].strip() != RULE:
            if _is_furniture(texts[i], ignore):
                gap += 1
                if gap > MAX_GAP:      # a real break between two separate thoughts
                    break
            else:
                gap = 0
                parts.append(texts[i].strip())
                end = i
            i += 1

        # Answered only if Notron speaks next. Blank space between does not count,
        # but a rule does: a reply on the far side of a rule belongs to a
        # different exchange, not to this question.
        peek = i
        while peek < len(texts) and _is_blank(texts[peek], ignore):
            peek += 1
        answered = peek < len(texts) and _is_notron(texts[peek])

        # A turn the Filer has ticked is done too: the lines that spoke to her
        # now read `✓ … → Somewhere`, and the receipt is the reply. Without this
        # rule a filed `@notron file this` would be read as unanswered forever.
        # Untagged lines typed alongside are context, not the question, so they
        # do not have to be ticked — unless nothing was tagged (the Ask note),
        # where every line is the question.
        spoke = [p for p in parts if TAG.search(p)] or parts
        filed = bool(parts) and all(p.startswith(notedoc.FILED) for p in spoke)

        turn = "\n".join(parts).strip()
        if turn and not answered and not filed and (not require_tag or TAG.search(turn)):
            out.append(Question(text=turn, after=end))

    return out


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
