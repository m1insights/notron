"""The semantic index over every note you own.

Keyword search fails the moment you phrase a question differently from how you
wrote the note — which is most of the time. This turns all your notes into
vectors once, caches them on disk, and re-embeds only the notes that changed.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import asdict, dataclass

CACHE = pathlib.Path(__file__).resolve().parents[1] / ".notron" / "index.json"
VECTORS = CACHE.with_name("vectors.npy")
CHUNK_CHARS = 1400
CHUNK_OVERLAP = 150


@dataclass
class Chunk:
    note_id: str
    title: str
    folder: str
    modified: str
    text: str
    vector: list[float] | None = None
    attachment: str = ""     # the file this chunk came out of, if it is not
                             # the note's own words. What she saw and heard is
                             # searchable from anywhere, not only in the note
                             # it hangs off.


def split(text: str) -> list[str]:
    """Break a note into overlapping windows on paragraph boundaries."""
    text = text.strip()
    if len(text) <= CHUNK_CHARS:
        return [text] if text else []
    out, start = [], 0
    while start < len(text):
        end = min(start + CHUNK_CHARS, len(text))
        if end < len(text):
            brk = text.rfind("\n", start + CHUNK_CHARS // 2, end)
            if brk > start:
                end = brk
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        if end >= len(text):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return out


def _load() -> dict[str, list[dict]]:
    if not CACHE.exists():
        return {}
    return json.loads(CACHE.read_text())


def _save(data: dict[str, list[dict]]) -> None:
    """Metadata as JSON, vectors as one float32 array. Keeps the index ~5x smaller
    and lets search memory-map it instead of parsing megabytes of text."""
    import numpy as np

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    vectors, row = [], 0
    for chunks in data.values():
        for c in chunks:
            vec = c.pop("vector", None)
            if vec is None:
                c["row"] = None
                continue
            vectors.append(vec)
            c["row"] = row
            row += 1
    CACHE.write_text(json.dumps(data))
    if vectors:
        np.save(VECTORS, np.asarray(vectors, dtype="float32"))


def _vector_at(row: int | None):
    import numpy as np

    if row is None or not VECTORS.exists():
        return None
    global _MMAP
    if _MMAP is None:
        _MMAP = np.load(VECTORS, mmap_mode="r")
    return _MMAP[row].tolist()


_MMAP = None


def _carried(live: list, on_progress=None) -> dict[str, list]:
    """Every file in the library, by note id — one request per folder.

    `attachments.in_folder` is 0.18s for a 222-note folder against 87s of
    per-note queries for the same answer, so a full index run can afford to ask
    about every folder rather than guessing which ones might hold something.
    A folder that cannot be read costs the attachments in it, not the run.
    """
    from . import attachments

    out: dict[str, list] = {}
    for folder in sorted({n.folder for n in live}):
        try:
            out.update(attachments.in_folder(folder))
        except Exception as e:
            if on_progress:
                on_progress(f"couldn't check {folder} for files ({type(e).__name__})")
    return out


def _attachment_rows(note, atts: list, brain, extract: bool, on_progress=None) -> list[Chunk]:
    """One chunk per file she can put into words, titled after the file.

    Without `extract` nothing is described or transcribed — only what is
    already on disk is read. A first index run over a library of screenshots
    must not quietly spend a vision call on each of them.
    """
    from . import attachments

    rows = []
    for att in atts:
        try:
            text = attachments.as_text(att, brain) if extract else attachments.known_text(att)
        except Exception as e:
            if on_progress:
                on_progress(f"couldn't read {att.name} ({type(e).__name__})")
            continue
        if not text.strip():
            continue
        for piece in split(text):
            rows.append(Chunk(note.id, f"{att.name} (attached to {note.title})",
                              note.folder, note.modified, piece, attachment=att.id))
    return rows


def build(brain, *, on_progress=None, force: bool = False, extract: bool = False) -> dict:
    """Embed every note that is new or has changed since the last run."""
    from . import markup, notes, workspace

    cached = {} if force else _load()
    from . import library

    live = library.user_notes()
    carried = _carried(live, on_progress)

    fresh: dict[str, list[dict]] = {}
    pending: list[tuple[str, Chunk]] = []
    reused = 0

    for n in live:
        old = cached.get(n.id)
        atts = carried.get(n.id, [])
        att_rows = _attachment_rows(n, atts, brain, extract, on_progress)
        # A note can be untouched while what she knows about the file hanging
        # off it has changed — she looked at the picture yesterday answering
        # something else. Reusing the cached chunks would lose that for good.
        same_files = old is not None and (
            {c["attachment"] for c in old if c.get("attachment")}
            == {c.attachment for c in att_rows})
        if (old and same_files and old[0].get("modified") == n.modified
                and old[0].get("row") is not None):
            for c in old:
                c["vector"] = _vector_at(c["row"])
            fresh[n.id] = old
            reused += 1
            continue
        text = markup.to_text(notes.read_body(n.id))
        rows = []
        for piece in split(text):
            c = Chunk(n.id, n.title, n.folder, n.modified, piece)
            rows.append(asdict(c))
            pending.append((n.id, c))
        for c in att_rows:
            rows.append(asdict(c))
            pending.append((n.id, c))
        fresh[n.id] = rows

    if on_progress:
        on_progress(f"{len(live)} notes · {reused} unchanged · {len(pending)} chunks to embed")

    if pending:
        vectors = brain.embed([c.text for _, c in pending])
        by_id: dict[str, list[list[float]]] = {}
        for (nid, _), vec in zip(pending, vectors):
            by_id.setdefault(nid, []).append(vec)
        for nid, vecs in by_id.items():
            for row, vec in zip([r for r in fresh[nid]], vecs):
                row["vector"] = vec

    _save(fresh)
    return {"notes": len(live), "reused": reused, "embedded": len(pending)}


def search(query: str, brain, *, limit: int = 8) -> list[Chunk]:
    import numpy as np

    data = _load()
    rows = _readable([r for chunks in data.values() for r in chunks if r.get("row") is not None])
    if not rows or not VECTORS.exists():
        return []

    store = np.load(VECTORS, mmap_mode="r")
    matrix = np.asarray(store[[r["row"] for r in rows]], dtype="float32")
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9
    q = np.array(brain.embed([query])[0], dtype="float32")
    q /= np.linalg.norm(q) + 1e-9

    scores = matrix @ q
    best = np.argsort(-scores)[: limit * 3]

    seen: set[str] = set()
    out: list[Chunk] = []
    for i in best:
        row = rows[int(i)]
        key = f"{row['note_id']}:{row['text'][:40]}"
        if key in seen:
            continue
        seen.add(key)
        out.append(Chunk(**{k: row[k] for k in ("note_id", "title", "folder", "modified", "text")},
                         attachment=row.get("attachment", "")))
        if len(out) >= limit:
            break
    return out


def _readable(rows: list[dict]) -> list[dict]:
    """The index can be a week older than the user's choices. A note they have
    since ignored must not surface just because it was embedded earlier."""
    from . import library

    lib = library.load()
    return [r for r in rows if not lib.hides(r["note_id"], r.get("modified", ""))]


def exists() -> bool:
    return CACHE.exists()


def glimpses(chars: int = 100, *, keep_lines: bool = False) -> dict[str, str]:
    """The opening of every indexed note, by note id — enough for a model to
    tell what a note is about without reading it. No index, no glimpses.

    Line breaks are collapsed by default: a prompt wants the words, not the
    layout. `keep_lines` is for the one caller that wants the opposite —
    `library.suggest` judges a note by its *shape*, and a flattened note is one
    long line, so with the default it scored every note in the user's library at
    zero for shape and suggested no homes at all."""
    out: dict[str, str] = {}
    try:
        for note_id, chunks in _load().items():
            if chunks and chunks[0].get("text"):
                text = chunks[0]["text"]
                if keep_lines:
                    text = "\n".join(" ".join(l.split()) for l in text.split("\n"))
                else:
                    text = " ".join(text.split())
                out[note_id] = text[:chars]
    except (OSError, ValueError):
        pass
    return out
