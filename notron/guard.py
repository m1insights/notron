"""The Guard node: the last thing between a model's idea and your notes.

Every proposed write passes through `check`. Nothing else in NOTRON is permitted
to call `notes.write_body` directly. Three guarantees, in plain terms:

  1. Notron can never write to 📌 About Me. Your instructions are yours.
  2. Outside her own folder Notron may only APPEND. She cannot delete or
     rewrite a note you wrote.
  3. No write may carry a password, PIN or key, whatever the model intended.
  4. Every allowed write is logged before it happens.
  5. No write ever lands on a note holding a picture. Apple Notes hands an
     embedded image back as inline base64 and then discards it when the body
     is written again — so a write there deletes the photo, silently, and
     `notedoc.preserves` cannot see it happen because the loss occurs after
     the proof. See `markup.holds_media`.

A fourth mode, `mark`, exists for the Filer: it may put a `✓ ` in front of a
line and a receipt after it, and `notedoc.marks_between` proves that is all it
did. It is an addition like any other — nothing of yours is ever removed.

Guarantee 2 has exactly two carve-outs, both narrow and both deliberate. A
fifth mode, `restore`, is the undo path: it puts back a body this very note
held a moment ago — your words, not the model's — so it needs no rewrite
permission and is exempt both from the checks that prove an append kept what
was there and from guarantee 3's secret scan (that text was already live in
the note; refusing to restore it would leave you stuck with what she wrote
over it). `check` cannot prove that a `restore` body really is what the note
held before — it trusts the caller entirely for that mode, the same way it
trusts every caller's `old_body`. The executor requires `undo.peek` snapshot
identity, matching post-write revision and exact saved body (plus its controlled
receipt) before passing a restore here. And `rewrite_allowed` lets
a `replace` land outside her folder only on a note you have explicitly opted
into rewrite-in-place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from . import markup, notedoc, privacy, when, workspace

MAX_BODY_CHARS = 200_000
MAX_TITLE_CHARS = 300
MAX_AHEAD_DAYS = 400

KINDS = {"reminder": {"create", "complete"}, "event": {"create"}}

# "Never schedule me before 9am" / "nothing before 08:30" / "no meetings before 10 am"
_HOUR = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"
EARLIEST = re.compile(rf"(?i)\b(?:never|no|nothing|don'?t|not)\b[^.\n]{{0,40}}?\bbefore\s+{_HOUR}")
LATEST = re.compile(rf"(?i)\b(?:never|no|nothing|don'?t|not)\b[^.\n]{{0,40}}?\bafter\s+{_HOUR}")


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str
    #: True when trying again can never help — the note holds a picture, and it
    #: will still hold one next time. The listener rests a note that failed for
    #: an ordinary reason and stops asking about one that failed for this,
    #: rather than paying a model every half hour to be refused identically.
    permanent: bool = False

    def __bool__(self) -> bool:
        return self.allowed


ALLOW = Verdict(True, "ok")


def check(*, folder: str, title: str, old_body: str, new_body: str, mode: str,
          rewrite_allowed: bool = False) -> Verdict:
    """Judge one proposed write. `mode` is "replace", "append", "insert",
    "mark" or "restore"; `rewrite_allowed` is the user's opt-in to a `replace`
    outside her folder."""
    if title in workspace.READ_ONLY:
        return Verdict(False, f"{title} is read-only — it is the user's instruction note.")

    if mode not in ("replace", "append", "insert", "mark", "restore"):
        return Verdict(False, f"unknown write mode {mode!r}")

    if not new_body.strip():
        return Verdict(False, "refusing to write an empty body")

    # Before the size check, because a note holding a photo is usually also an
    # enormous one, and "over the 200000 limit" sends the reader looking for a
    # bigger number instead of a deleted picture. Every mode, including
    # `restore` — it is exempt from the preserve checks and the secret scan
    # because it puts back the note's own words, but it is still a full-body
    # write and would still throw the picture away.
    if markup.holds_media(old_body):
        return Verdict(
            False,
            f"{title!r} holds a picture. Apple Notes drops a picture from any "
            "note a script writes to, so writing here would delete it — she "
            "will answer somewhere else instead.",
            permanent=True,
        )

    if len(new_body) > MAX_BODY_CHARS:
        return Verdict(False, f"body is {len(new_body)} chars, over the {MAX_BODY_CHARS} limit")

    outside = folder != workspace.FOLDER
    if outside and mode == "replace" and not rewrite_allowed:
        return Verdict(
            False,
            f"{title!r} is outside {workspace.FOLDER}; Notron may only add to it, never rewrite it.",
        )

    if mode == "append" and old_body and not new_body.startswith(old_body):
        return Verdict(False, "append would not preserve the existing note content")

    added_by_insert: str | None = None
    if mode == "insert" and old_body:
        added_by_insert = notedoc.inserted(old_body, new_body)
        if added_by_insert is None:
            return Verdict(False, "insert would have changed or removed existing text")

    marks: list[str] = []
    if mode == "mark":
        if not old_body:
            return Verdict(False, "cannot mark a note that does not exist")
        found = notedoc.marks_between(old_body, new_body)
        if found is None:
            return Verdict(False, "a mark may only add a ✓ and a receipt to a line — "
                                  "anything else was refused")
        marks = found

    if mode == "append":
        added = new_body[len(old_body):]
    elif mode == "mark":
        added = "".join(marks)
    elif added_by_insert is not None:
        added = added_by_insert
    else:
        added = new_body
    # A restore is exempt: the body going back in was live in this exact note a
    # moment ago, so anything key-shaped in it is the user's own text, already
    # theirs. Refusing it would leave them stuck with the version she wrote.
    # Scanned as text, not as HTML: the pattern for a labelled secret takes the
    # next non-space run as the value, and in a body straight from Notes that
    # run is often a tag. A story bible with a scene tag called MACRO-SECRET
    # became unanswerable because `SECRET</div>` read as "secret: </div>".
    if mode != "restore" and privacy.contains_secret(markup.to_text(added)):
        return Verdict(False, "the text contains something that looks like a password or key")

    if mode == "replace" and title in workspace.SHARED:
        return Verdict(False, f"{title} is shared with the user; append only.")

    return ALLOW


def _hour(match) -> int | None:
    if not match:
        return None
    hour = int(match.group(1))
    meridiem = (match.group(3) or "").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    return hour if 0 <= hour <= 23 else None


def earliest_hour(about: str) -> int | None:
    """The 'nothing before 9am' rule, read out of the user's own words."""
    return _hour(EARLIEST.search(about or ""))


def latest_hour(about: str) -> int | None:
    return _hour(LATEST.search(about or ""))


def check_action(action, *, about: str = "", request: str = "", now: datetime | None = None, timezone_name: str | None = None) -> Verdict:
    """Judge one thing Notron wants to do outside Notes.

    The two hard promises live here. A calendar event may only ever be created —
    the user's instruction note says nothing already in their calendar gets moved
    without asking, and that is enforced in code rather than left to a prompt. A
    reminder may be created or completed, never deleted: 'done' must not silently
    mean 'gone'.
    """
    now = now or datetime.now().astimezone()

    allowed_ops = KINDS.get(action.kind)
    if allowed_ops is None:
        return Verdict(False, f"I don't know how to work with {action.kind!r}.")

    if action.op not in allowed_ops:
        if action.kind == "event":
            return Verdict(False, "I never move or delete anything already in your calendar. "
                                  "I can add something new, or you can change it yourself.")
        return Verdict(False, "I don't delete reminders — I can only tick one off. "
                              "Deleting is yours to do.")

    if not action.title.strip():
        return Verdict(False, "refusing to create something with no name")

    if len(action.title) > MAX_TITLE_CHARS:
        return Verdict(False, f"that title is {len(action.title)} characters — too long to be a task")

    if privacy.contains_secret(f"{action.title}\n{action.notes}"):
        return Verdict(False, "the text contains something that looks like a password or key")

    if action.op == "complete":
        return ALLOW

    # Everything below is about a date, and a model's date is not to be trusted.
    if not action.when:
        return ALLOW if action.kind == "reminder" else Verdict(
            False, "an event needs a date and a time — tell me when and I'll add it")

    moment = when.parse(action.when)
    if moment is None:
        return Verdict(False, f"I couldn't read {action.when!r} as a date. "
                              "Give me a day and a time and I'll set it.")

    from .requests import local_timezone
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(timezone_name or local_timezone())
        moment = when.resolve_local(action.when, zone.key).astimezone(zone)
    except ValueError as exc:
        return Verdict(False, str(exc))
    if now.tzinfo is None:
        now = now.replace(tzinfo=moment.tzinfo)
    past = moment.timestamp() < now.timestamp() if when.has_time(action.when) else moment.date() < now.astimezone(moment.tzinfo).date()
    if past:
        return Verdict(False, f"that lands in the past ({when.human(moment)}) — "
                              "I've probably got the year or the day wrong. Say the date.")

    if moment.timestamp() > now.timestamp() + MAX_AHEAD_DAYS * 86400:
        return Verdict(False, f"that lands on {when.human(moment)}, over a year away. "
                              "That is usually a typo. Say the date and I'll set it.")

    # If they named a weekday, the date has to actually be that weekday. This is
    # the failure that would embarrass her most: a confident reminder, wrong day.
    named = when.weekday_named(request or "")
    if named is not None and moment.weekday() != named:
        said = when.WEEKDAYS[named].capitalize()
        return Verdict(False, f"you said {said}, but I worked that out as {when.human(moment)}. "
                              "Say the date and I'll set it.")

    if when.has_time(action.when):
        floor = earliest_hour(about)
        if floor is not None and moment.hour < floor:
            return Verdict(False, f"that's {moment:%H:%M}, and you asked me never to schedule "
                                  f"anything before {floor:02d}:00.")
        ceiling = latest_hour(about)
        if ceiling is not None and moment.hour >= ceiling:
            return Verdict(False, f"that's {moment:%H:%M}, and you asked me never to schedule "
                                  f"anything after {ceiling:02d}:00.")

    return ALLOW
