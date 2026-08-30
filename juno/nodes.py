"""The nodes of JUNO's graph. Each is a plain function State -> State.

Node design follows one rule: the cheapest model that can do the job does it.
Routing and guarding happen on every wake-up, so they run on Nemotron Nano;
planning and writing are rare and quality-critical, so they run on Super.
"""

from __future__ import annotations

from datetime import datetime

from . import markup, notes, privacy, workspace
from .executor import Executor
from .state import State, Write

INTENTS = ("question", "task", "capture", "plan", "ignore")


# ---------------------------------------------------------------- Watcher

def watcher(state: State, *, brain=None) -> State:
    """No model. Reads the user's standing instructions and long-term memory."""
    for title, attr in ((workspace.ABOUT, "about"), (workspace.MEMORY, "memory")):
        n = notes.find_note(workspace.FOLDER, title)
        if n:
            setattr(state, attr, markup.to_text(notes.read_body(n.id)))
    state.note("watcher", f"loaded {len(state.about)} chars of instructions")
    return state


# ----------------------------------------------------------------- Router

ROUTER_SYSTEM = """You route requests for a personal assistant that lives in the user's Apple Notes.
Classify the request into exactly one intent:
- question: they want an answer, possibly from their own notes
- task: they want something added, ticked off, or changed in their notes
- capture: they dumped a thought to be filed, no reply needed
- plan: they want a day or week planned
- ignore: not addressed to the assistant

Reply with JSON only:
{"intent": "...", "needs_context": true|false, "needs_web": true|false, "why": "under 12 words"}
needs_context is true ONLY when the answer depends on something the user themselves
wrote down — their plans, their decisions, their deadlines, their people.
needs_context is FALSE for questions about the world: books, authors, ideas, how
something works, your opinion. Their notes are not evidence about those things, and
pulling them in makes the answer wrong.
needs_web is true only when it requires current information from the internet."""


def router(state: State, *, brain) -> State:
    if not state.request.strip():
        state.intent = "ignore"
        state.note("router", "nothing to do")
        return state
    try:
        out = brain.ask_json(system=ROUTER_SYSTEM, user=state.request, tier="fast", max_tokens=400)
    except Exception as e:
        # A router that cannot classify must never stop Juno answering. Assume the
        # most useful intent and pay for the context.
        state.intent, state.needs_context = "question", True
        state.note("router", f"fell back to question ({type(e).__name__})")
        return state
    state.intent = out.get("intent", "question")
    if state.intent not in INTENTS:
        state.intent = "question"
    state.needs_context = bool(out.get("needs_context"))
    state.needs_web = bool(out.get("needs_web"))
    state.note("router", f"{state.intent} — {out.get('why', '')}")
    return state


# -------------------------------------------------------------- Retriever

def retriever(state: State, *, brain=None, limit: int = 12) -> State:
    """Pull the user's most relevant notes. Keyword match over titles for now;
    Phase 3 swaps in embeddings without changing this node's contract."""
    if not state.needs_context:
        return state
    from . import index

    if index.exists():
        chunks = index.search(state.request, brain, limit=limit)
        raw = [(c.title, f"### {c.title} ({c.folder})\n{c.text}") for c in chunks]
        safe = privacy.filter_passages(state.request, raw)
        state.context = [text for _, text in safe]
        dropped = len(raw) - len(safe)
        state.note("retriever", f"{len(safe)} passages (semantic)"
                                + (f", {dropped} withheld as private" if dropped else ""))
        return state

    from .retrieval import search

    hits = search(state.request, limit=limit)
    raw = [(h.title, f"### {h.title} ({h.folder})\n{h.excerpt}") for h in hits]
    state.context = [t for _, t in privacy.filter_passages(state.request, raw)]
    state.note("retriever", f"{len(hits)} notes (keyword — run `juno index`)")
    return state


# ---------------------------------------------------------------- Planner

PLANNER_SYSTEM = """You are Juno, a personal assistant living inside the user's Apple Notes.

You are given the user's standing instructions, what you remember about them, and
any relevant notes. Produce the plan they asked for.

Rules:
- The standing instructions override everything. Follow them exactly.
- Write in Markdown: ## headings, - bullets, "- [ ]" for tasks, | tables | when comparing.
- Be concrete. Real times, real days, real actions. Never say "consider" or "maybe".
- Short. This is read on a phone.
- Never invent facts about the user that are not in the material you were given.
- Never repeat a password, PIN, key or account number, even if you can see one."""


def planner(state: State, *, brain) -> State:
    if state.intent != "plan":
        return state
    body = brain.ask(
        system=PLANNER_SYSTEM,
        user=_prompt(state),
        tier="smart",
        max_tokens=1500,
    )
    title = workspace.WEEK if _is_weekly(state.request) else workspace.TODAY
    state.writes.append(Write(title=title, markdown=body, mode="replace"))
    state.answer = body
    state.note("planner", f"drafted {title}")
    return state


# ----------------------------------------------------------------- Writer

WRITER_SYSTEM = """You are Juno, a personal assistant living inside the user's Apple Notes.

Answer the user directly, the way a sharp, well-read friend would.

There are two kinds of question and they have different rules:

- **About them** — their plans, decisions, deadlines, the things in their notes.
  Answer only from the material you were given, and name the note a fact came
  from. If it is not there, say so rather than inventing it.
- **About the world** — books, ideas, how something works, what you think of
  something. Answer from what you know. Do not refuse a general question just
  because it is not in their notes; that is not what notes are for. Have a view
  and give it.

Their notes tell you about *them*. They are never evidence about a book, an author,
or anything else in the world. If a note happens to sit near a topic, that does not
make it a source about that topic — say what you actually know instead.

Never quote a note back word for word unless they asked you about that note. People
keep private things in their notes. Refer to what is there; do not reproduce it.

Rules:
- The standing instructions override everything.
- Answer first, in one or two sentences. Detail after, only if it helps.
- Markdown: ## headings, - bullets, "- [ ]" for tasks, | tables | when comparing.
- Short. This is read on a phone.
- Never repeat a password, PIN, key or account number, even if you can see one."""


def writer(state: State, *, brain) -> State:
    if state.intent in ("ignore", "plan"):
        return state
    if state.intent == "capture":
        state.writes.append(
            Write(title=workspace.MEMORY, markdown=f"\n- {state.request.strip()}\n", mode="append")
        )
        state.answer = "Noted — I'll remember that."
        state.note("writer", "captured to memory")
        # Fall through: a capture still gets a visible reply where it was said.
        # Without one the note looks unanswered forever and she reads it again
        # on every pass.

    else:
        state.answer = brain.ask(
            system=WRITER_SYSTEM, user=_prompt(state), tier="smart", max_tokens=1200
        )
    if state.reply_to is None:
        state.writes.append(Write(
            title=workspace.ASK, mode="append",
            markdown=f"\n**Juno:** {state.answer}\n\n———\n\n",
        ))
    else:
        title, folder, after = state.reply_to
        state.writes.append(Write(
            title=title, folder=folder, mode="insert", after=after,
            markdown=f"**Juno:** {state.answer}\n\n———\n",
        ))
    state.note("writer", f"{len(state.answer)} chars")
    return state


# ---------------------------------------------------------------- Executor

def executor(state: State, *, brain=None, dry_run: bool = False) -> State:
    """Guard runs inside Executor.apply — no write reaches Notes unjudged."""
    ex = Executor(dry_run=dry_run)
    for w in state.writes:
        folder = w.folder or workspace.FOLDER
        if w.mode == "insert":
            r = ex.insert(w.title, w.markdown, after=w.after or 0, folder=folder)
        elif w.mode == "append":
            r = ex.append(w.title, w.markdown, folder=folder)
        else:
            r = ex.replace(w.title, w.markdown, folder=folder)
        state.results.append(f"{'✓' if r.ok else '✗'} {w.title} — {r.reason}")
    state.note("executor", f"{len(state.writes)} writes")
    return state


# ---------------------------------------------------------------- helpers

def _is_weekly(request: str) -> bool:
    return any(w in request.lower() for w in ("week", "weekly", "next 7", "sunday"))


def _prompt(state: State) -> str:
    parts = [
        f"# Today\n{datetime.now():%A %-d %B %Y}",
        f"# The user's standing instructions\n{state.about or '(none yet)'}",
    ]
    if state.memory.strip():
        parts.append(f"# What you remember about them\n{state.memory}")
    if state.here:
        parts.append(f"# The note they tagged you in\n{state.here}")
    if state.context:
        parts.append("# Their other relevant notes\n" + "\n\n".join(state.context))
    parts.append(f"# Their request\n{state.request}")
    return "\n\n".join(parts)
