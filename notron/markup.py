"""Render Markdown into the HTML subset Apple Notes actually displays.

Verified on macOS 26.2 (2026-08-29):
  renders   h1/h2/h3, <b>, <i>, <ul>, <ol>, <table>, <div>
  stripped  <a href> (href is dropped, text is underlined) -> emit bare URLs
  stripped  class="checklist" (no native tap-to-tick) -> emit U+2610 / U+2705

The first line of the body becomes the note's title in Notes, so `render`
always emits the title as the opening block.
"""

from __future__ import annotations

import html
import re

TODO = "☐"   # ☐
DONE = "✅"   # ✅

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)")
_CODE = re.compile(r"`([^`]+)`")


def _inline(text: str) -> str:
    out = html.escape(text, quote=False)
    out = _BOLD.sub(r"<b>\1</b>", out)
    out = _ITALIC.sub(r"<i>\1</i>", out)
    out = _CODE.sub(r"<tt>\1</tt>", out)
    return out


def _table(rows: list[str]) -> str:
    cells = []
    for row in rows:
        parts = [p.strip() for p in row.strip().strip("|").split("|")]
        if all(set(p) <= {"-", ":", " "} and p for p in parts):
            continue  # markdown separator row
        cells.append(parts)
    if not cells:
        return ""
    body = "".join(
        "<tr>" + "".join(f"<td><div>{_inline(c)}</div></td>" for c in row) + "</tr>"
        for row in cells
    )
    return f"<table><tbody>{body}</tbody></table>"


def to_html(markdown: str) -> str:
    """Convert a Markdown fragment to Apple Notes HTML."""
    out: list[str] = []
    lines = markdown.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            out.append("<div><br></div>")
            i += 1
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            out.append(_table(block))
            continue

        if re.match(r"^\s*(-|\*)\s+", line) and not stripped.startswith(("- [", "* [")):
            items = []
            while i < len(lines) and re.match(r"^\s*(-|\*)\s+", lines[i]):
                items.append(_inline(re.sub(r"^\s*(-|\*)\s+", "", lines[i])))
                i += 1
            out.append("<ul>" + "".join(f"<li>{t}</li>" for t in items) + "</ul>")
            continue

        if re.match(r"^\s*\d+[.)]\s+", line):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+[.)]\s+", lines[i]):
                items.append(_inline(re.sub(r"^\s*\d+[.)]\s+", "", lines[i])))
                i += 1
            out.append("<ol>" + "".join(f"<li>{t}</li>" for t in items) + "</ol>")
            continue

        m = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            i += 1
            continue

        if set(stripped) <= {"-", "_", "*"} and len(stripped) >= 3:
            out.append("<div>———</div>")
            i += 1
            continue

        # Checkbox lines: "- [ ] thing" / "- [x] thing"
        m = re.match(r"^\s*[-*]\s+\[( |x|X)\]\s+(.*)$", line)
        if m:
            mark = DONE if m.group(1).lower() == "x" else TODO
            out.append(f"<div>{mark} {_inline(m.group(2))}</div>")
            i += 1
            continue

        out.append(f"<div>{_inline(stripped)}</div>")
        i += 1

    return "".join(out)


def render(title: str, markdown: str) -> str:
    """Full note body. The title must lead, because Notes reads it as the name."""
    return f"<div><h1>{_inline(title)}</h1></div>" + to_html(markdown)


_STRUCTURAL = re.compile(r"^\s*(#{1,3}\s|[-*]\s|\d+[.)]\s|\||☐|✅|———|\*\*Notron)")


def voice(markdown: str) -> str:
    """Set Notron's prose in italics, so her turns read as a different voice.

    In a note there are no chat bubbles — the only thing separating what you
    typed from what she wrote is typography. Your words stay plain; hers are
    italic. Only running prose is slanted: headings, lists, tables and
    checkboxes keep their structure, because italicising a table makes it
    harder to read, not easier to attribute.
    """
    out = []
    for line in markdown.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if not stripped or _STRUCTURAL.match(line):
            out.append(line)
        elif stripped.startswith("*") and stripped.endswith("*"):
            out.append(line)                      # already emphasised
        else:
            out.append(f"*{stripped}*")
    return "\n".join(out)


_TAG = re.compile(r"<[^>]+>")
_BLOCK_END = re.compile(r"</(div|p|li|tr|h[1-6]|ul|ol|table)>", re.I)


def to_text(body_html: str) -> str:
    """Flatten a Notes body back to plain text for feeding the model."""
    text = re.sub(r"<br\s*/?>", "\n", body_html, flags=re.I)
    text = _BLOCK_END.sub("\n", text)
    text = re.sub(r"<li[^>]*>", "• ", text, flags=re.I)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(l.rstrip() for l in text.split("\n")).strip()
