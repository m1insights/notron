"""Apple Mail, read in bulk. Read only: nothing here writes to Mail.

The user's Gmail accounts live in Mail.app on the Mac, so Mail is the one
place Notron can see them without a Google login of its own. Everything here is
AppleScript, because Mail offers nothing else, and the costs were measured on
the developer's Mac on 2026-09-25 (8,401 + 3,308 messages in two inboxes):

| Doing it the obvious way | Doing it right |
|---|---|
| `first message whose id is n` — **2.7s**, and Gmail hands back the copy in All Mail, whose content then fails with `-1728` | `message i of mailbox "INBOX"` by index, id checked in the same request |
| `content` of 40 messages — **92s** cold | headers first (**~4.5s** for 100 subject/sender/date/id once synced; ~2 min while Gmail is still syncing a large inbox), content only for the few worth reading (~0.5–1s each synced) |

Indexes move when new mail arrives, so a message is always addressed by its
index *and* its id together: the script checks the id it finds at that index and
looks a little further down (new mail pushes older mail down) before giving up.

**Mail is only ever read.** No script here sends, replies, saves, moves or
deletes, and a test reads every script to keep it that way. (A first version
saved reply drafts; the user found a draft written without their context was no
help — what they need is to not lose track of what an email asks of them.)
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

#: Unit and record separators: never typed into a subject or a sender name, so
#: they split the output of one bulk request without any quoting to get wrong.
US, RS = "\x1f", "\x1e"

#: How far below its old index a message may have slid by the time it is read.
SLIDE = 50

#: Bodies per request. Measured 2026-09-25 while Gmail was still syncing 85,846
#: messages into one inbox: every request cost ~24s before it read anything and
#: a body ~5s more, so a whole shortlist in one request could outrun the timeout.
BODIES_PER_CALL = 4

TIMEOUT = 240


class MailError(RuntimeError):
    pass


@dataclass(frozen=True)
class Header:
    account: str
    index: int          # a hint: where it was when the headers were read
    id: int             # Mail's id, checked at every use
    sender: str
    subject: str
    age: int            # seconds since it arrived
    read: bool
    message_id: str = ""    # the RFC 822 Message-ID: what a message:// link opens

    @property
    def link(self) -> str:
        """Opens this email in Mail on the Mac, from a reminder or a note."""
        from urllib.parse import quote
        return f"message://%3C{quote(self.message_id.strip('<>'), safe='@.-_')}%3E" if self.message_id else ""

    @property
    def key(self) -> str:
        return f"{self.account}#{self.id}"


def _osascript(script: str, *args: str, timeout: int = TIMEOUT) -> str:
    """The only place Mail is asked anything. Values travel as argv, never in the source."""
    try:
        proc = subprocess.run(["osascript", "-", *args], input=script, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise MailError(f"Mail did not answer within {timeout}s") from e
    if proc.returncode != 0:
        # Mail's errors can list every message it was asked about; the first line is the point.
        raise MailError((proc.stderr.strip() or "osascript failed")[:300])
    return proc.stdout.rstrip("\n")


ACCOUNTS = """
tell application "Mail"
    set out to ""
    repeat with a in every account
        if enabled of a then set out to out & (name of a) & (character id 30)
    end repeat
    return out
end tell
"""


def accounts() -> list[str]:
    return [a for a in _osascript(ACCOUNTS).split(RS) if a]


HEADERS = """
on run argv
    set acct to item 1 of argv
    set want to (item 2 of argv) as integer
    set US to character id 31
    set RS to character id 30
    tell application "Mail"
        set mb to mailbox "INBOX" of account acct
        set total to count of messages of mb
        if total = 0 then return ""
        if want > total then set want to total
        -- Asked of the range itself, every time: a range saved in a variable
        -- becomes a list of All Mail references, and `id of` a list fails (-1728).
        set idList to id of messages 1 thru want of mb
        set fromList to sender of messages 1 thru want of mb
        set subjList to subject of messages 1 thru want of mb
        set rcvdList to date received of messages 1 thru want of mb
        set readList to read status of messages 1 thru want of mb
        set midList to message id of messages 1 thru want of mb
        set now to current date
        set out to ""
        repeat with i from 1 to want
            set s to item i of subjList
            if s is missing value then set s to ""
            set out to out & i & US & (item i of idList) & US & (item i of fromList) & US & s & US & ((now - (item i of rcvdList)) as integer) & US & (item i of readList) & US & (item i of midList) & RS
        end repeat
        return out
    end tell
end run
"""


def headers(account: str, limit: int = 100) -> list[Header]:
    """The newest `limit` messages in one account's inbox: who, what, how old. No bodies."""
    out = []
    for row in _osascript(HEADERS, account, str(limit)).split(RS):
        parts = row.split(US)
        if len(parts) != 7:
            continue
        index, mid, sender, subject, age, read, message_id = parts
        try:
            out.append(Header(account, int(index), int(mid), sender.strip(), subject.strip(),
                              int(age), read.strip() == "true", message_id.strip()))
        except ValueError:
            continue
    return out


#: Finds a message by index and id; shared by every script that touches one.
FIND = """
-- The index where the message with id `wanted` now sits, or 0. Returns an index,
-- never the message: a message reference that leaves this handler resolves to
-- Gmail's All Mail copy, whose content Mail then refuses (-1728).
on findIt(mb, idx, wanted, slide)
    tell application "Mail"
        set total to count of messages of mb
        repeat with i from idx to (idx + slide)
            if i > total then exit repeat
            if i > 0 and (id of message i of mb) = wanted then return i
        end repeat
    end tell
    return 0
end findIt
"""

BODIES = FIND + """
on run argv
    set acct to item 1 of argv
    set slide to (item 2 of argv) as integer
    set RS to character id 30
    set US to character id 31
    set out to ""
    tell application "Mail"
        set mb to mailbox "INBOX" of account acct
        repeat with k from 3 to (count of argv) by 2
            set wanted to (item (k + 1) of argv) as integer
            set i to my findIt(mb, (item k of argv) as integer, wanted, slide)
            if i = 0 then
                set out to out & wanted & US & "" & RS
            else
                set out to out & wanted & US & (content of message i of mb) & RS
            end if
        end repeat
    end tell
    return out
end run
"""


def bodies(headers_: list[Header]) -> dict[str, str]:
    """Plain-text bodies for a few messages, keyed by `Header.key`. A message that
    has moved or gone is left out rather than guessed at."""
    found: dict[str, str] = {}
    by_account: dict[str, list[Header]] = {}
    for h in headers_:
        by_account.setdefault(h.account, []).append(h)
    for account, hs in by_account.items():
        for start in range(0, len(hs), BODIES_PER_CALL):
            args = [account, str(SLIDE)]
            for h in hs[start:start + BODIES_PER_CALL]:
                args += [str(h.index), str(h.id)]
            for row in _osascript(BODIES, *args).split(RS):
                mid, _, text = row.partition(US)
                if mid.strip().isdigit() and text.strip():
                    found[f"{account}#{int(mid)}"] = text
    return found


SENT = """
on run argv
    set acct to item 1 of argv
    set want to (item 2 of argv) as integer
    set US to character id 31
    set RS to character id 30
    tell application "Mail"
        set mb to missing value
        -- Gmail's sent mail is "[Gmail]/Sent Mail" by name; plain "Sent Mail" fails
        -- (measured 2026-09-25). iCloud and others call it "Sent Messages".
        repeat with nm in {"[Gmail]/Sent Mail", "Sent Mail", "Sent Messages", "Sent"}
            try
                set mb to mailbox (nm as text) of account acct
                exit repeat
            end try
        end repeat
        if mb is missing value then return "NOSENT"
        set total to count of messages of mb
        if total = 0 then return ""
        if want > total then set want to total
        set subjList to subject of messages 1 thru want of mb
        set sentList to date sent of messages 1 thru want of mb
        set toList to address of to recipient 1 of messages 1 thru want of mb
        set now to current date
        set out to ""
        repeat with i from 1 to want
            set s to item i of subjList
            if s is missing value then set s to ""
            set t to item i of toList
            if t is missing value then set t to ""
            set out to out & s & US & ((now - (item i of sentList)) as integer) & US & t & RS
        end repeat
        return out
    end tell
end run
"""


def sent(account: str, limit: int = 200) -> list[tuple[str, int, str]]:
    """(subject, seconds ago, first recipient) for the newest mail the user sent
    from one account. Raises when the account has no sent mailbox Notron can find,
    rather than reading as "you replied to nothing"."""
    raw = _osascript(SENT, account, str(limit))
    if raw == "NOSENT":
        raise MailError(f"No sent mailbox found in {account}.")
    out = []
    for row in raw.split(RS):
        parts = row.split(US)
        if len(parts) == 3 and parts[1].strip().lstrip("-").isdigit():
            out.append((parts[0].strip(), int(parts[1]), parts[2].strip().lower()))
    return out
