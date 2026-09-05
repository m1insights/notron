"""The only code in NOTRON that is allowed to change a note.

No language model runs here. The model proposes; the Guard judges; this applies
and records. Keeping the write path dumb is what makes the agent safe to leave
running on your own machine.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from contextlib import contextmanager
import fcntl
import json
import os
import threading
from hashlib import sha256
from datetime import datetime

from . import calendar, guard, markup, notedoc, notes, reminders, undo, workspace, policy, rewrite
from .state import Write
from .requests import revision

_LOCAL_LOCK = threading.RLock()
_LOCK_DEPTH = threading.local()


@contextmanager
def write_transaction():
    """Serialize local Notes transactions across threads/processes, including audit.

    iCloud and the Notes editor do not honor this lock. Reentrant calls reuse the
    held fd rather than deadlocking on a second flock in the same process.
    """
    from . import operations
    from .securestore import private_directory
    with _LOCAL_LOCK:
        if getattr(_LOCK_DEPTH, "held", False):
            yield
            return
        private_directory(operations.PATH.parent)
        path = operations.PATH.parent / "notes-write.lock"
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            _LOCK_DEPTH.held = True
            yield
        finally:
            _LOCK_DEPTH.held = False
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def capture_write(title: str, *, folder: str = workspace.FOLDER, note_id: str | None = None,
                  body: str | None = None, **kwargs) -> Write:
    """Bind an explicit ID and revision BEFORE generating a proposed change.

    Supplying body uses the exact already-read model input. A missing/ambiguous
    target stays unbound and cannot silently create a replacement.
    """
    if note_id is None and folder == workspace.FOLDER:
        note_id = policy.current().system_notes.get(title)
    note = notes.get_note(note_id) if note_id else notes.unique_note(folder, title)
    if note and policy.current().readable(note):
        body = notes.read_body(note.id) if body is None else body
        if kwargs.get('mode') == 'append' and 'anchor' not in kwargs:
            anchors = [text.strip() for text in notedoc.texts(body) if text.strip()]
            kwargs['anchor'] = anchors[-1] if anchors else ''
        return Write(title=note.title, folder=note.folder, note_id=note.id,
                     expected_revision=revision(body), markdown="", **kwargs)
    return Write(title=title, folder=folder, markdown="", **kwargs)


@dataclass(frozen=True)
class WriteResult:
    ok: bool
    reason: str
    note_id: str | None = None
    ref: str | None = None       # reminder id / event uid
    operation_id: str | None = None
    observed_revision: str | None = None
    alternative_text: str | None = None


class Executor:
    def __init__(self, *, dry_run: bool = False, audit: bool = True) -> None:
        self.dry_run = dry_run
        self.audit = audit

    # Immediate convenience calls bind now. Inference producers must instead
    # capture_write before inference, then pass the completed Write to apply_write.
    def replace(self, title, body_markdown, *, folder=workspace.FOLDER, rewrite_allowed=False):
        return self.apply_write(replace(capture_write(title, folder=folder, mode='replace',
                                      rewrite_allowed=rewrite_allowed), markdown=body_markdown))

    def append(self, title, body_markdown, *, folder=workspace.FOLDER, expected_note_id=None):
        return self.apply_write(replace(capture_write(title, folder=folder, note_id=expected_note_id,
                                      mode='append'), markdown=body_markdown))

    def insert(self, title, body_markdown, *, after, folder=workspace.FOLDER, anchor=''):
        return self.apply_write(replace(capture_write(title, folder=folder,
                                      note_id=policy.request_note_id(), mode='insert',
                                      after=after, anchor=anchor), markdown=body_markdown))

    def mark(self, title, marks, *, folder=workspace.FOLDER):
        return self.apply_write(capture_write(title, folder=folder, mode='mark', marks=marks))

    def restore(self, title, raw_html_body, *, folder=workspace.FOLDER):
        return self.apply_write(replace(capture_write(title, folder=folder, mode='restore'),
                                        markdown=raw_html_body))

    def create_approved(self, title, body_markdown, *, folder, source_checks=()):
        # Creation is a distinct explicit operation, never a title-selected append.
        return self._execute(Write(title=title, folder=folder, markdown=body_markdown,
                                   mode='append', source_checks=list(source_checks)), creation=True)

    def _permitted(self, note, mode, rewrite_allowed=False):
        snap = policy.current()
        if snap.status != 'ready' or note is None or not snap.readable(note):
            return False
        role = snap.system_role(note.id)
        if role is not None:
            # A moved/renamed system note does not acquire ordinary-home powers.
            return note.folder == workspace.FOLDER and note.title == role and role != workspace.ABOUT
        if note.folder == workspace.FOLDER:
            return False
        if mode in ('replace', 'restore'):
            return snap.can_file(note.id) and (mode == 'restore' or
                                               (rewrite_allowed and rewrite.allowed(note.id)))
        if mode == 'mark':
            return snap.can_file(note.id) or policy.can_mark_source(note.id)
        return snap.can_file(note.id) or snap.can_reply(note.id, policy.request_id())

    @staticmethod
    def _sources_current(write: Write) -> bool:
        bodies = {}
        for nid, expected, anchor, near in write.source_checks:
            source = notes.get_note(nid) if nid else None
            if not expected or not source or not policy.current().readable(source):
                return False
            if nid not in bodies:
                bodies[nid] = notes.read_body(nid)
            body = bodies[nid]
            hits = [line for line in notedoc.lines(body) if line.text.strip() == anchor.strip()]
            positioned = [line for line in hits if line.block == near]
            if not policy.current().readable(source):
                return False
            if len(hits) != 1 and not (revision(body) == expected and len(positioned) == 1):
                return False
        return True

    def apply_write(self, write: Write) -> WriteResult:
        return self._execute(write)

    def _execute(self, write: Write, *, creation=False) -> WriteResult:
        from . import retention
        retention.require_ready()
        with write_transaction():
            result = self._locked_write(write, creation=creation)
            if result.ok and result.reason == 'written':
                # A failed audit never turns a verified primary effect into failure.
                self._log(f'{write.mode} on *{write.title}*')
            elif not result.ok:
                self._log('**BLOCKED / NEEDS REVIEW** Notes write was not verified')
            return result

    def _locked_write(self, write: Write, *, creation=False) -> WriteResult:
        from . import operations, requests
        folder = write.folder or workspace.FOLDER
        def result(ok, reason, *, observed=None, alternative=None, note_id=None):
            return WriteResult(ok, reason, note_id or write.note_id,
                               operation_id=write.operation_id, observed_revision=observed,
                               alternative_text=alternative)
        if not write.operation_id:
            return result(False, 'operation identity is required')
        if not creation and (not write.note_id or not write.expected_revision):
            return result(False, 'target ID and captured expected revision are required')
        note = notes.get_note(write.note_id) if write.note_id else None
        def permitted(current_note):
            if creation:
                return (policy.current().status == 'ready' and folder != workspace.FOLDER
                        and not notes.find_note(folder, write.title))
            return self._permitted(current_note, write.mode, write.rewrite_allowed)
        if not permitted(note):
            return result(False, 'note missing or note policy denied access')
        if not self._sources_current(write):
            return result(False, 'source missing, changed or ambiguous; nothing copied')
        old = notes.read_body(note.id) if note else ''
        store = op = None
        if not self.dry_run:
            store = operations.current()
            envelope = requests.active_request()
            payload = json.dumps({'write': asdict(write), 'creation': creation},
                                 sort_keys=True, ensure_ascii=False).encode()
            op = store.prepare(envelope.request_id if envelope else 'write:' + write.operation_id,
                               write.operation_id, sha256(payload).hexdigest(), payload=payload,
                               source_id=(write.source_checks[0][0] if write.source_checks else
                                          envelope.note_id if envelope else write.note_id),
                               target_id=write.note_id, expected_revision=write.expected_revision)
            if op.status in (operations.S.APPLIED, operations.S.RECEIPTED):
                return result(True, 'already applied; no write repeated', observed=op.observed_revision)
            if op.status != operations.S.PREPARED:
                return result(False, 'operation requires review; no write repeated', observed=op.observed_revision)

        def refuse(reason, code='revision_conflict', *, observed=None, alternative=None):
            if store:
                store.transition(write.operation_id, operations.S.PREPARED, operations.S.CANCELLED,
                                 failure_code=code, observed_revision=observed)
            return result(False, reason, observed=observed, alternative=alternative)

        unchanged = revision(old) == write.expected_revision
        if write.mode in ('replace', 'restore') and not unchanged:
            return refuse('the note changed since this result was prepared', observed=revision(old))
        if write.mode in ('replace', 'restore') and (not notedoc.supports_replacement(old) or
                (write.mode == 'restore' and not notedoc.supports_replacement(write.markdown))):
            return refuse('unsupported rich content; keep the original and use a separate plain-text result',
                          'write_failed', alternative=markup.to_text(write.markdown)
                          if write.mode == 'restore' else write.markdown)
        if write.mode == 'append':
            if note and not unchanged and (not write.rebase_append or
                    notedoc.locate_unique(old, write.anchor, near=0) is None):
                return refuse('the note changed; append anchor or layout cannot be safely rebased')
            new = (old if note else markup.render(write.title, '')) + markup.to_html(write.markdown)
        elif write.mode == 'insert':
            at = notedoc.locate_unique(old, write.anchor, near=write.after or 0, unchanged=unchanged)
            if at is None:
                return refuse('the note changed or its reply anchor is ambiguous')
            new = notedoc.insert_after(old, at, markup.to_html(write.markdown))
        elif write.mode == 'mark':
            new, ticked = notedoc.mark_unique(old, write.marks, unchanged=unchanged)
            if not ticked:
                return refuse('the source lines changed or gone, or their anchors are ambiguous')
        elif write.mode == 'restore':
            new = write.markdown
        elif write.mode == 'replace':
            new = markup.render(note.title, write.markdown)
        else:
            return refuse('unsupported write mode', 'write_failed')
        verdict = guard.check(folder=note.folder if note else folder,
                              title=note.title if note else write.title, old_body=old,
                              new_body=new, mode=write.mode, rewrite_allowed=write.rewrite_allowed)
        if not verdict:
            return refuse(verdict.reason, 'write_failed')
        if self.dry_run:
            return result(True, 'dry run — nothing written')
        try:
            if note and write.mode != 'restore':
                undo.save(note.id, old)
        except Exception:
            return refuse('required backup failed; nothing written', 'write_failed')
        # Commit APPLYING before the external effect, then make final checks.
        if store.get(write.operation_id).status != operations.S.PREPARED:
            return result(False, 'note policy changed during preparation; nothing written')
        store.transition(write.operation_id, operations.S.PREPARED, operations.S.APPLYING)
        try:
            current_note = notes.get_note(note.id) if note else None
            if not permitted(current_note) or (note and current_note and
                    (current_note.title, current_note.folder) != (note.title, note.folder)):
                store.transition(write.operation_id, operations.S.APPLYING, operations.S.NEEDS_REVIEW,
                                 failure_code='policy_changed')
                return result(False, 'note policy or target changed; nothing written')
            if not self._sources_current(write):
                store.transition(write.operation_id, operations.S.APPLYING, operations.S.NEEDS_REVIEW,
                                 failure_code='source_changed')
                return result(False, 'source missing, changed or ambiguous; nothing copied')
            current_body = notes.read_body(note.id) if note else ''
            if current_body != old:
                store.transition(write.operation_id, operations.S.APPLYING, operations.S.NEEDS_REVIEW,
                                 failure_code='revision_conflict', observed_revision=revision(current_body))
                return result(False, 'the note changed during write preparation; nothing written',
                              observed=revision(current_body))
            if not permitted(current_note):
                if store.get(write.operation_id).status == operations.S.APPLYING:
                    store.transition(write.operation_id, operations.S.APPLYING, operations.S.NEEDS_REVIEW,
                                     failure_code='policy_changed')
                return result(False, 'note policy changed; nothing written')
            # Apple exposes no CAS. Remote edits after this read remain a final race.
            if note:
                notes.write_body(note.id, new)
                nid = note.id
            else:
                nid = notes.create_note(folder, new)
            observed = revision(notes.read_body(nid))
            if observed != revision(new):
                store.transition(write.operation_id, operations.S.APPLYING, operations.S.NEEDS_REVIEW,
                                 external_id=nid, failure_code='post_write_divergence', observed_revision=observed)
                return result(False, 'post-write divergence; outcome needs review', observed=observed, note_id=nid)
            store.transition(write.operation_id, operations.S.APPLYING, operations.S.APPLIED,
                             external_id=nid, observed_revision=observed)
        except Exception:
            # Do not retry: a timeout or failure after applying may have landed.
            if store.get(write.operation_id).status == operations.S.APPLYING:
                store.transition(write.operation_id, operations.S.APPLYING, operations.S.NEEDS_REVIEW,
                                 failure_code='unknown_outcome')
            return result(False, 'write outcome uncertain; review required')
        if note and note.folder != workspace.FOLDER and write.mode in ('append', 'insert') and not policy.current().can_file(note.id):
            policy.consume_reply()
        return result(True, 'written', observed=observed, note_id=nid)

    def do(self, action, *, about: str = "", request: str = "") -> WriteResult:
        """Apply one thing outside Notes. Still no model anywhere in this path."""
        if policy.current().status != 'ready':
            return WriteResult(False, 'note policy not ready; actions paused')
        from . import retention
        retention.require_ready()
        verdict = guard.check_action(action, about=about, request=request)
        if not verdict:
            self._log(f"**BLOCKED** {action.op} {action.kind} *{action.title}* — {verdict.reason}")
            return WriteResult(False, verdict.reason)

        if self.dry_run:
            return WriteResult(True, "dry run — nothing created")

        try:
            ref, detail = self._perform(action)
        except LookupError as e:
            self._log(f"**BLOCKED** {action.op} {action.kind} *{action.title}* — {e}")
            return WriteResult(False, str(e))
        except Exception as e:
            # An app that is not approved yet hangs rather than failing, so a real
            # exception here is worth saying out loud instead of swallowing.
            self._log(f"**FAILED** {action.op} {action.kind} *{action.title}* — {type(e).__name__}: {e}")
            return WriteResult(False, f"{action.kind} app said no ({type(e).__name__})")

        self._log(f"{action.op} {action.kind} *{action.title}*{detail}")
        return WriteResult(True, detail.strip(" —") or "done", ref=ref)

    def _perform(self, action) -> tuple[str, str]:
        if action.kind == "reminder" and action.op == "create":
            ref = reminders.create(action.title, notes=action.notes,
                                   list_name=action.where, when_iso=action.when)
            return ref, self._said(action)

        if action.kind == "reminder" and action.op == "complete":
            hit = reminders.find_open(action.title)
            if hit is None:
                raise LookupError(f"couldn't find an open reminder called {action.title!r}")
            reminders.complete(hit.id)
            return hit.id, f" — ticked off “{hit.title}”"

        if action.kind == "event" and action.op == "create":
            ref = calendar.create(action.title, start_iso=action.when, end_iso=action.ends,
                                  calendar_name=action.where, notes=action.notes)
            return ref, self._said(action)

        raise ValueError(f"nothing to do for {action.kind}/{action.op}")

    @staticmethod
    def _said(action) -> str:
        from . import when as when_mod

        moment = when_mod.parse(action.when)
        return f" — {when_mod.human(moment)}" if moment else ""

    def _log(self, line: str) -> None:
        if not self.audit or self.dry_run:
            return
        try:
            # The same lock/revision/post-read path, with recursion disabled.
            target = capture_write(workspace.LOG, mode='append')
            if target.note_id:
                Executor(audit=False).apply_write(replace(target, markdown=
                    f"{datetime.now():%Y-%m-%d %H:%M} — {line}"))
        except Exception:
            pass  # durable primary success stands; audit recovery belongs to Task 3
