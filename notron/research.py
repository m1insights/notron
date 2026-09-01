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


def check_url(url: str, *, timeout: int = 5) -> bool:
    """Does this URL actually resolve?

    The writer is told to only emit links it was given, but a model told not
    to invent links still occasionally does — seen live as a DOI one digit off
    from the real paper's, sitting next to a correct one and looking exactly
    as trustworthy. HEAD is enough: the question is "does this page exist",
    not what it says.
    """
    request = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "notron/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status < 400
    except urllib.error.HTTPError as e:
        # 405 means the server refuses HEAD, which still proves the page exists.
        return e.code == 405
    except Exception:
        return False


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


# Domain tiers for ranking findings. Suffix-matched, so subdomains count.
# Tier 1: peer-reviewed journals and study databases. Tier 2: institutions a
# pharmacist would accept. Everything else is tier 3 and yields to better.
JOURNALS = (
    "ncbi.nlm.nih.gov", "doi.org", "sciencedirect.com", "springer.com",
    "nature.com", "nejm.org", "thelancet.com", "jamanetwork.com", "bmj.com",
    "cochranelibrary.com", "mdpi.com", "tandfonline.com", "wiley.com",
    "oup.com", "academic.oup.com", "cambridge.org", "frontiersin.org",
    "europeanurology.com", "clinicaltrials.gov",
)
INSTITUTIONS = (
    "nih.gov", "medlineplus.gov", "fda.gov", "who.int", "cdc.gov",
    "mayoclinic.org", "clevelandclinic.org", "health.harvard.edu",
    "hopkinsmedicine.org", "examine.com", "lpi.oregonstate.edu",
)


def quality(url: str) -> int:
    """1 = journal/study database, 2 = trusted institution, 3 = the rest."""
    from urllib.parse import urlparse

    try:
        host = (urlparse(url).netloc or "").lower().lstrip("www.")
    except ValueError:
        return 3
    for domains, tier in ((JOURNALS, 1), (INSTITUTIONS, 2)):
        if any(host == d or host.endswith("." + d) for d in domains):
            return tier
    return 3
