"""Treating a note as a sequence of blocks you can insert between.

Apple Notes stores a note as a flat run of top-level elements — a <div> per
paragraph, a <ul> per list, and so on. To answer someone *underneath the thing
they wrote*, rather than at the bottom of the note, we need to address those
elements individually: find the block they typed, and put the reply after it.

Everything here is text surgery on that flat run. Nothing is reordered, nothing
is reformatted, and no existing block is ever modified — the only operation is
inserting new blocks between existing ones.
"""

from __future__ import annotations

import re

from .markup import to_text

# Top-level elements Apple Notes emits. <object> wraps tables.
_BLOCK = re.compile(
    r"<(div|ul|ol|h[1-6]|object|table|blockquote)\b[^>]*>.*?</\1>|<br\s*/?>",
    re.I | re.S,
)


def blocks(html: str) -> list[str]:
    """Split a note body into its top-level blocks, losing nothing.

    Everything between recognised blocks is kept as its own fragment, including
    the plain newlines Apple Notes puts between elements. Joining the result back
    together must reproduce the input exactly — the safety check that lets Juno
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


def preserves(old: str, new: str) -> bool:
    """Is `new` exactly `old` with whole blocks inserted at a block boundary?

    This is what lets Juno write inside a note you own without the risk that
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
    grown = len(new) - len(old)
    if grown <= 0:
        return False
    return any(
        new[:at] == old[:at] and new[at + grown:] == old[at:]
        for at in _boundaries(old)
    )


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
