"""The Reminders app — real checkboxes, real notifications, real phone sync.

This exists because Apple Notes cannot be given a tappable checkbox by script.
Juno writes `☐` and `✅` as plain text into notes; Reminders is where a task can
actually buzz.

The Notes performance rules apply here with one refinement worth understanding:
**what costs time is Apple events, not AppleScript loops.** Asking for
`name of every reminder of l` is one event for the whole list; asking each
reminder for its name in turn is one event each. So we fetch every property in
bulk, then loop over the values already sitting in AppleScript memory to tidy
them — which is free, and necessary, because `due date` comes back as
`missing value` for undated reminders and a list containing `missing value`
cannot be coerced to text.

Reminders is its own single-threaded script target and needs its own macOS
Automation approval — see `juno permissions`.
"""

from __future__ import annotations

from dataclasses import dataclass

from .applescript import run

COLD_START_TIMEOUT = 90
MAX_IN_PROMPT = 25

RS = "\x1e"
US = "\x1f"

_ISO = f"""
on iso(d)
  if d is missing value then return ""
  set y to text -4 thru -1 of ("0000" & (year of d))
  set m to text -2 thru -1 of ("00" & ((month of d) as integer))
  set dy to text -2 thru -1 of ("00" & (day of d))
  set h to text -2 thru -1 of ("00" & (hours of d))
  set mi to text -2 thru -1 of ("00" & (minutes of d))
  return y & "-" & m & "-" & dy & "T" & h & ":" & mi
end iso
"""

_OPEN = _ISO + f"""
on run argv
  set rows to {{}}
  tell application "Reminders"
    repeat with l in lists
      set ln to name of l
      set rs to (every reminder of l whose completed is false)
      if (count of rs) > 0 then
        set ids to id of rs
        set nms to name of rs
        set dds to due date of rs
        repeat with k from 1 to count of ids
          set end of rows to ln & "{RS}" & (item k of ids) & "{RS}" & (item k of nms) & "{RS}" & (my iso(item k of dds))
        end repeat
      end if
    end repeat
  end tell
  set text item delimiters to "{US}"
  return rows as text
end run
"""

_LISTS = f"""
on run argv
  tell application "Reminders" to set n to name of every list
  set text item delimiters to "{US}"
  return n as text
end run
"""

_CREATE = """
on mkdate(y, m, d, h, mi)
  set dt to current date
  set day of dt to 1
  set year of dt to y
  set month of dt to m
  set day of dt to d
  set hours of dt to h
  set minutes of dt to mi
  set seconds of dt to 0
  return dt
end mkdate

on run argv
  set theName to item 1 of argv
  set theBody to item 2 of argv
  set listName to item 3 of argv
  set hasDate to (item 4 of argv) is "1"
  tell application "Reminders"
    if listName is "" then
      set l to default list
    else
      set l to list listName
    end if
    if hasDate then
      set dt to my mkdate((item 5 of argv) as integer, (item 6 of argv) as integer, ¬
                          (item 7 of argv) as integer, (item 8 of argv) as integer, ¬
                          (item 9 of argv) as integer)
      set r to make new reminder at l with properties {name:theName, body:theBody, due date:dt}
    else
      set r to make new reminder at l with properties {name:theName, body:theBody}
    end if
    return id of r
  end tell
end run
"""

_COMPLETE = """
on run argv
  tell application "Reminders"
    set r to reminder id (item 1 of argv)
    set completed of r to true
    return name of r
  end tell
end run
"""


@dataclass(frozen=True)
class Reminder:
    list_name: str
    id: str
    title: str
    due: str          # ISO, or "" when undated


def warm_up() -> float:
    """Wake the Reminders app, absorbing its cold start somewhere that can wait."""
    import time

    started = time.time()
    run(_LISTS, timeout=COLD_START_TIMEOUT, retries=0)
    return time.time() - started


def lists(*, runner=None) -> list[str]:
    raw = (runner or run)(_LISTS)
    return [n for n in raw.split(US) if n.strip()]


def open_items(*, runner=None) -> list[Reminder]:
    """Every reminder not yet ticked, across every list, in one pass."""
    raw = (runner or run)(_OPEN)
    if not raw.strip():
        return []
    out: list[Reminder] = []
    for row in raw.split(US):
        parts = row.split(RS)
        if len(parts) != 4 or not parts[2].strip():
            continue
        out.append(Reminder(list_name=parts[0], id=parts[1], title=parts[2], due=parts[3]))
    return out


def summary(*, runner=None, limit: int = MAX_IN_PROMPT) -> str:
    """What is outstanding, short enough to sit in a prompt without crowding it."""
    items = open_items(runner=runner)
    if not items:
        return "Nothing outstanding in Reminders."
    lines = [f"- {r.title}" + (f" (due {r.due})" if r.due else "") + f" [{r.list_name}]"
             for r in items[:limit]]
    if len(items) > limit:
        lines.append(f"- …and {len(items) - limit} more")
    return "\n".join(lines)


def create(title: str, *, notes: str = "", list_name: str = "",
           when_iso: str | None = None, runner=None) -> str:
    """Make a reminder. Returns its id. Called only by the Executor."""
    from . import when as when_mod

    dt = when_mod.parse(when_iso)
    if dt is None:
        args = (title, notes, list_name, "0", "0", "0", "0", "0", "0")
    else:
        args = (title, notes, list_name, "1") + when_mod.components(dt)
    return (runner or run)(_CREATE, *args)


def complete(reminder_id: str, *, runner=None) -> str:
    """Tick one off. There is deliberately no way to delete a reminder from here:
    the user's list is theirs, and 'done' must never quietly mean 'gone'."""
    return (runner or run)(_COMPLETE, reminder_id)


def find_open(phrase: str, *, runner=None) -> Reminder | None:
    """The open reminder the user most likely means. Exact match, then substring,
    then the shortest title containing every word they said."""
    items = open_items(runner=runner)
    needle = phrase.strip().lower()
    if not needle:
        return None
    for r in items:
        if r.title.lower() == needle:
            return r
    for r in items:
        if needle in r.title.lower():
            return r
    words = [w for w in needle.split() if len(w) > 2]
    hits = [r for r in items if words and all(w in r.title.lower() for w in words)]
    return min(hits, key=lambda r: len(r.title)) if hits else None
