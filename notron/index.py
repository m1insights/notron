"""The semantic index over every note you own.

Keyword search fails the moment you phrase a question differently from how you
wrote the note — which is most of the time. This turns all your notes into
vectors once, caches them on disk, and re-embeds only the notes that changed.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import asdict, dataclass

from .outbound import Passage, prepare_outbound
from . import privacy

from .paths import DATA_DIR
CACHE = DATA_DIR / "index.json"
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
    from .securestore import read_json, write_json, IntegrityError
    payload = read_json(CACHE)
    if not payload:
        return {}
    if payload.get("outbound_version") != 1 or not isinstance(payload.get("notes"), dict):
        raise IntegrityError('Encrypted index schema invalid; processing paused.')
    data = payload["notes"]
    _validate(data)
    safe = {nid: rows for nid, chunks in data.items() if (rows := _readable(chunks))}
    if safe != data:
        write_json(CACHE, {"outbound_version": 1, "notes": safe})
    return safe



def _validate(data: dict) -> None:
    import math
    from .securestore import IntegrityError
    try:
        dimensions = set()
        for nid, rows in data.items():
            if not isinstance(nid, str) or not nid or not isinstance(rows, list): raise ValueError()
            for row in rows:
                if not isinstance(row, dict) or row.get("note_id") != nid: raise ValueError()
                if not all(isinstance(row.get(k), str) for k in ("text", "title", "folder", "modified")): raise ValueError()
                vector = row.get("vector")
                if vector is not None:
                    if not isinstance(vector, list) or not vector: raise ValueError()
                    if not all(type(v) in (int, float) and math.isfinite(v) for v in vector): raise ValueError()
                    dimensions.add(len(vector))
        if len(dimensions) > 1: raise ValueError()
    except (ValueError, TypeError, OverflowError):
        raise IntegrityError('Encrypted index schema invalid; processing paused.') from None

def _save(data: dict[str, list[dict]]) -> None:
    from .securestore import write_json
    _validate(data)
    safe_data = {}
    for nid, chunks in data.items():
        rows = [dict(c) for c in _readable(chunks)]
        for c in rows:
            c["text"] = prepare_outbound("embed", [_passage(c)])[0]
            c["title"] = privacy.redact(c["title"])
            c["folder"] = privacy.redact(c["folder"])
            c.pop("row", None)
        if rows:
            safe_data[nid] = rows
    write_json(CACHE, {"outbound_version": 1, "notes": safe_data})


# Compatibility name only; never holds a persistent mmap. Vectors are decrypted
# with their metadata on each load and remain in process memory.
_MMAP = None


def build(brain, *, on_progress=None, force: bool = False) -> dict:
    """Embed every note that is new or has changed since the last run."""
    from . import markup, notes, workspace

    from . import retention
    retention.reconcile()
    cached = {} if force else _load()
    from . import library, policy

    policy.require_ready()
    live = library.user_notes()

    fresh: dict[str, list[dict]] = {}
    pending: list[tuple[str, Chunk]] = []
    reused = 0

    for n in live:
        old = cached.get(n.id)
        if old and old[0].get("modified") == n.modified and old[0].get("vector") is not None:
            fresh[n.id] = old
            reused += 1
            continue
        text = prepare_outbound("embed", [Passage.from_note(
            markup.to_text(notes.read_body(n.id)), n)])[0]
        rows = []
        for piece in split(text):
            c = Chunk(n.id, privacy.redact(n.title), privacy.redact(n.folder), n.modified, piece)
            rows.append(asdict(c))
            pending.append((n.id, c))
        fresh[n.id] = rows

    if on_progress:
        on_progress(f"{len(live)} notes · {reused} unchanged · {len(pending)} chunks to embed")

    if pending:
        vectors = brain.embed([_passage(asdict(c)) for _, c in pending])
        by_id: dict[str, list[list[float]]] = {}
        for (nid, _), vec in zip(pending, vectors):
            by_id.setdefault(nid, []).append(vec)
        for nid, vecs in by_id.items():
            for row, vec in zip([r for r in fresh[nid]], vecs):
                row["vector"] = vec

    _save(fresh)
    return {"notes": len(live), "reused": reused, "embedded": len(pending)}


def search(query: list[Passage], brain, *, limit: int = 8) -> list[Chunk]:
    import numpy as np

    from . import retention
    retention.reconcile()
    prepare_outbound("embed", query)
    data = _load()
    rows = _readable([r for chunks in data.values() for r in chunks if r.get("vector") is not None])
    if not rows:
        return []

    matrix = np.asarray([r["vector"] for r in rows], dtype="float32")
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9
    q = np.array(brain.embed(query)[0], dtype="float32")
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
    from .notes import Note
    return [r for r in rows if not lib.is_ignored(
        Note(r["note_id"], r.get("title", ""), r.get("folder", ""), r.get("modified", "")))]


def exists() -> bool:
    return bool(_load())


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
            if chunks and _readable([dict(chunks[0], note_id=note_id)]) and chunks[0].get("text"):
                text = chunks[0]["text"]
                if keep_lines:
                    text = "\n".join(" ".join(l.split()) for l in text.split("\n"))
                else:
                    text = " ".join(text.split())
                out[note_id] = text[:chars]
    except (OSError, ValueError):
        pass
    return out


def _passage(row: dict) -> Passage:
    return Passage(row["text"], "note", row["note_id"], row["title"], row["modified"])
