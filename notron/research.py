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
import time
from dataclasses import dataclass
from typing import Sequence

from .outbound import Passage, prepare_outbound
from . import network

ENDPOINT = network.SEARCH_URL
TIMEOUT = 30


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
    from . import credentials, retention
    retention.require_ready()
    from .transport import configured
    return configured() is not None or credentials.get(credentials.SEARCH_KEY) is not None


def search(passages: Sequence[Passage], *, limit: int = 5, depth: str = "basic") -> tuple[str, list[Finding]]:
    """Return Tavily's own summary answer plus the sources behind it."""
    query = "\n".join(prepare_outbound("search", passages))
    from . import brain
    deadline = time.monotonic() + TIMEOUT
    with brain._deadline_guard(deadline):
        from .transport import configured
        managed=configured()
        if managed is not None:
            data=managed.search(passages,limit,depth,deadline)
        else:
            brain._check_cooldown('tavily')
            from .transport import DirectTransport
            data = DirectTransport(None).search(passages,limit,depth,deadline)

    findings = [
        Finding(
            title=r.get("title", "")[:120],
            url=r.get("url", ""),
            snippet=(r.get("content") or "")[:800],
        )
        for r in data.get("results", [])
    ]
    return (data.get("answer") or "").strip(), findings


def _search(query: str, limit: int, depth: str, deadline: float) -> dict:
    from . import brain
    from . import credentials, retention
    retention.require_ready()
    endpoint = network.provider_endpoint(ENDPOINT, 'tavily')
    secret = credentials.get(endpoint.credential_name)
    key = secret.decode("utf-8") if secret else ""
    if not key:
        raise NoSearchKey("Search credential is not configured")

    payload = json.dumps({
        "api_key": key,
        "query": query,
        "max_results": limit,
        "search_depth": depth,
        "include_answer": True,
    }).encode()

    def request():
        # Rebuild the constrained client so each retry revalidates endpoint,
        # credential and destination at the existing provider boundary.
        with network.provider_client(endpoint) as client:
            response = client.post(
                endpoint.url, content=payload, headers={"Content-Type": "application/json"},
                timeout=brain._remaining(deadline))
            response.raise_for_status()
            return response.json()

    return brain.provider_call(request, deadline, service='tavily')


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
