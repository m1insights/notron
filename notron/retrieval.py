"""Finding the right notes out of hundreds.

Phase 3 will replace the scorer with embeddings served from Nebius; the search
contract here stays identical so no node has to change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import markup, notes, workspace

STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "my",
    "me", "i", "is", "are", "was", "what", "when", "how", "do", "did", "you",
    "notron", "about", "that", "this", "it", "at", "be", "have", "has",
}


@dataclass(frozen=True)
class Hit:
    title: str
    folder: str
    excerpt: str
    score: float


def _terms(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP and len(w) > 2]


def search(query: str, *, limit: int = 12, excerpt_chars: int = 900) -> list[Hit]:
    terms = _terms(query)
    if not terms:
        return []

    from . import library

    candidates = library.user_notes()

    # Cheap pass: score titles, so we only pay to read the bodies that matter.
    scored = []
    for n in candidates:
        title_terms = set(_terms(n.title))
        score = sum(2.0 for t in terms if t in title_terms)
        scored.append((score, n))

    scored.sort(key=lambda p: (-p[0], p[1].title))
    shortlist = [n for s, n in scored if s > 0][:limit]
    if len(shortlist) < limit:
        shortlist += [n for s, n in scored if s == 0][: limit - len(shortlist)]

    hits = []
    for n in shortlist:
        text = markup.to_text(notes.read_body(n.id))
        body_terms = _terms(text)
        body_set = set(body_terms)
        score = sum(2.0 for t in terms if t in set(_terms(n.title)))
        score += sum(1.0 for t in terms if t in body_set)
        if score <= 0:
            continue
        hits.append(Hit(n.title, n.folder, text[:excerpt_chars], score))

    hits.sort(key=lambda h: -h.score)
    return hits[:limit]
