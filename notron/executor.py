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
        target = capture_write(title, folder=folder, mode='restore')
        snapshot = undo.peek(target.note_id) if target.note_id else None
        return self.apply_write(replace(target, markdown=raw_html_body,
                                        snapshot_id=snapshot.snapshot_id if snapshot else None))

    def create_approved(self, title, body_markdown, *, folder, source_checks=(), content_sources=(), operation_id=None):
        # Creation is a distinct explicit operation, never a title-selected append.
        write = Write(title=title, folder=folder, markdown=body_markdown,
                      mode='append', source_checks=list(source_checks), content_sources=list(content_sources))
        if operation_id:
            write.operation_id = operation_id
        return self.apply_creation(write)

    def apply_creation(self, write: Write) -> WriteResult:
        """Apply an exact write prepared by the explicit creation approval path."""
        return self._execute(write, creation=True)

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
    def _content_sources(write: Write) -> tuple[str, ...]:
        from . import requests
        envelope = requests.active_request()
        sources = set(write.content_sources)
        sources.update(check[0] for check in write.source_checks)
        # Payload includes destination title/anchors/marks as well as copied text.
        if write.note_id:
            sources.add(write.note_id)
        if envelope and envelope.note_id:
            sources.add(envelope.note_id)
        return tuple(sorted(nid for nid in sources if nid))

    @staticmethod
    def _content_readable(sources: tuple[str, ...]) -> bool:
        current = [notes.get_note(nid) for nid in sources]
        # Read current policy after the metadata reads; those reads may overlap
        # a policy save. All contributing Notes sources must remain readable.
        snap = policy.current()
        return all(note is not None and snap.readable(note) for note in current)

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
            if result.ok:
                # A failed audit never turns a verified primary effect into failure.
                self._log('Notes operation verified', operation_id=write.operation_id)
            elif not result.ok:
                self._log('**BLOCKED / NEEDS REVIEW** Notes write was not verified')
            return result

    def _locked_write(self, write: Write, *, creation=False) -> WriteResult:
        from . import operations, requests, recovery
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
        if not creation and not permitted(note):
            return result(False, 'note missing or note policy denied access')
        if write.undo_reply and write.mode not in ('append', 'insert'):
            return result(False, 'undo receipts must preserve the live body')
        content_sources = self._content_sources(write)
        if not self.dry_run:
            store = operations.current()
            prior = store.get(write.operation_id)
            if prior and prior.status != operations.S.PREPARED:
                envelope = requests.active_request()
                payload = json.dumps({'write': asdict(write), 'creation': creation},
                                     sort_keys=True, ensure_ascii=False).encode()
                expected_request = envelope.request_id if envelope else 'write:' + write.operation_id
                if (prior.request_id != expected_request or prior.payload_hash != sha256(payload).hexdigest()
                        or prior.content_source_ids != content_sources):
                    raise operations.OperationConflict('Operation identity already belongs to different work.')
                if not prior.payload_ref or not self._content_readable(prior.content_source_ids or ()):
                    return result(False, 'operation content unavailable; review required')
                if prior.status in (operations.S.APPLIED, operations.S.RECEIPTED):
                    self._finish_snapshot(write)
                    return result(True, 'already applied; no write repeated', observed=prior.observed_revision, note_id=prior.external_id)
                if prior.status == operations.S.APPLYING or (prior.status == operations.S.NEEDS_REVIEW and
                                                             prior.failure_code == 'unknown_outcome'):
                    evidence = recovery.get(write.operation_id + ':evidence')
                    if creation:
                        # Approval authorizes verification of a durably known
                        # created ID, not reading arbitrary same-title bodies.
                        # Without that ID Apple metadata cannot establish ownership.
                        note = notes.get_note(prior.external_id) if prior.external_id else None
                        if note and (note.folder != folder or note.title != write.title):
                            note = None
                    if note and evidence and revision(notes.read_body(note.id)) == evidence['revision']:
                        store.transition(write.operation_id, prior.status, operations.S.APPLIED,
                                         external_id=note.id, observed_revision=evidence['revision'])
                        self._finish_snapshot(write)
                        return result(True, 'reconciled; no write repeated', observed=evidence['revision'], note_id=note.id)
                    if prior.status == operations.S.APPLYING:
                        store.transition(write.operation_id, prior.status, operations.S.NEEDS_REVIEW,
                                         failure_code='post_write_divergence')
                    else:
                        store.record_inconclusive_review(write.operation_id)
                return result(False, 'operation requires review; no write repeated', observed=prior.observed_revision)
        if write.undo_reply:
            from . import conversation
            snapshot = undo.peek(write.note_id)
            valid_identity = (snapshot.snapshot_id if snapshot else None) == write.snapshot_id
            expected = undo.ASK_REPLY if write.title == workspace.ASK else undo.offer_text(snapshot)
            if write.recovery_receipt_id:
                expected = undo.COPY_REPLY
                if not self.dry_run:
                    prior_copy = operations.current().get(write.recovery_receipt_id)
                    saved = recovery.get(write.recovery_receipt_id) if prior_copy else None
                    if (not prior_copy or prior_copy.status not in (operations.S.APPLIED, operations.S.RECEIPTED)
                            or not saved or not saved.get('creation')
                            or saved['write'].get('recovery_note_id') != write.note_id
                            or saved['write'].get('snapshot_id') != write.snapshot_id):
                        return result(False, 'recovery copy has not been verified')
            if (not valid_identity or write.mode != 'insert'
                    or write.markdown != conversation.turn(expected)):
                return result(False, 'undo receipt does not match its saved snapshot')
        if write.recovery_note_id:
            snapshot = undo.peek(write.recovery_note_id)
            source = notes.get_note(write.recovery_note_id)
            if (not creation or not snapshot or not source or snapshot.snapshot_id != write.snapshot_id
                    or write.markdown != undo.copy_markdown(snapshot)
                    or write.title != undo.copy_title(source.title, snapshot)
                    or len(write.source_checks) != 1
                    or write.source_checks[0][0] != source.id
                    or write.source_checks[0][2] != '@notron ' + undo.copy_command(snapshot)):
                return result(False, 'explicit recovery-copy confirmation and saved snapshot are required')
        if creation and not permitted(note):
            return result(False, 'creation target exists or policy denied access')
        if not self._content_readable(content_sources) or not self._sources_current(write):
            return result(False, 'source missing, changed or ambiguous; nothing copied')
        old = notes.read_body(note.id) if note else ''
        # A target read can overlap revocation before this operation exists.
        # Never recreate revoked content in encrypted history after its purge.
        if not self._content_readable(content_sources):
            return result(False, 'source permission changed; nothing prepared')
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
                               target_id=write.note_id, expected_revision=write.expected_revision,
                               content_source_ids=content_sources, require_active_request=bool(envelope))
            if op.status in (operations.S.APPLIED, operations.S.RECEIPTED):
                return result(True, 'already applied; no write repeated', observed=op.observed_revision)
            if op.status != operations.S.PREPARED:
                return result(False, 'operation requires review; no write repeated', observed=op.observed_revision)

        def refuse(reason, code='revision_conflict', *, observed=None, alternative=None):
            if store:
                store.transition(write.operation_id, operations.S.PREPARED, operations.S.CANCELLED,
                                 failure_code=code, observed_revision=observed)
            return result(False, reason, observed=observed, alternative=alternative)

        if write.mode == 'restore':
            snapshot = undo.peek(write.note_id)
            if (not snapshot or snapshot.snapshot_id != write.snapshot_id
                    or not snapshot.after_revision
                    or snapshot.after_revision != write.expected_revision
                    or write.markdown != undo.restore_body(snapshot, note.title, note.folder, write.restore_receipt)):
                return refuse('saved snapshot proof or post-write revision does not match; use a recovery copy')
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
            if creation:
                new += markup.to_html(recovery.reference(write.operation_id))
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
            if note and write.mode != 'restore' and not write.undo_reply:
                undo.save(note.id, old, revision(new), write.operation_id)
        except Exception:
            if note:
                undo.discard(note.id, write.operation_id)
            return refuse('required backup failed; nothing written', 'write_failed')
        attempted = False
        try:
            # Commit APPLYING before the external effect, then make final checks.
            if store.get(write.operation_id).status != operations.S.PREPARED:
                return result(False, 'note policy changed during preparation; nothing written')
            recovery.put(op.request_id, write.operation_id + ':evidence',
                         {'revision': revision(new)}, content_sources)
            recovery.boundary('before_applying', write.operation_id)
            store.transition(write.operation_id, operations.S.PREPARED, operations.S.APPLYING)
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
            if write.mode == 'restore' or write.recovery_note_id or write.undo_reply:
                latest = undo.peek(write.recovery_note_id or write.note_id)
                if (latest.snapshot_id if latest else None) != write.snapshot_id:
                    store.transition(write.operation_id, operations.S.APPLYING, operations.S.NEEDS_REVIEW,
                                     failure_code='source_changed')
                    return result(False, 'saved snapshot changed during preparation; nothing written')
            if not permitted(current_note):
                if store.get(write.operation_id).status == operations.S.APPLYING:
                    store.transition(write.operation_id, operations.S.APPLYING, operations.S.NEEDS_REVIEW,
                                     failure_code='policy_changed')
                return result(False, 'note policy changed; nothing written')
            if not self._content_readable(content_sources):
                if store.get(write.operation_id).status == operations.S.APPLYING:
                    store.transition(write.operation_id, operations.S.APPLYING, operations.S.NEEDS_REVIEW,
                                     failure_code='policy_changed')
                return result(False, 'source permission changed; nothing written')
            if store.get(write.operation_id).status != operations.S.APPLYING:
                return result(False, 'operation invalidated before mutation; nothing written')
            # Apple exposes no CAS. Remote edits after this read remain a final race.
            attempted = True
            if note:
                notes.write_body(note.id, new)
                nid = note.id
            else:
                nid = notes.create_note(folder, new)
            recovery.boundary('after_external_save', write.operation_id)
            observed = revision(notes.read_body(nid))
            if observed != revision(new):
                store.transition(write.operation_id, operations.S.APPLYING, operations.S.NEEDS_REVIEW,
                                 external_id=nid, failure_code='post_write_divergence', observed_revision=observed)
                return result(False, 'post-write divergence; outcome needs review', observed=observed, note_id=nid)
            recovery.boundary('before_external_id', write.operation_id)
            store.transition(write.operation_id, operations.S.APPLYING, operations.S.APPLIED,
                             external_id=nid, observed_revision=observed)
            self._finish_snapshot(write)
        except Exception:
            # Do not retry: a timeout or failure after applying may have landed.
            if store.get(write.operation_id).status == operations.S.APPLYING:
                store.transition(write.operation_id, operations.S.APPLYING, operations.S.NEEDS_REVIEW,
                                 failure_code='unknown_outcome')
            return result(False, 'write outcome uncertain; review required')
        finally:
            if not attempted and note:
                undo.discard(note.id, write.operation_id)
        if note and note.folder != workspace.FOLDER and write.mode in ('append', 'insert') and not policy.current().can_file(note.id):
            policy.consume_reply()
        return result(True, 'written', observed=observed, note_id=nid)

    @staticmethod
    def _finish_snapshot(write):
        if write.mode == 'restore':
            undo.consume(write.note_id, write.snapshot_id)
        elif write.note_id and not write.undo_reply:
            undo.promote(write.note_id, write.operation_id)

    def do(self, action, *, about: str = "", request: str = "", content_sources=()) -> WriteResult:
        """Commit intent before EventKit; reconcile uncertain saves without replay."""
        from . import operations, requests, recovery, retention
        if policy.current().status != 'ready':
            return WriteResult(False, 'note policy not ready; actions paused')
        retention.require_ready()
        with write_transaction():
            store = operations.current()
            oid = action.operation_id
            prior = store.get(oid)
            if prior:
                if not prior.payload_ref or not self._content_readable(prior.content_source_ids or ()):
                    return WriteResult(False, 'action content unavailable; review required', operation_id=oid)
                persisted = recovery.get(oid)
                from .state import Action
                saved = Action(**persisted['action'])
                envelope = requests.active_request()
                expected_request = envelope.request_id if envelope else 'action:' + oid
                incoming = replace(action, target_id=saved.target_id) if action.target_id is None else action
                incoming_sources = set(content_sources)
                if envelope and envelope.note_id:
                    incoming_sources.add(envelope.note_id)
                if (prior.request_id != expected_request or asdict(incoming) != asdict(saved)
                        or tuple(sorted(incoming_sources)) != prior.content_source_ids):
                    raise operations.OperationConflict('Action identity already belongs to different work.')
                action = saved
                if prior.status in (operations.S.APPLIED, operations.S.RECEIPTED):
                    self._log('Action verified', operation_id=oid)
                    return WriteResult(True, self._said(action).strip(' —') or 'done',
                                       ref=prior.external_id, operation_id=oid)
                if prior.status == operations.S.APPLYING:
                    return self._reconcile_action(action, prior)
                if prior.status != operations.S.PREPARED:
                    return WriteResult(False, 'action outcome needs review', operation_id=oid)
            verdict = guard.check_action(action, about=about, request=request)
            if not verdict:
                self._log('**BLOCKED** action was refused')
                return WriteResult(False, verdict.reason, operation_id=oid)
            if self.dry_run:
                return WriteResult(True, 'dry run — nothing created')
            envelope = requests.active_request()
            sources = set(content_sources)
            if envelope and envelope.note_id:
                sources.add(envelope.note_id)
            if not self._content_readable(tuple(sources)):
                return WriteResult(False, 'source permission changed; action paused')
            if action.op == 'complete' and not action.target_id:
                hit = reminders.find_open(action.title)
                if hit is None:
                    return WriteResult(False, "couldn't find an open reminder with that title")
                action = replace(action, target_id=hit.id)
            if not prior:
                prior = recovery.put(envelope.request_id if envelope else 'action:' + oid, oid,
                                     {'action': asdict(action)}, sources)
            if envelope and not requests.current().validate_source(envelope):
                store.transition(oid, operations.S.PREPARED, operations.S.NEEDS_REVIEW,
                                 failure_code='source_changed')
                return WriteResult(False, 'source changed before action; review required', operation_id=oid)
            recovery.boundary('before_applying', oid)
            store.transition(oid, operations.S.PREPARED, operations.S.APPLYING)
            if not self._content_readable(prior.content_source_ids or ()) or store.get(oid).status != operations.S.APPLYING:
                return WriteResult(False, 'source permission changed; action paused')
            try:
                ref, detail = self._perform(action)
                recovery.boundary('after_external_save', oid)
                recovery.boundary('before_external_id', oid)
                store.transition(oid, operations.S.APPLYING, operations.S.APPLIED, external_id=ref)
            except Exception:
                # Leave APPLYING: a later exact adapter read may establish the ID.
                return WriteResult(False, 'action outcome uncertain; recovery pending', operation_id=oid)
            self._log('Action verified', operation_id=oid)
            return WriteResult(True, detail.strip(' —') or 'done', ref=ref, operation_id=oid)

    def _reconcile_action(self, action, op):
        from . import operations
        store = operations.current()
        try:
            if action.op == 'complete':
                matches = [action.target_id] if reminders.is_completed(action.target_id) else []
            else:
                adapter = reminders if action.kind == 'reminder' else calendar
                matches = adapter.find_by_operation(action.operation_id)
        except Exception:
            matches = []
        if len(matches) == 1 and matches[0]:
            store.transition(op.operation_id, operations.S.APPLYING, operations.S.APPLIED,
                             external_id=matches[0])
            self._log('Action verified', operation_id=op.operation_id)
            return WriteResult(True, self._said(action).strip(' —') or 'done', ref=matches[0],
                               operation_id=op.operation_id)
        store.transition(op.operation_id, operations.S.APPLYING, operations.S.NEEDS_REVIEW,
                         failure_code='unknown_outcome')
        return WriteResult(False, 'action outcome needs review; no save repeated', operation_id=op.operation_id)

    def _perform(self, action) -> tuple[str, str]:
        if action.kind == "reminder" and action.op == "create":
            ref = reminders.create(action.title, notes=self._action_notes(action),
                                   list_name=action.where, when_iso=action.when)
            return ref, self._said(action)

        if action.kind == "reminder" and action.op == "complete":
            reminders.complete(action.target_id)
            return action.target_id, f" — ticked off “{action.title}”"

        if action.kind == "event" and action.op == "create":
            ref = calendar.create(action.title, start_iso=action.when, end_iso=action.ends,
                                  calendar_name=action.where, notes=self._action_notes(action))
            return ref, self._said(action)

        raise ValueError(f"nothing to do for {action.kind}/{action.op}")

    @staticmethod
    def _action_notes(action):
        from .recovery import reference
        return action.notes.rstrip() + '\n' + reference(action.operation_id)

    @staticmethod
    def _said(action) -> str:
        from . import when as when_mod

        moment = when_mod.parse(action.when)
        return f" — {when_mod.human(moment)}" if moment else ""

    def _log(self, line: str, *, content_sources=(), operation_id=None) -> None:
        if not self.audit or self.dry_run:
            return
        try:
            from . import audit
            audit.enqueue(line, operation_id=operation_id)
            audit.drain(limit=1)
        except Exception:
            pass  # Primary APPLIED state never depends on an audit adapter.
