"""The nodes of NOTRON's graph. Each is a plain function State -> State.

Node design follows one rule: the cheapest model that can do the job does it.
Routing and guarding happen on every wake-up, so they run on Nemotron Nano;
planning and writing are rare and quality-critical, so they run on Super.
"""

from __future__ import annotations

from .credentials import CredentialUnavailable
from .securestore import StorageError
from .policy import PolicyError

import re
from datetime import datetime
from dataclasses import replace

from . import conversation, markup, notedoc, notes, privacy, rewrite, undo, workspace, policy
from .executor import Executor, capture_write, revision
from .state import Action, State, Write
from .outbound import Passage

INTENTS = ("question", "task", "capture", "plan", "remind", "schedule", "file",
           "undo", "organize", "ignore")

# "file this: …", "file my brain dump", "sort these" — said in plain words, so
# the Filer is chosen in code and the model is never asked. A tag that says
# "file" must file, every time, not nine times in ten.
FILE_WORDS = re.compile(r"(?i)^\s*(?:please\s+)?(?:file\b|sort\s+(?:this|these|that|it|them)\b)"
                        r"|\bbrain\s*dump\b")

# "undo", "revert that", "clean this up" — the same posture as filing. Whether
# someone said "put it back" is a fact about the words, never a judgment call,
# so it costs no model call and cannot come out right only nine times in ten.
# Anchored the way FILE_WORDS anchors "file" — otherwise "|" splits the whole
# pattern, not just the alternative it sits next to, and "we should revert
# that decision, @notron what do you think" would fire a real, unchecked
# restore on an ordinary opinion question.
UNDO_WORDS = re.compile(r"(?i)^\s*(?:please\s+)?(?:undo\b|revert\s+(?:that|this)\b)")
ORGANIZE_WORDS = re.compile(r"(?i)^\s*(?:please\s+)?(?:organize\s+this\b|"
                            r"clean\s+(?:this|it)\s+up\b|tidy\s+(?:this|it)\s+(?:note\s+)?up\b)")
# The `yes` under her one-line offer to keep a note clean in place. Read here
# rather than sent to the classifier for the same reason the Filer's `yes` is
# read in code: it is one word, and there is nothing to interpret.
CONFIRM_WORDS = re.compile(r"(?i)^\s*(?:yes|yep|yeah|sure|go ahead|do it)\.?\s*$")


# ---------------------------------------------------------------- Watcher

def watcher(state: State, *, brain=None) -> State:
    """No model. Reads the user's standing instructions and long-term memory."""
    for title, attr in ((workspace.ABOUT, "about"), (workspace.MEMORY, "memory"),
                        (workspace.LESSONS, "lessons")):
        n = notes.find_note(workspace.FOLDER, title)
        if n and policy.current().system_role(n.id) == title and policy.current().readable(n):
            body = notes.read_body(n.id)
            text = markup.to_text(body)
            state.write_targets[title] = capture_write(title, note_id=n.id, body=body, mode="append")
            setattr(state, attr, text)
            origin = {"about": "standing", "memory": "memory", "lessons": "lesson"}[attr]
            state.system_sources[attr] = Passage.from_note(text, n, origin)
    _bind_reply(state)
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
    if state.reply_to is not None:
        # Everything below is about *this* note, so it only means anything when
        # the request came from inside one. Said with no note behind it —
        # `notron ask "undo my last change"` — it falls through to the model and
        # gets an ordinary answer, rather than a guess at which note was meant.
        if UNDO_WORDS.search(state.request):
            state.intent = "undo"
            state.note("router", "undo — said so in plain words, no model asked")
            return state
        # Rewriting in place is only ever a question about the user's own
        # notes. `reply_to` is set for 📥 Ask Notron too — the listener answers
        # there the same way it answers a tag anywhere else — and tidying her
        # own inbox note is not what any of this is for, nor is reading a `yes`
        # meant for something else as consent to rewrite it.
        if state.reply_to[1] != workspace.FOLDER and (
                ORGANIZE_WORDS.search(state.request)
                or CONFIRM_WORDS.fullmatch(state.request.strip())):
            state.intent = "organize"
            state.note("router", "organize — said so in plain words, no model asked")
            return state
    try:
        out = brain.ask_json(system=ROUTER_SYSTEM, user=[_request_passage(state)], purpose="route", tier="fast", max_tokens=400)
    except (CredentialUnavailable, StorageError, PolicyError):
        raise
    except Exception as e:
        # A router that cannot classify must never stop Notron answering. Assume the
        # most useful intent and pay for the context.
        state.intent, state.needs_context = "question", True
        state.note("router", f"fell back to question ({type(e).__name__})")
        return state
    state.intent = out.get("intent", "question")
    if state.intent not in INTENTS:
        state.intent = "question"
    if state.intent in ("file", "undo", "organize"):
        # Those two are settled above, in code, from the words themselves. The
        # router prompt never offers them — but a hallucinated one would reach a
        # node that expects a real note behind `state.reply_to`, so it is
        # refused here rather than defended against twice downstream.
        state.intent = "question"
        state.note("router", "the model cannot choose file, undo or organize — answering instead")
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
        chunks = index.search([_request_passage(state)], brain, limit=limit)
        state.context = [Passage(f"### {c.title} ({c.folder})\n{c.text}", "note",
                                 c.note_id, c.title, c.modified) for c in chunks]
        state.note("retriever", f"{len(state.context)} passages (semantic)")
        return state

    from .retrieval import search

    hits = search(state.request, limit=limit)
    state.context = [Passage(f"### {h.title} ({h.folder})\n{h.excerpt}", "note",
                             h.note_id, h.title, h.modified) for h in hits]
    state.note("retriever", f"{len(hits)} notes (keyword — run `notron index`)")
    return state


# ------------------------------------------------------------- Researcher

def researcher(state: State, *, brain=None) -> State:
    """Search the web, but only when the answer cannot come from anywhere else."""
    if not state.needs_web:
        return state

    from . import research

    if not research.available():
        state.note("researcher", "no web access — configure the search credential in Keychain")
        return state

    try:
        answer, findings = research.search([_request_passage(state)], limit=8)
    except (CredentialUnavailable, StorageError, PolicyError):
        raise
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
    except (CredentialUnavailable, StorageError, PolicyError):
        raise
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
        out = brain.ask_json(system=SCHEDULER_SYSTEM, user=_scheduling_prompt(state), purpose="schedule",
                             tier="fast", max_tokens=400)
    except (CredentialUnavailable, StorageError, PolicyError):
        raise
    except Exception as e:
        state.note("scheduler", f"could not read that as a date ({type(e).__name__})")
        state.answer = ("I couldn't work out the date from that. "
                        "Say it as a day and a time and I'll set it.")
        return state

    if (not isinstance(out, dict) or
            (out.get("kind"), out.get("op", "create")) not in
            (("reminder", "create"), ("reminder", "complete"), ("event", "create"))):
        state.answer = "I couldn't turn that into a supported reminder or calendar action."
        state.note("scheduler", "rejected unsupported operation")
        return state
    if any(out.get(field) is not None and not isinstance(out[field], str)
           for field in ("title", "when", "ends", "where", "notes")):
        state.answer = "I couldn't turn that into a supported reminder or calendar action."
        state.note("scheduler", "rejected nontext action fields")
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
        items, bare = filing.items_from_turn(state.source, title=title, folder=folder, near=after,
                                            note_id=state.source_note_id or policy.request_note_id(),
                                            modified=state.source_modified)

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


# ---------------------------------------------------------------- Organizer

ORGANIZER_SYSTEM = """You are Notron, a personal assistant living inside the user's Apple Notes.

You are given one whole note. Reply with that entire note, cleaned and organized,
in Markdown, and nothing else.

Rules:
- Keep every fact. Every thing the note says must still be there afterwards.
- Never invent anything. You are tidying what is there, not adding to it.
- Their words stay their words. Group, order and format them; do not rewrite
  their voice or summarise their thoughts away.
- Markdown: ## headings, - bullets, "- [ ]" for tasks, | tables | when comparing.
- The first line you are given is the note's title. It is kept for you — do not
  repeat it in your reply.
- Leave out the line where they asked you to tidy the note, and anything you
  said back to them. Those are the conversation, not the note.
- Never repeat a password, PIN, key or account number, even if you can see one.
- No preamble, no explanation, no code fence. Just the note."""

#: The phrase that makes her offer recognisable when she reads the note back a
#: poll later. It has to appear verbatim in `ORGANIZE_ASK`, and it is the only
#: thing that tells a `yes` about rewriting from a `yes` about anything else.
ORGANIZE_ASK_MARKER = "keep this note clean in place"
ORGANIZE_ASK = (f"Want me to {ORGANIZE_ASK_MARKER} next time, instead of adding below? "
                "Reply **@notron yes** and I will.")

#: Her receipt for a rewrite. Said once, written into the note once — the
#: answer she reports and the turn the note ends on are the same sentence.
CLEANED = "Cleaned it up."

#: Room for the whole note to come back, not just an answer about it. Every
#: other node asks the model for a reply, so a fixed budget is fine there; here
#: the reply *is* the note, and `brain.ask` cannot tell an answer that finished
#: from one that ran out of room — so a long note under a fixed budget would
#: come back silently missing its tail, and on an opted-in note that lands
#: straight over the original. Roughly two tokens of room per four characters,
#: floored so a short note still has space to think in, capped so a huge one
#: cannot run away.
MIN_CLEAN_TOKENS, MAX_CLEAN_TOKENS = 1500, 8000


def _budget_for(text: str) -> int:
    return max(MIN_CLEAN_TOKENS, min(len(text) // 2, MAX_CLEAN_TOKENS))


def organizer(state: State, *, brain) -> State:
    """The whole note, cleaned. Super, because it is the note itself at stake.

    Outside 🤖 NOTRON she may only add (invariant #2), so by default the cleaned
    version lands *underneath* what the user wrote, exactly like any other
    reply, plus one line offering to keep that note clean in place instead. A
    `yes` typed under that line — tagged, because outside her folder nothing
    untagged is ever read — is the opt-in, and it costs no model call.

    She never applies anything herself: every branch here ends in a proposed
    `Write` for the Guard to judge and the Executor to apply.
    """
    if state.intent != "organize" or state.reply_to is None:
        return state
    title, folder, after = state.reply_to
    nid = state.source_note_id or policy.request_note_id()
    note = notes.get_note(nid) if nid else notes.unique_note(folder, title)
    if note is None:
        state.answer = "I can't find that note any more."
        state.note("organizer", "no such note")
        return state
    if (not policy.current().readable(note) or
            (policy.request_note_id() is not None and policy.request_note_id() != note.id)):
        raise policy.PolicyError('Request note is not readable or its identity changed.')
    body = notes.read_body(note.id)
    target = capture_write(title, folder=folder, note_id=note.id, body=body, mode='replace')
    _bind_reply(state)

    supported = notedoc.supports_replacement(body)
    separate_target = _separate_result_target() if not supported else None
    if CONFIRM_WORDS.fullmatch(state.request.strip()):
        if not supported:
            state.answer = "This note contains unsupported rich content. Keep the original and use a separate plain-text result."
            state.writes.append(replace(separate_target, markdown=conversation.turn(state.answer)))
            return state
        if not _offer_precedes(body, after):
            # A bare `yes` is consent to rewrite a note only directly under the
            # offer to rewrite it — not just anywhere in the note. A later,
            # unrelated question of hers can sit lower in the same note (she
            # asked twice; the offer is old), and a `yes` answering *that* must
            # never be read as consent to something asked earlier. So this
            # checks the turn immediately above the `yes`, not merely the last
            # turn of hers found anywhere — the router matched a word, not a
            # meaning, unless it's the *right* word in the *right* place.
            state.intent = "question"
            state.note("organizer", "a yes, but not to my offer — answering it normally")
            return state
        if not policy.current().can_file(note.id):
            state.answer = 'This note is Read only. Select it as a Home before enabling in-place cleanup.'
            state.writes.append(_reply(state))
            return state
        rewrite.allow(note.id)
        state.answer = "Got it — from now on I'll keep this note clean in place."
        state.writes.append(_reply(state))
        state.note("organizer", f"{title} may be rewritten in place — no model asked")
        return state

    text = markup.to_text(body)
    cleaned = _without_tags(brain.ask(
        system=ORGANIZER_SYSTEM,
        user=[Passage.from_note(text, note)] + _prompt(state),
        purpose="organize",
        tier="smart",
        max_tokens=_budget_for(text),
    ))

    # An empty answer (a retry that still ran out of room — see brain.ask) or
    # an implausibly short one (the model summarised the note away instead of
    # tidying it) must never become the note. This is the one path that can
    # overwrite a user's own words outright — undo can bring the original
    # back, but that must never be the only thing standing between a bad
    # answer and a wiped note.
    if not cleaned.strip() or len(cleaned) < 0.5 * len(_without_tags(text)):
        state.answer = "I couldn't tidy that safely, so I left it alone."
        if supported:
            state.writes.append(_reply(state))
        else:
            state.writes.append(replace(separate_target, markdown=conversation.turn(state.answer)))
        state.note("organizer", "model's answer was empty or too short — refused to write it")
        return state

    if not supported:
        state.answer = (cleaned + "\n\nThis note contains unsupported rich content. "
                        "I kept the original untouched. You can use this separate plain-text result "
                        "in a new note.")
        state.writes.append(replace(separate_target, markdown=conversation.turn(state.answer)))
        state.note("organizer", "unsupported rich content; separate result in Ask")
        return state

    if rewrite.allowed(note.id):
        # One write, not two. Every successful write saves the note's prior body
        # for undo, and there is only one slot per note — a separate reply write
        # straight after this one would overwrite it with the *cleaned* body,
        # and "undo that" would hand back her own version instead of theirs.
        # So her turn rides along inside the rewrite.
        state.writes.append(replace(target, rewrite_allowed=True,
                                  markdown=f"{cleaned}\n\n{conversation.turn(CLEANED)}"))
        state.answer = CLEANED
        state.note("organizer", f"rewrote {title} in place ({len(cleaned)} chars)")
    else:
        permission_help = (ORGANIZE_ASK if policy.current().can_file(note.id) else
                           'This note is Read only, so I added a separate cleaned copy.')
        state.answer = f"{cleaned}\n\n{permission_help}"
        state.writes.append(_reply(state))
        state.note("organizer", f"added a cleaned copy below and asked ({len(cleaned)} chars)")
    return state



def _separate_result_target() -> Write:
    """Capture a safe existing Ask destination before rich-note inference.

    An unavailable or rich Ask produces an unbound Write, so execution records a
    refusal and request admission retains needs_review instead of silent completion.
    No fallback to a title twin or automatic creation is allowed.
    """
    nid = policy.current().system_notes.get(workspace.ASK)
    note = notes.get_note(nid) if nid else None
    if note and policy.current().readable(note):
        body = notes.read_body(note.id)
        if notedoc.supports_replacement(body):
            return capture_write(workspace.ASK, note_id=nid, body=body,
                                 mode='append', rebase_append=False)
    return Write(title=workspace.ASK, folder=workspace.FOLDER, markdown='', mode='append')

def _without_tags(markdown: str) -> str:
    """Drop any line still addressed to her.

    A rewrite replaces the whole note, so a `@notron clean this up` the model
    copied through would sit there unanswered — and the mention scanner would
    hand it straight back on the next poll, and the one after that. The prompt
    asks for it to be left out; this is what makes sure.
    """
    kept = [l for l in markdown.split("\n") if not conversation.TAG.search(l)]
    return "\n".join(kept).strip()


def _offer_precedes(body_html: str, before: int) -> bool:
    """Whether the turn immediately above block `before` — skipping blank
    space, the same way `conversation.unanswered` skips it when deciding a
    turn is answered — is Notron's own, and is the one where she offered to
    keep this note clean in place.

    Not "did she ever offer, anywhere in the note": a note can hold an old
    offer lower down and a newer, unrelated turn of hers above it, and a `yes`
    has to answer the turn it actually sits under, not the first offer found
    by scanning the whole note.
    """
    texts = notedoc.texts(body_html)
    i = before - 1
    while i >= 0 and not texts[i].strip():
        i -= 1
    if i < 0 or texts[i].strip() != conversation.RULE:
        return False
    turn: list[str] = []
    i -= 1
    while i >= 0 and not texts[i].lstrip().startswith(conversation.SIGNATURE):
        turn.append(texts[i])
        i -= 1
    turn.reverse()
    return ORGANIZE_ASK_MARKER in "\n".join(turn)


# ------------------------------------------------------------------- Undoer

def undoer(state: State, *, brain=None) -> State:
    """No model. Puts a note back the way it was before her last write to it.

    One step back, per note (`undo.py`), and the saved copy is consumed as it is
    used — so a second "undo" in a row says there is nothing to undo rather than
    bouncing the note between two versions.
    """
    if state.intent != "undo" or state.reply_to is None:
        return state
    title, folder, _ = state.reply_to
    if title == workspace.ASK:
        # The Ask note is the conversation itself, not a thing that was
        # written — "undo" said here has no note to point at. Design doc:
        # ask which one rather than guess (guessing would mean restoring the
        # Ask note's own body, discarding the whole conversation, while
        # reporting a "done" that has nothing to do with what the user meant).
        state.answer = ("Tag me with @notron undo on the note you want put back — "
                        "from here I can't tell which one.")
        state.writes.append(_reply(state))
        state.note("undoer", "asked in the Ask note — no note named")
        return state
    nid = state.source_note_id or policy.request_note_id()
    note = notes.get_note(nid) if nid else notes.unique_note(folder, title)
    if note is None:
        # No note means nothing to put back — and nowhere to say so either,
        # since `_reply` anchors inside this very note. Popping the slot here
        # would spend the one step back on a write that could never land.
        state.answer = "Nothing to undo here."
        state.note("undoer", "no such note")
        return state

    if not policy.current().readable(note):
        raise policy.PolicyError('Undo target is no longer readable.')
    target = capture_write(title, folder=folder, note_id=note.id, mode='restore')
    _bind_reply(state)
    old = undo.pop(note.id)
    if old:
        state.answer = "Done — put it back the way it was."
        # `markdown` carries the note's own saved HTML here, not Markdown —
        # `Executor.restore` puts it back verbatim (see `state.Write`) — with her
        # one line of receipt rendered onto the end of it. That is deliberate,
        # twice over. It is one write, not two: `restore` saves no undo slot, so
        # a second write would fill the slot with the body she just put back and
        # leave the note one step behind itself. And the receipt has to sit at
        # the *end* of the restored note rather than under the line that asked
        # for the undo, because that line is gone along with everything else she
        # wrote over — what is back is the note as it stood before her last
        # write, and whatever the user had said in it then is unanswered all
        # over again. Without a turn after it the watcher hands it straight back
        # and she redoes the very write that was just undone.
        #
        # A trailing turn only closes the *last* one of those — two tagged asks
        # separated by a real gap (`conversation.MAX_GAP`) are still two turns,
        # and only the final one sits next to what comes after it. So every
        # unanswered tagged turn the restored body holds gets ticked first, the
        # same primitive the Filer already relies on to stop a filed line being
        # re-asked forever (`conversation.unanswered` treats a ticked turn as
        # answered). Outside her folder only — inside it (☀️ Today, the Ask
        # note) nothing is tagged, and re-answering a restored question there is
        # the ordinary, correct thing to do, not a loop to close.
        if folder != workspace.FOLDER:
            # `notedoc.texts` (what `conversation.unanswered` reads) prefixes
            # every list item with "• " for display; `notedoc.find_line` (what
            # `mark_lines` matches against) compares the bare <li> text — the
            # same prefix the Filer already strips for the same reason
            # (filer.py). Without stripping it here, a tagged line inside a
            # bulleted note never matches and never gets ticked.
            #
            # Known remaining gap, not closed here: `notedoc.blocks()` treats
            # a whole <ul> as one block, so a tagged line sharing a list with
            # untagged siblings still reads as unanswered even once ticked —
            # `conversation.unanswered`'s filed-check tests the whole block's
            # text, not the one line inside it. Pre-existing and deeper than
            # this fix (a block-vs-line granularity mismatch between notedoc
            # and conversation.py); see test_the_undoer_ticks_a_tagged_line_
            # written_as_its_own_bullet for what this does and doesn't close.
            marks = [
                (line.strip().removeprefix("• "), q.after, " → put back")
                for q in conversation.unanswered(old, ignore=(title,), require_tag=True)
                for line in q.text.split("\n")
                if conversation.TAG.search(line)
            ]
            old, _ = notedoc.mark_lines(old, marks)
        state.writes.append(replace(target,
            markdown=old + markup.to_html(conversation.turn(state.answer)),
        ))
    else:
        # Nothing moved, so her turn goes under the line that asked, like any
        # other reply. A turn with no visible answer reads as unanswered next
        # pass, and she is asked to undo again, and again.
        state.answer = "Nothing to undo here."
        state.writes.append(_reply(state))
    state.note("undoer", "restored" if old else "nothing saved for this note")
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
    title = workspace.WEEK if _is_weekly(state.request) else workspace.TODAY
    target = capture_write(title, mode="replace")
    body = brain.ask(
        system=PLANNER_SYSTEM,
        user=_prompt(state), purpose="write",
        tier="smart",
        max_tokens=1500,
    )
    title = workspace.WEEK if _is_weekly(state.request) else workspace.TODAY
    state.answer = body
    _verify_links(state)
    state.writes.append(replace(target, markdown=state.answer))
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
    if state.intent in ("ignore", "plan", "file", "undo", "organize"):
        # The planner, the filer, the undoer and the organizer have each already
        # said their piece, where it was asked. A second reply would double up.
        return state
    _bind_reply(state)
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
                replace(state.write_targets.get(workspace.MEMORY) or
                        capture_write(workspace.MEMORY, mode="append"), markdown=f"\n- {fact}\n")
            )
            state.answer = "Noted — I'll remember that."
            state.note("writer", "captured to memory")
        # Fall through: a capture still gets a visible reply where it was said.
        # Without one the note looks unanswered forever and she reads it again
        # on every pass.

    else:
        state.answer = brain.ask(
            system=WRITER_SYSTEM, user=_prompt(state), purpose="write", tier="smart", max_tokens=1200
        )
        _verify_links(state)
    state.writes.append(_reply(state))
    state.note("writer", f"{len(state.answer)} chars")
    return state


def _verify_links(state: State) -> None:
    """Keep exact URLs in prepared source context; never resolve or fetch links.

    Grounding establishes where the citation came from, not factual correctness.
    Model/history/diagnostic passages cannot establish their own source authority.
    Reuse the same preparation and note permissions as the writer transport.
    """
    import re
    from .outbound import prepare_outbound

    pattern = re.compile(r'(?P<open>【)?(?P<url>https?://(?:\[[^\]\s]+\])?[^\s<>"\[\]【】]*)(?P<close>】)?', re.IGNORECASE)
    def clean(url):
        url = url.rstrip(".,;:!?\"'>")
        while url.endswith(')') and url.count(')') > url.count('('):
            url = url[:-1]
        return url
    sources = [p for p in _prompt(state) if p.origin in ('web', 'user_request', 'note')]
    known = {clean(match['url']) for text in prepare_outbound('write', sources)
             for match in pattern.finditer(text)}
    unsupported = set()
    def replace(match):
        raw = match['url']
        url = clean(raw)
        if url in known:
            return match[0]
        unsupported.add(url)
        return '(citation unverifiable — link removed)' + raw[len(url):]
    state.answer = pattern.sub(replace, state.answer)
    if unsupported:
        state.note('writer', f'{len(unsupported)} unsupported links removed')


def _bind_reply(state: State) -> None:
    if 'reply' in state.write_targets:
        return
    if state.reply_to is None:
        state.write_targets['reply'] = capture_write(workspace.ASK, mode='append')
        return
    title, folder, after = state.reply_to
    anchor = (state.source or state.request).strip().split("\n")[-1].strip()
    target = capture_write(title, folder=folder,
                           note_id=state.source_note_id or policy.request_note_id(),
                           mode='insert', after=after, anchor=anchor)
    if state.source_revision:
        target.expected_revision = state.source_revision
    state.write_targets['reply'] = target


def _reply(state: State) -> Write:
    """Use the target captured before inference; never choose a title here."""
    _bind_reply(state)
    body = conversation.turn(state.answer)
    return replace(state.write_targets['reply'], markdown=body)


# ---------------------------------------------------------------- Executor

def executor(state: State, *, brain=None, dry_run: bool = False) -> State:
    """Guard runs inside Executor.apply — no write reaches Notes unjudged."""
    ex = Executor(dry_run=dry_run)
    for w in state.writes:
        r = ex.apply_write(w)
        if r.alternative_text:
            state.answer = r.alternative_text + "\n\n" + r.reason
        elif not r.ok:
            draft = state.answer if state.intent == "plan" else ""
            state.answer = "I could not verify this change: " + r.reason
            if draft:
                state.answer += "\n\nUnsaved draft:\n" + draft
        state.results.append(f"{'✓' if r.ok else '✗'} {w.title} — {r.reason}")
    state.note("executor", f"{len(state.writes)} writes")
    return state


# ---------------------------------------------------------------- helpers

def _is_weekly(request: str) -> bool:
    return any(w in request.lower() for w in ("week", "weekly", "next 7", "sunday"))


def _request_passage(state: State) -> Passage:
    nid = state.source_note_id or policy.request_note_id()
    if (state.trigger == "notes" or state.here or state.reply_to) and not nid:
        raise policy.PolicyError('Note request requires its observed source ID.')
    title = state.reply_to[0] if state.reply_to else ''
    return Passage(state.request, "user_request", nid, title, state.source_modified)


def _prompt(state: State) -> list[Passage]:
    parts = [Passage(f"# Today\n{datetime.now():%A %-d %B %Y}", "diagnostic")]
    for attr, heading in (
        ("about", "The user's standing instructions"),
        ("lessons", "Lessons you have taught yourself — subordinate to standing instructions"),
        ("memory", "What you remember about them"),
    ):
        text = getattr(state, attr)
        if not text.strip() or (attr == "lessons" and "Nothing learned yet" in text):
            continue
        source = state.system_sources.get(attr)
        if source is None:
            raise policy.PolicyError('Standing context requires its observed source ID.')
        from dataclasses import replace
        parts.append(replace(source, text=f"# {heading}\n{text}"))
    parts.extend(Passage(w, "web") for w in state.web)
    request = _request_passage(state)
    if state.here:
        parts.append(Passage(state.here, "note", request.note_id, request.title, request.modified))
    parts.extend(state.context)
    if state.agenda:
        parts.append(Passage(f"# Their real day, from Calendar and Reminders\n{state.agenda}", "agenda"))
    from dataclasses import replace
    parts.append(replace(request, text=f"# Their request\n{request.text}"))
    return parts


def _scheduling_prompt(state: State) -> list[Passage]:
    # Retrieved instructions, lessons and model answers cannot originate an
    # operation. The scheduler extracts only from the current user's request.
    return [Passage(f"# Today\n{datetime.now():%A %-d %B %Y}", "diagnostic"),
            _request_passage(state)]
