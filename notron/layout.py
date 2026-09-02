"""How a filed thought is laid out in the note it lands in.

Two shapes, chosen per note and remembered:

  * a **log** — things that happen over time: what someone took, ate, did,
    trained, felt. It reads like a journal: one bold date per day, and under
    it each thought as it was written — a sentence, then the list that came
    with it. No stamp on every line; the date is the heading.

  * a **list** — things that simply exist: recipes, ideas, names, places,
    things to buy. Plain bullets, no dates. A thought that arrived as a
    sentence with lines under it keeps that shape.

Pure functions, Markdown out, for `markup.to_html` to render. Nothing here
reads or writes a note.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Sequence

LOG = "log"
LIST = "list"

#: "Wed 2 Sep 2026" — the day, as a person would write it at the top of a page.
STAMP = "%a %-d %b %Y"
_HEADING = re.compile(r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{1,2} [A-Z][a-z]{2} \d{4}$")

#: (lead line, the lines under it)
Entry = tuple[str, Sequence[str]]


def shape(said: str | None) -> str:
    """The shape a model named, or LOG when it said nothing usable — an
    undated supplement log is a worse mistake than a dated recipe."""
    return LIST if (said or "").strip().casefold() == LIST else LOG


def heading(day: date) -> str:
    return f"{day:{STAMP}}"


def under_today(existing_text: str, day: date) -> bool:
    """Does the note already end under today's date? The last dated heading in
    the plain text is the one everything below belongs to."""
    last = None
    for line in existing_text.split("\n"):
        if _HEADING.match(line.strip()):
            last = line.strip()
    return last == heading(day)


def _block(entry: Entry, *, shape: str) -> str:
    lead, parts = entry
    if shape == LIST and not parts:
        return f"- {lead}"
    return "\n".join([lead, *(f"- {p}" for p in parts)])


def markdown(entries: Sequence[Entry], *, shape: str, existing_text: str, day: date) -> str:
    """The Markdown to append for these entries, in this shape, to a note
    whose plain text currently reads `existing_text`."""
    blocks = [_block(e, shape=shape) for e in entries]
    if shape == LIST:
        chunks: list[str] = []
        for b in blocks:
            if "\n" not in b and chunks and "\n" not in chunks[-1]:
                chunks[-1] += "\n" + b          # adjacent bullets, one list
            else:
                chunks.append(b)
        return "\n" + "\n\n".join(chunks) + "\n"
    body = "\n\n".join(blocks)
    if under_today(existing_text, day):
        return "\n" + body + "\n"
    return f"\n**{heading(day)}**\n{body}\n"
