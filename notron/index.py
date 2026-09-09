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
QUERY_READ_BUDGET = 8


class SearchResults(list):
    """List compatibility plus explicit omitted/unavailable context evidence."""
    incomplete = False
    truncated = False


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
                if not isinstance(row.get('attachment', ''), str): raise ValueError()
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


def _carried(live: list, on_progress=None) -> dict[str, list]:
    """Files belonging to policy-approved notes, grouped by source ID."""
    from . import attachments
    from .securestore import StorageError
    from .credentials import CredentialUnavailable
    from .policy import PolicyError

    out: dict[str, list] = {}
    for folder in sorted({n.folder for n in live}):
        try:
            out.update(attachments.in_folder(folder))
        except (StorageError, CredentialUnavailable, PolicyError):
            raise
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
    from .securestore import StorageError
    from .credentials import CredentialUnavailable
    from .policy import PolicyError

    rows = []
    for att in atts:
        try:
            text = attachments.as_text(att, brain) if extract else attachments.known_text(att)
        except (StorageError, CredentialUnavailable, PolicyError):
            raise
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

    from . import retention
    retention.reconcile()
    cached = {} if force else _load()
    from . import library, policy

    policy.require_ready()
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
            {(c['attachment'], c['text']) for c in old if c.get('attachment')}
            == {(c.attachment, c.text) for c in att_rows})
        if (old and same_files and old[0].get("modified") == n.modified
                and old[0].get("vector") is not None):
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
        for c in att_rows:
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


def search(query: list[Passage], brain, *, limit: int = 8, read_budget: int = QUERY_READ_BUDGET) -> list[Chunk]:
    import numpy as np

    from . import retention
    retention.reconcile()
    prepare_outbound("embed", query)
    data = _load()
    rows = _readable([r for chunks in data.values() for r in chunks if r.get("vector") is not None])
    if not rows:
        return SearchResults()

    matrix = np.asarray([r["vector"] for r in rows], dtype="float32")
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9
    q = np.array(brain.embed(query)[0], dtype="float32")
    q /= np.linalg.norm(q) + 1e-9

    scores = matrix @ q
    best = np.argsort(-scores)[: limit * 3]

    from . import notes, markup, policy
    from .securestore import StorageError
    from .credentials import CredentialUnavailable
    seen = set()
    out = SearchResults()
    reads = 0
    for i in best:
        row = rows[int(i)]
        nid = row['note_id']
        key = (nid, row.get('attachment', ''))
        if key in seen:
            continue
        seen.add(key)
        if reads >= read_budget:
            out.incomplete = out.truncated = True
            break
        reads += 1
        try:
            live = notes.get_note(nid)
            if live is None or not policy.current().readable(live):
                out.incomplete = True
                continue
            if row.get('attachment'):
                from . import attachments
                current = attachments.on_note(nid, live.modified)
                att = next((a for a in current if a.id == row['attachment']), None)
                if att is None or live.modified != row['modified']:
                    out.incomplete = True
                    continue
                chunk = Chunk(**{k: row[k] for k in ('note_id', 'title', 'folder', 'modified', 'text')},
                              attachment=row['attachment'])
            elif live.modified != row['modified'] or live.title != row['title'] or live.folder != row['folder']:
                text = markup.to_text(notes.read_body(nid))
                check = notes.get_note(nid)
                if check != live or not policy.current().readable(live):
                    out.incomplete = True
                    continue
                text = prepare_outbound('write', [Passage.from_note(text, live)])[0]
                out.truncated |= len(text) > CHUNK_CHARS
                chunk = Chunk(nid, live.title, live.folder, live.modified, text[:CHUNK_CHARS])
            else:
                chunk = Chunk(**{k: row[k] for k in ('note_id', 'title', 'folder', 'modified', 'text')})
            out.append(chunk)
        except (StorageError, CredentialUnavailable, policy.PolicyError):
            raise
        except Exception:
            out.incomplete = True
        if len(out) >= limit:
            out.truncated |= len(seen) < len({r['note_id'] for r in rows})
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
