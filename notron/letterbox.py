"""Notron's own email address: requests from people the user approved.

The user gives Notron an address (say notron@m1labs.io), adds it to Apple Mail
as an account of its own, and names who may write to it. Their pharmacy
manager emails "draw up next week's rota"; a minute later it is a request in
the Reminders list "Notron", and from there it goes exactly where a request
dictated to Siri goes (`inbox.py`): Nemotron routes it, answers it in Notes or
writes a brief, and nothing runs until the user ticks Approve.

Chosen over a WhatsApp number (2026-10-01): no relay server, no Meta account,
and anyone can set one up with the Mail app they already have.

Anyone can email an address and anyone can type a name into From, so in plain
code, before any model sees a word:

1. Only mail that arrived after setup, in that one account, is looked at.
2. The sender's address must be on the user's list. Everyone else is ignored.
3. The receiving server's verdict must prove the sender: the topmost
   `Authentication-Results` header must be the user's own mail server's (its
   name is checked — anything else the sender could have written) and show
   DMARC pass for the sender's domain, or DKIM pass signed by that domain. SPF
   alone is not enough: shared senders can pass it for each other's domains.
   Unproven mail is held; at most one held alert per sender a day.
4. Anything shaped like patient details (an NHS number, a date of birth, the
   word "patient") is held, never sent to a model: the user's staff are asked to
   strip those, and this is the check that does not rely on them remembering.

A held email becomes a `Held email: …` reminder for the user — said once, never
answered. An email never becomes more than a request: it cannot approve, it
grants nothing, and Mail is only read, so nothing is ever sent back.
"""

from __future__ import annotations

import re
import time
from contextlib import contextmanager
from dataclasses import dataclass

from . import mail, paths

#: How often the listener looks. One small header read of one account.
EVERY = 120
#: Headers read per look: the newest are first.
SCAN = 20
#: How much of an email becomes the request. The title is the request.
MAX_BODY = 500
#: How long a looked-at message is remembered.
FORGET_AFTER = 30 * 86400
#: Whose `Authentication-Results` count, unless the user names their own server
#: at setup (`--server`). Google adds its own on top of every message it receives.
SERVERS = ("mx.google.com",)

#: Patient-shaped text. Narrow and conservative: a held admin email costs the
#: user one tap; patient details at a cloud model cannot be taken back.
PATIENT = re.compile(
    r"\b\d{3}[ -]?\d{3}[ -]?\d{4}\b"                       # an NHS number
    r"|\b(d\.?o\.?b\.?|date of birth|nhs (no|number)|patient'?s?|chi number)\b", re.I)


class LetterboxError(RuntimeError):
    pass


@dataclass(frozen=True)
class Letter:
    """One email from an approved sender, read in full. No model has seen it."""
    header: mail.Header
    sender: str            # the address
    name: str              # the display name, or the address
    body: str
    verdict: str           # "" if it may become a request, else why it is held


# ------------------------------------------------------------------- store

def _path():
    return paths.data_dir() / "letterbox.json"


def _read() -> dict:
    from . import securestore
    data = securestore.read_json(_path()) or {}
    if data and data.get("version") != 1:
        raise LetterboxError("The Notron address record is from a newer Notron; leaving it untouched.")
    return data


@contextmanager
def _editing():
    import fcntl
    from . import securestore
    securestore.private_directory(paths.data_dir())
    with open(paths.data_dir() / "letterbox.lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            data = _read() or {"version": 1, "seen": {}}
            yield data
            securestore.write_json(_path(), data)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def setup(account: str, senders: list[str], *, now: float | None = None,
          servers: list[str] | None = None) -> dict:
    """Point Notron at one Mail account and name who may write to it.

    Only mail arriving from now on counts: setting up an address must not turn
    a month of old mail into requests."""
    senders = sorted({s.strip().lower() for s in senders if s.strip()})
    bad = [s for s in senders if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", s)]
    if bad:
        raise LetterboxError(f"Not an email address: {', '.join(bad)}")
    if not senders:
        raise LetterboxError("Name at least one sender: --allow you@example.com")
    with _editing() as data:
        if data.get("account") != account:
            data["since"] = time.time() if now is None else now
        data.update(account=account, senders=senders)
        if servers is not None:
            data["servers"] = sorted({s.strip().lower() for s in servers if s.strip()}) or list(SERVERS)
        return dict(data)


def settings() -> dict:
    return _read()


def account() -> str:
    return _read().get("account", "")


# ------------------------------------------------------------------- checks

def _address(sender: str) -> str:
    m = re.search(r"<([^>]+)>", sender)
    return (m.group(1) if m else sender).strip().lower()


def _name(sender: str) -> str:
    name = sender.split("<")[0].strip().strip('"') if "<" in sender else ""
    return name or _address(sender)


def _unfold(raw: str) -> list[str]:
    """Header lines with RFC 5322 continuation lines joined back on."""
    lines: list[str] = []
    for line in raw.replace("\r\n", "\n").split("\n"):
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += " " + line.strip()
        elif line.strip():
            lines.append(line)
    return lines


def proven(raw_headers: str, sender: str, servers=SERVERS) -> bool:
    """Did the user's own mail server prove `sender` sent this?

    Only the topmost Authentication-Results counts, and only when it names the
    user's server: servers add theirs on top, so every other one travelled with
    the message and may be forged."""
    domain = sender.rpartition("@")[2].lower()
    top = next((ln.split(":", 1)[1] for ln in _unfold(raw_headers)
                if ln.lower().startswith("authentication-results:")), "")
    if not top or not domain:
        return False
    top = top.lower()
    if top.split(";", 1)[0].strip().split()[:1] not in ([s] for s in servers):
        return False
    for d in re.findall(r"\bdmarc=pass\b[^;]*?header\.from=([\w.-]+)", top):
        if d == domain:
            return True
    for d in re.findall(r"\bdkim=pass\b[^;]*?header\.(?:d|i)=@?([\w.@-]+)", top):
        if d.rpartition("@")[2] == domain:
            return True
    return False


def holds_patient_details(text: str) -> bool:
    return bool(PATIENT.search(text))


def _trim(text: str) -> str:
    """The new part of an email, as one line: quoted history is not the request."""
    kept = []
    for line in text.splitlines():
        if line.startswith(">") or re.match(r"^On .{5,200} wrote:\s*$", line) or line.strip() == "--":
            break
        kept.append(line.strip())
    return " ".join(" ".join(kept).split())


# -------------------------------------------------------------------- read

def gather(*, now: float | None = None) -> list[Letter]:
    """Mail reads only — no model, no writes. Safe on a side thread."""
    now = time.time() if now is None else now
    data = _read()
    acct, senders = data.get("account"), set(data.get("senders") or [])
    if not acct or not senders:
        return []
    since, seen = data.get("since", now), data.get("seen", {})
    letters = []
    for h in mail.headers(acct, SCAN):
        if h.key in seen or now - h.age < since:
            continue
        addr = _address(h.sender)
        if addr not in senders:
            letters.append(Letter(h, addr, _name(h.sender), "", "ignored"))
            continue
        raw, body = mail.message(h)
        if not raw and not body:
            continue                    # moved or gone: looked at again next time, never guessed
        verdict = ("" if proven(raw, addr, data.get("servers") or SERVERS) else "unproven")
        # Screened as it would be sent: the subject and the new part of the body.
        if not verdict and holds_patient_details(f"{h.subject}\n{_trim(body)[:MAX_BODY]}"):
            verdict = "patient"
        letters.append(Letter(h, addr, _name(h.sender), body, verdict))
    return letters


# ------------------------------------------------------------------- write

HELD = {"unproven": "Mail could not prove it really came from {who}, so Notron did not read it.",
        "patient": "It looks like it holds patient details, so it was not sent to a model."}


def request_text(letter: Letter) -> str:
    """The request the graph is asked: who, what, and the new part of the email."""
    subject = " ".join(letter.header.subject.split()) or "(no subject)"
    body = _trim(letter.body)[:MAX_BODY]
    return f"{letter.name} emailed Notron: {subject}" + (f" — {body}" if body else "")


def file(letters: list[Letter], *, caller=None) -> dict:
    """Turn gathered letters into Reminders in the Notron list. Returns counts.

    Each email is claimed by its Message-ID before anything is made (`inbox._buzz`),
    so a retry never files it twice; it is marked seen only once its reminder exists."""
    from . import inbox
    out = {"filed": 0, "held": 0, "ignored": 0}
    for letter in letters:
        h = letter.header
        key = f"letter:{h.message_id or h.key}"
        today = time.strftime("%Y-%m-%d")
        if letter.verdict == "ignored":
            out["ignored"] += 1
        elif letter.verdict and _read().get("held_on", {}).get(letter.sender) == today:
            pass        # one held alert per sender a day: a forger cannot flood the phone
        elif letter.verdict:
            rid = inbox._buzz(f"held:{key}", f"Held email: {h.subject[:80] or 'from ' + letter.name}",
                              f"From {letter.name} <{letter.sender}>. "
                              + HELD[letter.verdict].format(who=letter.sender)
                              + (f"\nOpen it: {h.link}" if h.link else ""), caller=caller)
            if not rid:
                continue            # another pass is making it: looked at again, never lost
            with _editing() as data:
                data.setdefault("held_on", {})[letter.sender] = today
            out["held"] += 1
        else:
            rid = inbox._buzz(key, request_text(letter),
                              f"Emailed to Notron by {letter.name} <{letter.sender}>."
                              + (f"\nOpen it: {h.link}" if h.link else ""), caller=caller, request=True)
            if not rid:
                continue            # a claim cut short by a crash: looked at again, never lost
            out["filed"] += 1
        with _editing() as data:
            data.setdefault("seen", {})[h.key] = time.time()
            cutoff = time.time() - FORGET_AFTER
            data["seen"] = {k: v for k, v in data["seen"].items() if v >= cutoff}
    return out
