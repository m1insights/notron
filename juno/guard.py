"""The Guard node: the last thing between a model's idea and your notes.

Every proposed write passes through `check`. Nothing else in JUNO is permitted
to call `notes.write_body` directly. Three guarantees, in plain terms:

  1. Juno can never write to 📌 About Me. Your instructions are yours.
  2. Outside her own folder Juno may only APPEND. She cannot delete or
     rewrite a note you wrote.
  3. Every allowed write is logged before it happens.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import workspace

MAX_BODY_CHARS = 200_000


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str

    def __bool__(self) -> bool:
        return self.allowed


ALLOW = Verdict(True, "ok")


def check(*, folder: str, title: str, old_body: str, new_body: str, mode: str) -> Verdict:
    """Judge one proposed write. `mode` is "replace" or "append"."""
    if title in workspace.READ_ONLY:
        return Verdict(False, f"{title} is read-only — it is the user's instruction note.")

    if mode not in ("replace", "append"):
        return Verdict(False, f"unknown write mode {mode!r}")

    if not new_body.strip():
        return Verdict(False, "refusing to write an empty body")

    if len(new_body) > MAX_BODY_CHARS:
        return Verdict(False, f"body is {len(new_body)} chars, over the {MAX_BODY_CHARS} limit")

    outside = folder != workspace.FOLDER
    if outside and mode == "replace":
        return Verdict(False, f"{title!r} is outside {workspace.FOLDER}; Juno may only append there.")

    if mode == "append" and old_body and not new_body.startswith(old_body):
        return Verdict(False, "append would not preserve the existing note content")

    if mode == "replace" and title in workspace.SHARED:
        return Verdict(False, f"{title} is shared with the user; append only.")

    return ALLOW
