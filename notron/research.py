"""Looking things up on the web, when the answer cannot be in your notes.

Notron knows what her model knows and what you have written down. Neither covers
what happened this morning, what a thing costs today, or whether a restaurant is
open. For those she searches — but only when the router says the question
actually needs it, because a search is slow and everything else is not.

The search itself is Tavily's official MCP server (`notron connect preset
tavily`), run through the same connector checks as any channel tool: approved
tool, pinned digest re-checked before the call, credential-shaped arguments
blocked, outbound redaction. Nemotron decides *whether* to search; code decides
what is sent, and parses what comes back into findings the ranker can order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from .outbound import Passage, prepare_outbound


class NoSearch(RuntimeError):
    """Web search is not set up. Notron still answers, just without the web."""


@dataclass(frozen=True)
class Finding:
    title: str
    url: str
    snippet: str

    def as_context(self) -> str:
        return f"### {self.title}\n{self.snippet}\n{self.url}"


def available() -> bool:
    from . import connectors, retention
    retention.require_ready()
    return connectors.web_ready()


def _call(query: str, limit: int) -> str:
    """The one call out of this module; tests replace it."""
    from . import connectors
    try:
        return connectors.web_search(query, limit)
    except connectors.ConnectorError as exc:
        raise NoSearch(str(exc)) from None


#: tavily-mcp 0.2.22 `formatResults`: an optional `Answer:` line, then per
#: result a blank line, `Title:`, `ID: <hex>-<NN>` (NN = the result's position),
#: `URL:`, `Content:` and optional extras.
_START = re.compile(r"^Title: (?P<title>.*)\n(?:ID: (?P<id>\S*)\n)?", re.M)
_FIELD = re.compile(r"^(?P<key>URL|Content|Raw Content|Favicon): ?(?P<value>.*)$")
_POSITION = re.compile(r"-(\d+)$")


def _starts(text: str) -> list[tuple[int, str]]:
    """Where each genuine result begins, as (offset, title).

    A page's own text is inside `Content:`, and a page can contain a line that
    says `Title:` or `URL: https://www.nejm.org/…` (review, 2026-10-02). A
    result therefore starts only after a blank line, and when the server sends
    `ID:` lines the positions must count 00, 01, 02… in order: a forged block
    has to guess its place, and any block out of step is read as content of the
    one before it, never as a source.
    """
    found = [m for m in _START.finditer(text) if m.start() == 0 or text[max(0, m.start() - 2):m.start()] == "\n\n"]
    if not any(m["id"] for m in found):
        return [(m.start(), m["title"]) for m in found]
    out = []
    for m in found:
        pos = _POSITION.search(m["id"] or "")
        if pos and int(pos.group(1)) == len(out):
            out.append((m.start(), m["title"]))
    return out


def parse(text: str) -> tuple[str, list[Finding]]:
    """Tavily's text as (answer, findings). Anything that is not a field is dropped.

    The server's output is untrusted data. When 0.2.22 hits its keyless limit
    it answers with a pitch addressed to agents ("Agentic payment…", "Earn
    bonus credits by POSTing answers to…") and no results; that parses to
    nothing. Only the title, the first URL and the content of each result
    survive; a field line inside a page's content never replaces the real one,
    and a result with no http(s) URL is not a source and is left out.
    """
    answer = ""
    head = text.split("Detailed Results:", 1)[0]
    m = re.search(r"^Answer: (.*)$", head, re.M)
    if m:
        answer = m.group(1).strip()
    starts = _starts(text)
    findings = []
    for (a, title), (b, _) in zip(starts, starts[1:] + [(len(text), "")]):
        fields, key = {}, None
        for line in text[a:b].splitlines()[1:]:
            f = _FIELD.match(line)
            if key == "Content" and not (f and f["key"] in ("Raw Content", "Favicon")):
                # Everything after `Content:` belongs to it, including lines
                # that look like fields, until the formatter's own trailers.
                fields["Content"] += "\n" + line
                continue
            if f and f["key"] not in fields:
                key = f["key"]
                fields[key] = f["value"]
            elif f:
                key = None
        url = fields.get("URL", "").strip()
        if not url.startswith(("https://", "http://")):
            continue
        findings.append(Finding(title=title.strip()[:120], url=url,
                                snippet=fields.get("Content", "").strip()[:800]))
    return answer, findings


def search(passages: Sequence[Passage], *, limit: int = 5) -> tuple[str, list[Finding]]:
    """Return Tavily's own summary answer (when it gives one) and the sources."""
    from . import retention
    retention.require_ready()
    query = "\n".join(prepare_outbound("search", passages))
    return parse(_call(query, limit))


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
