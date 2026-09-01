# Fix: Notron fabricates citations when it should search the web

## The problem

Notron answers health/supplement questions with specific-looking academic
citations (author, year, journal, sometimes a DOI link) even when it never
searched the web. Some of these are real. Some are fabricated — wrong DOI,
or an entirely invented paper. The user has no way to tell which from the
note alone, which is dangerous for a pharmacist relying on it for
supplement/medication research.

Root cause is **not** the missing Tavily key (that's now fixed — see below).
It's `router` in `notron/nodes.py` deciding, per-question, whether a
question needs `researcher` (real Tavily web search) at all. When it
decides no, `writer` (`notron/nodes.py:356`, tier `smart` /
Nemotron-3-Super-120B) still generates confident citations from its own
training memory, with no signal to the user that nothing was actually
looked up.

## Evidence

Two real Ask-Notron exchanges, from `.notron/listen.log`:

**1. "What's the deal with Cognizin the nootropic?"**
- `router: question — seeks info on external nootropic, not personal notes`
- `researcher: no web access — add a Tavily key to .env` (key wasn't set yet)
- Writer still produced 2 citations with DOI links:
  - McGlade et al. 2012 — real paper, but the DOI given 404s. Correct DOI:
    `10.4236/fns.2012.36103` (writer had `...33039`).
  - "Alvarez-Ximénez FJ et al. 2014, *Psychopharmacology*" — could not find
    this paper anywhere. Appears fully fabricated.

**2. "What's the deal with caffeine and theanine taken together?"**
- `router: question — general knowledge question` — never even considered
  researcher.
- Writer produced 2 citations (Haskell 2008, Giesbrecht 2010) — both
  **verified real** this time, but purely by luck of what was in the
  model's training data. Also self-contradicted the Giesbrecht year (2010
  in the sources list, 2017 earlier in the same note).

Conclusion: whether a citation is real is currently random — it depends on
whether that specific paper happened to be well-represented in the base
model's training data. The router's classification of "general knowledge"
vs. "needs lookup" is not a reliable gate, and the writer has no concept of
"I didn't actually look this up, so I shouldn't cite specifics."

## Already fixed this session

- Added `TAVILY_API_KEY` to `.env` — confirmed working via a live
  `notron.research.search()` call (real Tavily results returned).
- Confirmed via `.notron/index.json` that the *synqology* recommendation in
  a separate answer (supplement-tracking question) was legitimate — pulled
  from real notes via semantic retrieval, not hallucinated.

## What's still broken / to investigate

1. **Router under-triggers `researcher`.** Any question inviting a citation
   or specific factual claim (dosages, study results, drug interactions)
   should probably route to `researcher` by default, not just ones the
   router happens to tag as "external."
2. **Writer has no "unverified" mode.** Even when `researcher` doesn't run,
   `writer` (`notron/nodes.py:356`, `WRITER_SYSTEM` prompt) should either:
   - refuse to cite specific studies/DOIs when no research context was
     retrieved, or
   - clearly flag such claims as "from memory, unverified" in the note.
3. **No citation validation.** Nothing checks that a DOI/URL the writer
   emits actually resolves before it's written to the note. Even with
   Tavily wired in for *some* questions, a determined hallucination could
   still slip through on the ones that don't route to research.

## Suggested next steps (not yet designed)

- Tighten the router prompt/logic so any question implying a factual claim
  about a substance, study, or health effect always triggers `researcher`.
- Add a rule to `WRITER_SYSTEM` (`notron/nodes.py`) barring specific
  citations (author/year/DOI) unless research passages are present in
  state.
- Optionally: a lightweight post-write check that HEAD-requests any URL in
  the answer and drops/flags dead links before `executor` writes the note.

## Do not

- Don't touch `notron/watch.py` timing (SETTLE/ASK_POLL) — that's a
  separate, already-discussed tradeoff, unrelated to this issue.
