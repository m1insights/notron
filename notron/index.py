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


def build(brain, *, on_progress=None, force: bool = False) -> dict:
    """Embed every note that is new or has changed since the last run."""
    from . import markup, notes, workspace

    cached = {} if force else _load()
    from . import library

    live = library.user_notes()

    fresh: dict[str, list[dict]] = {}
    pending: list[tuple[str, Chunk]] = []
    reused = 0

    for n in live:
        old = cached.get(n.id)
        if old and old[0].get("modified") == n.modified and old[0].get("row") is not None:
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
        out.append(Chunk(**{k: row[k] for k in ("note_id", "title", "folder", "modified", "text")}))
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


def glimpses(chars: int = 100) -> dict[str, str]:
    """The opening of every indexed note, by note id — enough for a model to
    tell what a note is about without reading it. No index, no glimpses."""
    out: dict[str, str] = {}
    try:
        for note_id, chunks in _load().items():
            if chunks and chunks[0].get("text"):
                out[note_id] = " ".join(chunks[0]["text"].split())[:chars]
    except (OSError, ValueError):
        pass
    return out
