"""The nodes of NOTRON's graph. Each is a plain function State -> State.

Node design follows one rule: the cheapest model that can do the job does it.
Routing and guarding happen on every wake-up, so they run on Nemotron Nano;
planning and writing are rare and quality-critical, so they run on Super.
"""

from __future__ import annotations

import re
from datetime import datetime

from . import conversation, markup, notes, privacy, workspace
from .executor import Executor
from .state import Action, State, Write

INTENTS = ("question", "task", "capture", "plan", "remind", "schedule", "file", "ignore")

# "file this: …", "file my brain dump", "sort these" — said in plain words, so
# the Filer is chosen in code and the model is never asked. A tag that says
# "file" must file, every time, not nine times in ten.
FILE_WORDS = re.compile(r"(?i)^\s*(?:please\s+)?(?:file\b|sort\s+(?:this|these|that|it|them)\b)"
                        r"|\bbrain\s*dump\b")


# ---------------------------------------------------------------- Watcher

def watcher(state: State, *, brain=None) -> State:
    """No model. Reads the user's standing instructions and long-term memory."""
    for title, attr in ((workspace.ABOUT, "about"), (workspace.MEMORY, "memory"),
                        (workspace.LESSONS, "lessons")):
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
- remind: they want a reminder set, or an existing one ticked off
- schedule: they want something put in their calendar
- file: they want a line, or their brain dump, sorted into the right one of their own notes
- ignore: not addressed to the assistant

Reply with JSON only:
{"intent": "...", "needs_context": true|false, "needs_web": true|false, "why": "under 12 words"}
needs_context is true ONLY when the answer depends on something the user themselves
wrote down — their plans, their decisions, their deadlines, their people.
needs_context is FALSE for questions about the world: books, authors, ideas, how
something works, your opinion. Their notes are not evidence about those things, and
pulling them in makes the answer wrong.
needs_web is true whenever a good answer would state specific checkable facts —
studies, doses, drug or supplement effects, products, prices, news, anything
current. When in doubt, true: a search is cheap, and a citation from memory may
be misremembered or invented."""


def router(state: State, *, brain) -> State:
    if not state.request.strip():
        state.intent = "ignore"
        state.note("router", "nothing to do")
        return state
    if FILE_WORDS.search(state.request):
        state.intent = "file"
        state.note("router", "file — said so in plain words, no model asked")
        return state
    try:
        out = brain.ask_json(system=ROUTER_SYSTEM, user=state.request, tier="fast", max_tokens=400)
    except Exception as e:
        # A router that cannot classify must never stop Notron answering. Assume the
        # most useful intent and pay for the context.
        state.intent, state.needs_context = "question", True
        state.note("router", f"fell back to question ({type(e).__name__})")
        return state
    state.intent = out.get("intent", "question")
    if state.intent not in INTENTS:
        state.intent = "question"
    if state.intent == "ignore" and state.trigger in ("notes", "manual"):
        # Everything that reaches the router today was said *to* her — typed in
        # the Ask note, tagged #notron, or given on the command line. A router
        # that answers "not addressed to the assistant" is wrong by construction,
        # and the silence it causes is worse than a redundant answer: the note
        # stays unanswered, so the listener asks the model again forever.
        state.intent = "question"
        state.note("router", "overrode ignore — this surface is always addressed to her")
    state.needs_context = bool(out.get("needs_context"))
    state.needs_web = bool(out.get("needs_web"))
    if state.intent == "question" and not state.needs_context and not state.needs_web:
        # The router once tagged "caffeine and theanine together?" as general
        # knowledge and skipped the researcher; the writer then cited two papers
        # from training memory, and whether they were real was luck. A world
        # question answered from neither notes nor web is exactly where
        # fabricated citations come from, so it always gets a search.
        state.needs_web = True
        state.note("router", "world question — searching so any citation is real")
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
    state.note("retriever", f"{len(hits)} notes (keyword — run `notron index`)")
    return state


# ------------------------------------------------------------- Researcher

def researcher(state: State, *, brain=None) -> State:
    """Search the web, but only when the answer cannot come from anywhere else."""
    if not state.needs_web:
        return state

    from . import research

    if not research.available():
        state.note("researcher", "no web access — add a Tavily key to .env")
        return state

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


# ------------------------------------------------------------------ Agenda

SCHEDULING = ("plan", "remind", "schedule")


def _agenda_text() -> str:
    """Today's calendar and what is still outstanding. Two app reads, no model."""
    from . import calendar, reminders

    return (f"## In your calendar today\n{calendar.brief()}\n\n"
            f"## Still outstanding in Reminders\n{reminders.summary()}")


def agenda(state: State, *, brain=None) -> State:
    """No model. Reads the real day, but only when the request is about the day.

    This is deliberately not in `watcher`: the watcher runs on every wake-up of the
    listener, and firing a Calendar query every few seconds would wedge the very
    app the plan depends on.
    """
    if state.intent not in SCHEDULING:
        return state
    try:
        state.agenda = _agenda_text()
    except Exception as e:
        # Automation approval can be revoked at any time, and Calendar hangs rather
        # than failing when it is. Losing context is survivable; losing the morning
        # routine is not.
        state.note("agenda", f"could not read calendar or reminders ({type(e).__name__})")
        return state
    state.note("agenda", f"{len(state.agenda)} chars of real commitments")
    return state


# --------------------------------------------------------------- Scheduler

SCHEDULER_SYSTEM = """You extract one scheduling action from what someone said to their assistant.

Reply with JSON only:
{"kind": "reminder"|"event", "op": "create"|"complete", "title": "...",
 "when": "YYYY-MM-DDTHH:MM" or "YYYY-MM-DD" or null,
 "ends": "YYYY-MM-DDTHH:MM" or null, "where": "", "notes": ""}

- kind is "event" only when they clearly mean their calendar: a meeting, an
  appointment, something with other people or a fixed slot. Otherwise "reminder".
- op is "complete" when they are telling you something is already done.
- title is the task itself, in their words, with no "remind me to" in front of it.
- when: resolve relative dates against today's date, which you are given. If they
  gave no time of day, give the date only. Never invent a time they did not ask for.
- Output nothing but the JSON object."""


def scheduler(state: State, *, brain) -> State:
    """Turns English into one structured Action. The only new model call, on Nano."""
    if state.intent not in ("remind", "schedule"):
        return state
    try:
        out = brain.ask_json(system=SCHEDULER_SYSTEM, user=_prompt(state),
                             tier="fast", max_tokens=400)
    except Exception as e:
        state.note("scheduler", f"could not read that as a date ({type(e).__name__})")
        state.answer = ("I couldn't work out the date from that. "
                        "Say it as a day and a time and I'll set it.")
        return state

    action = Action(
        kind=out.get("kind") or ("event" if state.intent == "schedule" else "reminder"),
        op=out.get("op") or "create",
        title=(out.get("title") or "").strip(),
        when=out.get("when"),
        ends=out.get("ends"),
        where=out.get("where") or "",
        notes=out.get("notes") or "",
    )
    state.actions.append(action)
    state.note("scheduler", f"{action.op} {action.kind}: {action.title}")
    return state


# --------------------------------------------------------------------- Doer

def doer(state: State, *, brain=None, dry_run: bool = False) -> State:
    """No model. Applies the actions, then writes the truth into state.answer.

    This runs before the writer on purpose. If the writer went first she could
    announce a reminder the Guard was about to refuse.
    """
    if not state.actions:
        return state
    ex = Executor(dry_run=dry_run)
    done: list[str] = []
    for a in state.actions:
        r = ex.do(a, about=state.about, request=state.request)
        state.results.append(f"{'✓' if r.ok else '✗'} {a.kind} — {r.reason}")
        done.append(_confirmation(a, r))
    state.answer = "\n\n".join(done)
    state.note("doer", f"{len(state.actions)} actions")
    return state


def _confirmation(action, result) -> str:
    """Plain, specific, and always says the weekday out loud — a wrong date has to
    be obvious at a glance, not discovered on the day."""
    from . import when as when_mod

    if not result.ok:
        return f"I didn't set that. {result.reason}"
    if action.op == "complete":
        return f"Ticked off: {action.title}"
    moment = when_mod.parse(action.when)
    word = "In your calendar" if action.kind == "event" else "Reminder set"
    if moment is None:
        return f"{word}: {action.title}"
    return f"{word}: {action.title} — {when_mod.human(moment)}"


# ------------------------------------------------------------------ Filer

def filer(state: State, *, brain, dry_run: bool = False) -> State:
    """Sort lines into the user's own notes. Nano chooses a title; the Executor
    copies and ticks behind the Guard. Runs before the writer so the reply
    says what landed, never what was meant to.

    Two shapes: "file my brain dump" (or a bare "file") works the whole dump;
    a tagged line — `@notron file this: …` — files that line where it sits.
    """
    if state.intent != "file":
        return state
    from . import filer as filing

    items, bare = [], []
    if state.reply_to is not None and state.source.strip() and not filing.mentions_dump(state.request):
        title, folder, after = state.reply_to
        items, bare = filing.items_from_turn(state.source, title=title, folder=folder, near=after)

    if items:
        out = filing.file_items(brain, items, bare=bare, dry_run=dry_run,
                                on_step=lambda m: state.note("filer", m))
        title, folder, _ = state.reply_to
        # Every line ticked where it was typed reads as done — the receipt is the
        # reply. Anything left over needs a word under it, or she reads it again.
        if not out.filed or (folder, title) not in out.ticked or out.proposed or out.left:
            state.answer = out.summary()
            state.writes.append(_reply(state))
    else:
        out = filing.run(brain, dry_run=dry_run, on_step=lambda m: state.note("filer", m))
        state.answer = out.summary()
        state.writes.append(_reply(state))
    state.results.extend(out.results)
    state.note("filer", f"{len(out.filed)} filed, {len(out.proposed)} proposed, "
                        f"{len(out.left)} left, {out.model_calls} model call(s)")
    return state


# ---------------------------------------------------------------- Planner

PLANNER_SYSTEM = """You are Notron, a personal assistant living inside the user's Apple Notes.

You are given the user's standing instructions, what you remember about them, and
any relevant notes. Produce the plan they asked for.

Rules:
- The standing instructions override everything. Follow them exactly.
- Write in Markdown: ## headings, - bullets, "- [ ]" for tasks, | tables | when comparing.
- Be concrete. Real times, real days, real actions. Never say "consider" or "maybe".
- Short. This is read on a phone.
- Never invent facts about the user that are not in the material you were given.
- Never repeat a password, PIN, key or account number, even if you can see one.
- You are given their real calendar and their real open reminders. Plan around
  what is already there. Never put work on top of an appointment, and never
  invent a commitment that is not in the list you were given.
- If something is already in Reminders, do not re-list it as a new task. Refer to
  it, or leave it alone."""


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

WRITER_SYSTEM = """You are Notron, a personal assistant living inside the user's Apple Notes.

Answer the user directly, the way a sharp, well-read friend would.

There are two kinds of question and they have different rules:

- **About them** — their plans, decisions, deadlines, the things in their notes.
  Answer only from the material you were given, and name the note a fact came
  from. If it is not there, say so rather than inventing it.
- **About the world** — books, ideas, how something works, what you think of
  something. Answer from what you know. Do not refuse a general question just
  because it is not in their notes; that is not what notes are for. Have a view
  and give it.

When you have searched the web, prefer what you found there over what you
remember, and give the link so they can check it. Say plainly when something is
current information rather than something you already knew.

Citing sources:
- Name a specific study, author, journal, DOI or link ONLY if it appears in the
  material you were given — their notes, or what you found on the web just now.
- Never write a DOI or URL from memory. A remembered link is a guess, and a
  guessed link that happens to work is worse than one that does not.
- Give every link bare, as plain text. Notes strips link markup, so [text](url)
  and 【…】 wrappers come out as noise.
- State a year, journal or author for a source only if the material you were
  given says it. If the snippet does not name a year, give none.
- Given nothing from the web, keep claims general and say plainly they are from
  memory and unverified — above all for doses, interactions and health effects.

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
    if state.intent in ("ignore", "plan", "file"):
        return state       # the planner and the filer have already said their piece
    if state.intent in ("remind", "schedule"):
        # The doer already said exactly what happened. Paying a smart model to
        # rephrase a fact would only give it room to get the fact wrong.
        state.writes.append(_reply(state))
        state.note("writer", "confirmed without a model call")
        return state
    if state.intent == "capture":
        fact = state.request.strip()
        # A capture used to append unconditionally, so saying the same thing
        # twice (or the graph running twice on one message) piled up identical
        # bullets in Memory — eleven copies of one fact, once, in production.
        if fact and fact.lower() in state.memory.lower():
            state.answer = "Already noted."
            state.note("writer", "capture skipped — already in memory")
        else:
            state.writes.append(
                Write(title=workspace.MEMORY, markdown=f"\n- {fact}\n", mode="append")
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
        _verify_links(state)
    state.writes.append(_reply(state))
    state.note("writer", f"{len(state.answer)} chars")
    return state


_URL = None  # compiled lazily so `import nodes` stays cheap


def _verify_links(state: State) -> None:
    """Drop any link the model produced from memory that does not resolve.

    Links that came in with the research or the user's notes are trusted —
    Tavily returned them seconds ago, and a second HEAD request per link would
    double the wait for nothing. Everything else is a link the model wrote
    itself, and one of those has already shipped a fabricated DOI to a
    pharmacist. Unverifiable is treated exactly like dead: only a link that
    answered survives.
    """
    global _URL
    import re

    from . import research

    if _URL is None:
        # Printable ASCII only: the model wraps links in 【…】 markers, and a
        # CJK bracket read as part of the URL once made every real citation in
        # an answer look dead — the checker 404'd on `…/research】` four times.
        _URL = re.compile(r"https?://[!-~]+")
    known = "\n".join(state.web + state.context) + state.here + state.request
    seen: list[str] = []
    for match in _URL.findall(state.answer):
        url = match.rstrip(".,;:!?\"')]>")
        if url in seen or url in known:
            continue
        seen.append(url)
        if len(seen) > 5:  # bound the wait; five checks is already 25s worst case
            break
        try:
            alive = research.check_url(url)
        except Exception:
            alive = False
        if not alive:
            gone = "(link removed — it didn't work when I checked)"
            wrapped = f"【{url}】"
            target = wrapped if wrapped in state.answer else url
            state.answer = state.answer.replace(target, gone)
            state.note("writer", f"dropped a dead link: {url}")


def _reply(state: State) -> Write:
    """Her turn, set as a different voice — see `conversation.turn`."""
    body = conversation.turn(state.answer)
    if state.reply_to is None:
        return Write(title=workspace.ASK, mode="append", markdown=f"\n{body}\n")
    title, folder, after = state.reply_to
    # The last line of the request is the block the reply sits under; the index
    # is only a hint, because the user may still be typing above it.
    anchor = state.request.strip().split("\n")[-1].strip()
    return Write(title=title, folder=folder, mode="insert", after=after,
                 anchor=anchor, markdown=body)


# ---------------------------------------------------------------- Executor

def executor(state: State, *, brain=None, dry_run: bool = False) -> State:
    """Guard runs inside Executor.apply — no write reaches Notes unjudged."""
    ex = Executor(dry_run=dry_run)
    for w in state.writes:
        folder = w.folder or workspace.FOLDER
        if w.mode == "insert":
            r = ex.insert(w.title, w.markdown, after=w.after or 0, folder=folder,
                          anchor=w.anchor)
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
    if state.lessons.strip() and "Nothing learned yet" not in state.lessons:
        parts.append("# Lessons you have taught yourself — follow them unless the "
                     f"standing instructions above say otherwise\n{state.lessons}")
    if state.memory.strip():
        parts.append(f"# What you remember about them\n{state.memory}")
    if state.web:
        parts.append("# What you found on the web just now\n" + "\n\n".join(state.web))
    if state.here:
        parts.append(f"# The note they tagged you in\n{state.here}")
    if state.context:
        parts.append("# Their other relevant notes\n" + "\n\n".join(state.context))
    if state.agenda:
        parts.append(f"# Their real day, from Calendar and Reminders\n{state.agenda}")
    parts.append(f"# Their request\n{state.request}")
    return "\n\n".join(parts)
