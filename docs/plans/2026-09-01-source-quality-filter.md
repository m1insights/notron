# Source-Quality Filter Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** When Notron searches the web, journal/medical sources (PubMed, ScienceDirect, Cleveland Clinic) beat content-farm sites (ubiehealth.com) in what reaches the writer.

**Architecture:** A pure `quality(url) -> 1|2|3` tier function in `notron/research.py` (1 = peer-reviewed journals/databases, 2 = trusted medical institutions, 3 = everything else). The `researcher` node in `notron/nodes.py` fetches 8 Tavily results instead of 5, sorts them best-tier-first (stable — Tavily's relevance order preserved within a tier), drops tier-3 sources whenever ≥2 better ones exist, and passes at most 5 findings on. No model involved; plain code, like the Guard.

**Tech Stack:** Python stdlib only (`urllib.parse`). Tests: pytest, no network, no API key (see `tests/conftest.py` — it strips `TAVILY_API_KEY`).

**Context you don't have:** This repo is Notron (`/Users/m1labs/Dev/apps/juno`), a personal AI agent answering questions inside Apple Notes. A background listener process runs the graph in `notron/graph.py`: `router` decides a question needs the web → `researcher` calls Tavily → `writer` (Nemotron Super via Nebius) answers citing ONLY what research returned (citation-fabrication fixes shipped 2026-09-01, commits 32f7d36…26104a3). Real failure motivating this plan: a Cialis answer cited ubiehealth.com — a thin content site — alongside the actual 2005 European Urology trial. Read `CLAUDE.md` first; obey its invariants.

**Work directly on `main`** (repo convention — solo project, small commits). Run everything from the repo root with `.venv/bin/python`.

---

### Task 1: `research.quality(url)` — the tier function

**Files:**
- Modify: `notron/research.py` (add function at end)
- Test: `tests/test_research.py` (append)

**Step 1: Write the failing tests** — append to `tests/test_research.py`:

```python
def test_journals_outrank_institutions_outrank_content_farms():
    """A Cialis answer once cited ubiehealth.com — a thin AI-content site —
    with the same visual weight as the European Urology trial next to it."""
    assert research.quality("https://pubmed.ncbi.nlm.nih.gov/15661417") == 1
    assert research.quality("https://www.sciencedirect.com/science/article/abs/pii/S03") == 1
    assert research.quality("https://link.springer.com/article/10.1186/x") == 1
    assert research.quality("https://health.clevelandclinic.org/what-is-ashwagandha") == 2
    assert research.quality("https://examine.com/supplements/tongkat-ali/") == 2
    assert research.quality("https://ubiehealth.com/doctors-note/tadalafil") == 3
    assert research.quality("https://random-wellness-blog.io/cialis") == 3


def test_quality_never_raises_on_junk_input():
    assert research.quality("") == 3
    assert research.quality("not a url at all") == 3
```

**Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_research.py -q`
Expected: 2 FAILED with `AttributeError: ... no attribute 'quality'`

**Step 3: Implement** — append to `notron/research.py`:

```python
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
```

Note: `health.harvard.edu` matching requires suffix logic on the full host — `www.health.harvard.edu` ends with `.health.harvard.edu`? No — it equals `health.harvard.edu` after the `lstrip`. Both branches of the `any()` are needed; keep them.

**Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_research.py -q`
Expected: all pass

**Step 5: Commit**

```bash
git add notron/research.py tests/test_research.py
git commit -m "feat: research.quality — tier web sources (journal > institution > rest)"
```

---

### Task 2: researcher prefers better sources

**Files:**
- Modify: `notron/nodes.py` — the `researcher` function (~line 125)
- Test: `tests/test_research.py` (append)

**Step 1: Write the failing tests** — append to `tests/test_research.py`:

```python
def _finding(url):
    return research.Finding(title=url, url=url, snippet="s")


def test_content_farms_are_dropped_when_better_sources_exist(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "x")
    monkeypatch.setattr(research, "search", lambda *a, **k: ("", [
        _finding("https://ubiehealth.com/a"),
        _finding("https://pubmed.ncbi.nlm.nih.gov/1"),
        _finding("https://random-blog.io/b"),
        _finding("https://clevelandclinic.org/c"),
    ]))
    state = State(request="cialis?", needs_web=True)
    nodes.researcher(state)
    joined = "\n".join(state.web)
    assert "pubmed" in joined and "clevelandclinic" in joined
    assert "ubiehealth" not in joined and "random-blog" not in joined
    assert any("dropped" in t for t in state.trace)


def test_a_content_farm_is_kept_when_it_is_most_of_what_came_back(monkeypatch):
    """Filtering must never starve the writer: one journal + one blog is a
    thin answer, not a reason to throw half of it away."""
    monkeypatch.setenv("TAVILY_API_KEY", "x")
    monkeypatch.setattr(research, "search", lambda *a, **k: ("", [
        _finding("https://random-blog.io/only"),
        _finding("https://pubmed.ncbi.nlm.nih.gov/1"),
    ]))
    state = State(request="obscure supplement?", needs_web=True)
    nodes.researcher(state)
    assert "random-blog" in "\n".join(state.web)


def test_journals_come_first_and_at_most_five_findings_pass(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "x")
    monkeypatch.setattr(research, "search", lambda *a, **k: ("", [
        _finding(f"https://blog{i}.io/x") for i in range(4)
    ] + [
        _finding("https://pubmed.ncbi.nlm.nih.gov/1"),
        _finding("https://sciencedirect.com/2"),
        _finding("https://examine.com/3"),
    ]))
    state = State(request="cialis?", needs_web=True)
    nodes.researcher(state)
    findings = [w for w in state.web if not w.startswith("### What the web says")]
    assert len(findings) <= 5
    assert "pubmed" in findings[0] or "sciencedirect" in findings[0]
```

**Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_research.py -q`
Expected: the 3 new tests FAIL (findings unfiltered/unsorted)

**Step 3: Implement** — in `notron/nodes.py`, replace the body of `researcher` from `try:` down to the final `return state` with:

```python
    try:
        answer, findings = research.search(state.request, limit=8)
    except Exception as e:
        # A failed search should cost the user an answer, not the whole reply.
        state.note("researcher", f"search failed ({type(e).__name__}) — answering without it")
        return state

    # Journals first, content farms last — and dropped entirely when at least
    # two better sources came back. A Cialis answer once cited a thin
    # AI-content site with the same weight as the European Urology trial
    # beside it; ranking is plain code, like the Guard, because the writer
    # cites whatever it is handed.
    findings.sort(key=lambda f: research.quality(f.url))  # stable: Tavily order kept per tier
    good = [f for f in findings if research.quality(f.url) < 3]
    kept = good if len(good) >= 2 else findings
    dropped = len(findings) - len(kept)
    kept = kept[:5]

    if answer:
        state.web.append(f"### What the web says\n{answer}")
    state.web.extend(f.as_context() for f in kept)
    state.note("researcher", f"{len(kept)} sources"
                             + (f" ({dropped} low-quality dropped)" if dropped > 0 else ""))
    return state
```

Also delete the now-unused `limit: int = 5` parameter from `researcher`'s signature — search breadth is fixed at 8 here. Check callers first: `rg "researcher\(" notron tests` — the graph calls it via `NODES` with no limit arg, tests call `nodes.researcher(state)`.

**Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: all pass (~183). If `test_findings_reach_the_writer_as_context` fails, its fake returns one example.com finding — 1 finding, 0 good → `kept = findings`, so it should still pass; investigate before touching it.

**Step 5: Commit**

```bash
git add notron/nodes.py tests/test_research.py
git commit -m "feat: researcher ranks sources — journals first, content farms dropped"
```

---

### Task 3: prove it live, then restart the listener

**Step 1: Live dry-run** (uses real Nebius + Tavily keys from `.env`, writes nothing):

Run: `.venv/bin/python -m notron ask "Any evidence to support Cialis improving endothelial function?" --dry-run`
Expected: trace shows `researcher: N sources (M low-quality dropped)` or all-good sources; cited links in the answer are journal/institution domains. If ubiehealth.com still appears, the filter did not run — stop and debug.

**Step 2: Restart the background listener** — it runs old code until restarted (a whole bug-hunt was once wasted on this):

```bash
launchctl bootout gui/$(id -u)/io.m1labs.notron.listen
sleep 2
.venv/bin/python -m notron listen --install
launchctl list | grep io.m1labs.notron.listen   # expect a PID
```

**Step 3: Report** — tell the user tests are green, show the dry-run's source domains, and give them the 1-minute check: ask a supplement question in Apple Notes → 📥 Ask Notron and confirm citations come from journals/institutions.
