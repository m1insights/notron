"""Looking things up on the web, when the answer cannot be in your notes.

Notron knows what her model knows and what you have written down. Neither covers
what happened this morning, what a thing costs today, or whether a restaurant is
open. For those she searches — but only when the router says the question
actually needs it, because a search is slow and everything else is not.

Tavily is used because it returns short, already-extracted answers rather than a
page of blue links, which is what a model can actually use.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

ENDPOINT = "https://api.tavily.com/search"
TIMEOUT = 20


class NoSearchKey(RuntimeError):
    """No Tavily key configured. Notron still answers, just without the web."""


@dataclass(frozen=True)
class Finding:
    title: str
    url: str
    snippet: str

    def as_context(self) -> str:
        return f"### {self.title}\n{self.snippet}\n{self.url}"


def available() -> bool:
    return bool(os.environ.get("TAVILY_API_KEY", "").strip())


def search(query: str, *, limit: int = 5, depth: str = "basic") -> tuple[str, list[Finding]]:
    """Return Tavily's own summary answer plus the sources behind it."""
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not key:
        raise NoSearchKey("TAVILY_API_KEY is not set")

    payload = json.dumps({
        "api_key": key,
        "query": query,
        "max_results": limit,
        "search_depth": depth,
        "include_answer": True,
    }).encode()

    request = urllib.request.Request(
        ENDPOINT, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        data = json.loads(response.read())

    findings = [
        Finding(
            title=r.get("title", "")[:120],
            url=r.get("url", ""),
            snippet=(r.get("content") or "")[:800],
        )
        for r in data.get("results", [])
    ]
    return (data.get("answer") or "").strip(), findings
