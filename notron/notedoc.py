"""Treating a note as a sequence of blocks you can insert between.

Apple Notes stores a note as a flat run of top-level elements — a <div> per
paragraph, a <ul> per list, and so on. To answer someone *underneath the thing
they wrote*, rather than at the bottom of the note, we need to address those
elements individually: find the block they typed, and put the reply after it.

Everything here is text surgery on that flat run. Nothing is reordered, nothing
is reformatted, and nothing of yours is ever deleted or edited. Two operations
exist, and each has a checker that proves it did only what it says:

  * inserting new blocks between existing ones (`insert_after` / `preserves`);
  * ticking a line as filed — a `✓ ` in front of its text and a receipt after
    it, inside its own element (`mark` / `marks_between`).
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass

from .markup import to_text

#: The text tick that marks a line as filed. Native Notes checkboxes cannot be
#: written by script, so the tick is a character — and it is text the user can
#: delete to "unfile" a line.
FILED = "✓ "
#: Separates a filed line from where it went: `✓ magnesium → Supplements`.
RECEIPT = " → "

# One <li> and what is inside it.
_LI = re.compile(r"<li\b[^>]*>(.*?)</li>", re.I | re.S)
# The elements whose text is "a line" someone wrote. A mark goes just inside.
_OPENS_LINE = re.compile(r"<(?:div|li|h[1-6]|p)\b[^>]*>", re.I)
_OPENS_LINE_AT_END = re.compile(r"<(?:div|li|h[1-6]|p)\b[^>]*>$", re.I)
_CLOSES_LINE_AT_END = re.compile(r"</(?:div|li|h[1-6]|p)>\s*$", re.I)
_CLOSES_LINE = re.compile(r"</(?:div|li|h[1-6]|p)>|<br\s*/?>", re.I)
_TRAILING_BR = re.compile(r"(?:<br\s*/?>\s*)+$", re.I)
_RECEIPT_TEXT = re.compile(re.escape(RECEIPT) + r"[^<>\n]+")

# Top-level elements Apple Notes emits. <object> wraps tables.
_BLOCK = re.compile(
    r"<(div|ul|ol|h[1-6]|object|table|blockquote)\b[^>]*>.*?</\1>|<br\s*/?>",
    re.I | re.S,
)


def blocks(html: str) -> list[str]:
    """Split a note body into its top-level blocks, losing nothing.

    Everything between recognised blocks is kept as its own fragment, including
    the plain newlines Apple Notes puts between elements. Joining the result back
    together must reproduce the input exactly — the safety check that lets Notron
    write inside your notes is built on that being true.
    """
    out, pos = [], 0
    for m in _BLOCK.finditer(html):
        if m.start() > pos:
            out.append(html[pos : m.start()])
        out.append(m.group(0))
        pos = m.end()
    if pos < len(html):
        out.append(html[pos:])
    return out


def texts(html: str) -> list[str]:
    """The plain text of each block, index-aligned with `blocks`."""
    return [to_text(b) for b in blocks(html)]


def insert_after(html: str, index: int, new_html: str) -> str:
    """Put `new_html` immediately after block `index`. Nothing else moves."""
    parts = blocks(html)
    if not parts:
        return html + new_html
    index = max(0, min(index, len(parts) - 1))
    parts.insert(index + 1, new_html)
    return "".join(parts)


def locate(html: str, anchor: str, *, near: int) -> int:
    """The index of the block whose text is `anchor`, preferring the one nearest
    `near`.

    Between the listener reading a note and the executor writing the answer, the
    user may keep typing — on the phone, mid-sync — and every block below their
    edit shifts. An index captured before the shift then points at the wrong
    block, and the answer lands under the wrong words. So the index is treated
    as a hint and the words as the truth: find the anchor text again at write
    time, closest to where it used to be.
    """
    want = anchor.strip()
    if not want:
        return near
    hits = [i for i, t in enumerate(texts(html)) if t.strip() == want]
    if not hits:
        return near
    return min(hits, key=lambda i: abs(i - near))


@dataclass(frozen=True)
class Line:
    """One line of a note: a block, or one item inside a list block."""
    block: int      # index into `blocks`
    item: int       # which <li> inside the block, or -1 for the block itself
    text: str
    after_gap: bool = False   # an empty line sits between this and the one above


def lines(html: str) -> list[Line]:
    """Every line someone could have typed, in order. A list block yields one
    line per item, because a brain dump written as bullets is still one
    thought per line.

    An empty element — the <div><br></div> a blank line leaves behind — is not
    a line, but the next line remembers it (`after_gap`): a blank line is how
    people separate one thought from the next."""
    out: list[Line] = []
    gap = False
    for i, block in enumerate(blocks(html)):
        items = list(_LI.finditer(block))
        if items:
            for k, m in enumerate(items):
                out.append(Line(i, k, to_text(m.group(1)).strip(), gap))
                gap = False
            continue
        text = to_text(block).strip()
        if text:
            out.append(Line(i, -1, text, gap))
            gap = False
        elif block.strip():
            gap = True          # an element with nothing in it; bare "\n" between elements is not
    return out


def find_line(html: str, anchor: str, *, near: int) -> Line | None:
    """The line whose text is `anchor`, preferring the one nearest block `near`.
    Same idea as `locate`: the index is a hint, the words are the truth."""
    want = anchor.strip()
    if not want:
        return None
    hits = [ln for ln in lines(html) if ln.text.strip() == want]
    if not hits:
        return None
    return min(hits, key=lambda ln: abs(ln.block - near))


def mark(html: str, line: Line, *, suffix: str = "") -> str:
    """Tick one line: `FILED` in front of its text and `suffix` after it, both
    inside the line's own element. No block is added, moved or removed.

    A trailing <br> stays after the receipt, or the receipt would render on a
    line of its own. `suffix` is plain text and is escaped here."""
    parts = blocks(html)
    block = parts[line.block]
    if line.item >= 0:
        items = list(_LI.finditer(block))
        if line.item >= len(items):
            return html
        start, end = items[line.item].start(1), items[line.item].end(1)
    else:
        opened = _OPENS_LINE.match(block)
        closed = _CLOSES_LINE_AT_END.search(block)
        if not opened or not closed:
            return html                      # not a line she knows how to tick
        start, end = opened.end(), closed.start()
    inner = block[start:end]
    tail = _TRAILING_BR.search(inner)
    body_end = start + (tail.start() if tail else len(inner))
    parts[line.block] = (block[:start] + FILED + block[start:body_end]
                         + _html.escape(suffix, quote=False) + block[body_end:])
    return "".join(parts)


def mark_lines(html: str, marks: list[tuple[str, int, str]]) -> tuple[str, int]:
    """Tick several lines, each given as (anchor text, block hint, receipt).
    Returns the new body and how many lines were actually found and ticked —
    a line the user has since edited or deleted is simply left alone."""
    done = 0
    for anchor, near, receipt in marks:
        line = find_line(html, anchor, near=near)
        if line is None or line.text.startswith(FILED):
            continue
        updated = mark(html, line, suffix=receipt)
        if updated != html:
            html, done = updated, done + 1
    return html, done


def marks_between(old: str, new: str) -> list[str] | None:
    """Is `new` exactly `old` with lines ticked, and nothing else?

    Walks both bodies together. Wherever they differ, the extra text in `new`
    must be either a `✓ ` sitting right after a line's opening tag, or a
    receipt (` → …`) sitting right before a line's closing tag or a <br>.
    Every character of `old` must still be there, in order. Returns what was
    added — so the Guard can check it for secrets — or None if anything else
    changed. The Guard for `mark` writes is this function."""
    added: list[str] = []
    i = j = 0
    while j < len(new):
        if i < len(old) and old[i] == new[j]:
            i += 1
            j += 1
            continue
        if new.startswith(FILED, j) and _OPENS_LINE_AT_END.search(new, max(0, j - 300), j):
            added.append(FILED)
            j += len(FILED)
            continue
        m = _RECEIPT_TEXT.match(new, j)
        if m and _CLOSES_LINE.match(new, m.end()):
            added.append(m.group(0))
            j = m.end()
            continue
        return None
    if i != len(old) or not added:
        return None
    return added


def preserves(old: str, new: str) -> bool:
    """Is `new` exactly `old` with whole blocks inserted at a block boundary?

    This is what lets Notron write inside a note you own without the risk that
    makes that dangerous. Two conditions, both required:

      * every original character still sits in `new`, in order — nothing of
        yours was deleted or edited; and
      * the new text was wedged *between* two elements, not into the middle of
        one — so she can answer under your paragraph but can never slip a word
        into the middle of your sentence.

    The second condition matters more than it looks. Without it, "my book idea"
    becoming "my BETTER book idea" passes the first test perfectly well.

    Rather than infer the edit, we simply try every boundary a legitimate insert
    could have used. There are only as many as there are blocks.
    """
    return inserted(old, new) is not None


def inserted(old: str, new: str) -> str | None:
    """The text an insert put in, or None if `new` is not a legitimate insert.

    `preserves` answers whether the insert was allowed; this answers *what she
    added*, which is what the Guard should judge her on. Judging the whole new
    body instead means the user's own words are held against her: one
    key-shaped string anywhere in a note they wrote blocks every answer she
    ever tries to write into it, and her reply never removes their text, so the
    block never lifts. Seen live on a story bible whose scene tags include the
    word SECRET.
    """
    grown = len(new) - len(old)
    if grown <= 0:
        return None
    for at in _boundaries(old):
        if new[:at] == old[:at] and new[at + grown:] == old[at:]:
            return new[at:at + grown]
    return None


def _boundaries(html: str) -> list[int]:
    """Every offset where one element ends and the next begins, plus the ends.

    Derived from the same split `blocks` uses rather than by looking for `><`:
    Apple Notes puts a newline between top-level elements, so the elements very
    rarely actually touch.
    """
    offsets, running = [0], 0
    for part in blocks(html):
        running += len(part)
        offsets.append(running)
    if running != len(html):          # split lost something; trust nothing
        return [0, len(html)]
    return offsets
