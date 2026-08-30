"""Working out what has been said to Juno and what she has not answered.

A note is a conversation if you treat it as one. People do not write neatly at
the bottom — they reply in the middle, add a line at the top, come back to an old
note and add a thought. So rather than assuming a position, Juno reads the whole
note as alternating turns and looks for any of yours that she has not answered.

Her own turns are marked at the top and closed with a horizontal rule, so a reply
that runs to several paragraphs — a heading, a list, a table — is read as one turn
rather than as a heading followed by three new questions from you. Yours are
anything outside those.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import notedoc

SIGNATURE = "Juno:"
RULE = "———"
TAG = re.compile(r"(?:^|\s)[#@]juno\b", re.I)


@dataclass(frozen=True)
class Question:
    text: str
    after: int          # insert the reply after this block index


def _is_juno(text: str) -> bool:
    return text.lstrip().startswith(SIGNATURE)


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
    """Every turn of yours that Juno has not replied to yet.

    `ignore` lists the note's standing header lines, which are scenery rather
    than anything anyone said. `require_tag` restricts this to turns that
    actually mention her, which is how she behaves in notes that are not hers —
    she stays quiet unless spoken to.
    """
    texts = notedoc.texts(body_html)
    out: list[Question] = []
    i = 0
    while i < len(texts):
        if _is_juno(texts[i]):
            # Everything through to her closing rule is one reply of hers.
            i += 1
            while i < len(texts) and texts[i].strip() != RULE:
                i += 1
            i += 1
            continue

        if _is_furniture(texts[i], ignore):
            i += 1
            continue

        # Gather this turn: consecutive blocks until Juno speaks or a rule lands.
        parts, end = [], i
        while (i < len(texts) and not _is_juno(texts[i])
               and texts[i].strip() != RULE):
            if texts[i].strip() and not _is_furniture(texts[i], ignore):
                parts.append(texts[i].strip())
                end = i
            i += 1

        answered = i < len(texts) and _is_juno(texts[i])
        turn = "\n".join(parts).strip()

        if turn and not answered and (not require_tag or TAG.search(turn)):
            out.append(Question(text=turn, after=end))

    return out


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
