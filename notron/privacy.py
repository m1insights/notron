"""Keeping your secrets out of the notes Notron writes.

An assistant that reads everything you own will, sooner or later, read the note
where you keep your passwords — and then helpfully quote it back in a plan that
syncs to your phone and anywhere else that note goes. That is not hypothetical;
it happened on the first live run of Notron's morning routine.

Two defences, both applied by default:

  1. Notes that exist to store credentials are excluded from retrieval outright.
  2. Anything that still looks like a secret is redacted from a passage before
     the model ever sees it, and blocked again on the way out in the Guard.

The user can always ask about a credential note directly — the exclusion lifts
only when their request names it.
"""

from __future__ import annotations

import re

# Note titles that exist to hold credentials.
VAULT_TITLE = re.compile(
    r"\b(passwords?|passcodes?|logins?|credentials?|secrets?|api[\s_-]?keys?|"
    r"seed[\s_-]?phrase|recovery[\s_-]?codes?|pin\s?numbers?|bank\s?details?|"
    r"card\s?numbers?|social\s?security)\b",
    re.I,
)

# A labelled secret: "password: hunter2", "API key = sk-...", and the far more
# common "password hunter2" with no punctuation at all.
_LABEL = (r"pass(?:word|code|phrase)?|pwd|pw|pin|api[\s_-]?key|secret|token|"
          r"auth|bearer|client[\s_-]?secret|private[\s_-]?key|seed[\s_-]?phrase")

# Words that follow a label without being the secret, so "password manager" and
# "reset my password for Netflix" survive intact.
_NOT_A_SECRET = {
    "manager", "managers", "protected", "protection", "reset", "change", "changed",
    "for", "to", "and", "or", "on", "in", "of", "is", "was", "the", "a", "an",
    "required", "needed", "wrong", "correct", "field", "prompt", "page", "note",
    "same", "new", "old", "above", "below", "here", "saved", "stored",
}

LABELLED = re.compile(rf"(?i)\b({_LABEL})\b\s*(?:is|are|=|:)?\s*([^\s,.;]{{3,}})")

# Unlabelled high-entropy strings: long hex digests, sk-/ghp-/xox- style keys.
HIGH_ENTROPY = re.compile(
    r"\b(?:[0-9a-f]{32,}|sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|eyJ[A-Za-z0-9_-]{20,})\b",
    re.I,
)

REDACTED = "[redacted]"

# Notes people would not want quoted back at them. Notron may know they exist and
# may work with them when asked directly, but they never get pulled in as
# background material for an unrelated question — and never reproduced verbatim.
PRIVATE_TITLE = re.compile(
    r"\b(journal|diary|therapy|dream[s]?|confession[s]?|fantas(?:y|ies)|"
    r"roleplay|rp\s?log|nsfw|intimate|sext|medical|diagnosis|prescription)\b",
    re.I,
)


def is_vault(title: str) -> bool:
    """Does this note exist to store credentials?"""
    return bool(VAULT_TITLE.search(title))


def is_private(title: str) -> bool:
    """Is this the kind of note nobody wants quoted back at them?"""
    return bool(PRIVATE_TITLE.search(title))


def asked_for(request: str, title: str) -> bool:
    """Did the user's own words point at this note? Then it is fair game."""
    words = {w for w in re.findall(r"[a-z]{3,}", title.lower())}
    said = {w for w in re.findall(r"[a-z]{3,}", request.lower())}
    return bool(words & said) and bool(VAULT_TITLE.search(request) or words & said)


def _blank(m: re.Match) -> str:
    """Redact the value after a credential label, unless it is an ordinary word."""
    label, value = m.group(1), m.group(2)
    if value.lower().strip("'\"") in _NOT_A_SECRET:
        return m.group(0)
    return f"{label}: {REDACTED}"


def redact(text: str) -> str:
    """Blank out anything that reads like a credential, keeping the label."""
    return HIGH_ENTROPY.sub(REDACTED, LABELLED.sub(_blank, text))


def contains_secret(text: str) -> bool:
    if HIGH_ENTROPY.search(text):
        return True
    return any(_blank(m) != m.group(0) for m in LABELLED.finditer(text))


# Inside a note that exists to hold credentials, every value is a credential —
# "gmail: hunter2" carries no keyword to key off. So mask them all.
ANY_PAIR = re.compile(r"(?m)^([^\n:=]{1,40})\s*[:=]\s*(\S.*)$")


def redact_vault(text: str) -> str:
    """Mask every value in a credential note, keeping the labels so the user can
    still see which accounts they have."""
    return ANY_PAIR.sub(lambda m: f"{m.group(1).strip()}: {REDACTED}", redact(text))


def filter_passages(request: str, passages: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """(title, text) pairs in, safe pairs out. Vault notes drop unless asked for."""
    out = []
    for title, text in passages:
        vault = is_vault(title)
        if (vault or is_private(title)) and not asked_for(request, title):
            continue
        out.append((title, redact_vault(text) if vault else redact(text)))
    return out
