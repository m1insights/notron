"""Persist request identity before inference, including distinct Notes occurrences.

An envelope is data, never a permission capability. Only the watcher may open the
existing scoped explicit-reply policy context after checking the observed note.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from difflib import SequenceMatcher
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import operations
from .operations import OperationConflict, OperationStore, identity, now
from .securestore import StorageError


def revision(body: str) -> str:
    return sha256(body.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class RequestEnvelope:
    version: int
    request_id: str
    source: str
    text: str
    captured_at: datetime | None
    observed_at: datetime
    timezone: str
    capture_confidence: str
    note_id: str | None = None
    source_revision: str | None = None
    thread_id: str | None = None
    # Source context is encrypted with the envelope, never copied into SQL.
    source_text: str = ''
    reply_to: tuple | None = None
    here: str = ''
    source_modified: str = ''

    def __post_init__(self):
        identity(self.request_id)
        if self.version != 1 or self.source not in {'ask', 'mention', 'shortcut', 'cli', 'morning'}:
            raise ValueError('Unsupported request envelope.')
        if not isinstance(self.text, str):
            raise ValueError('Request text is required.')
        for stamp in (self.observed_at, self.captured_at):
            if stamp is not None and (not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None):
                raise ValueError('Request timestamps must be timezone-aware.')
        if self.observed_at is None or self.capture_confidence not in {'explicit', 'observed_only'}:
            raise ValueError('Invalid capture metadata.')
        if (self.captured_at is not None) != (self.capture_confidence == 'explicit'):
            raise ValueError('Capture confidence requires matching timestamp evidence.')
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise ValueError('Request timezone must be an IANA identifier.') from None

    def encode(self) -> bytes:
        value = asdict(self)
        value['observed_at'] = self.observed_at.isoformat()
        value['captured_at'] = self.captured_at.isoformat() if self.captured_at else None
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()

    @classmethod
    def decode(cls, data: bytes) -> RequestEnvelope:
        try:
            value = json.loads(data)
            value['observed_at'] = datetime.fromisoformat(value['observed_at'])
            value['captured_at'] = datetime.fromisoformat(value['captured_at']) if value['captured_at'] else None
            value['reply_to'] = tuple(value['reply_to']) if value.get('reply_to') else None
            return cls(**value)
        except (ValueError, TypeError, KeyError):
            raise StorageError('Request payload is invalid; processing paused.') from None


@dataclass(frozen=True)
class RequestRecord:
    request_id: str
    status: str
    envelope: RequestEnvelope | None
    failure_code: str | None


def local_timezone() -> str:
    configured = os.environ.get('TZ')
    if configured:
        # Reject POSIX timezone expressions/abbreviations that cannot support
        # date-specific IANA rules for deferred requests.
        try:
            ZoneInfo(configured)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('Configured timezone must be an IANA identifier.') from None
        return configured
    path = str(Path('/etc/localtime').resolve())
    if '/zoneinfo/' in path:
        name = path.split('/zoneinfo/', 1)[1]
        ZoneInfo(name)
        return name
    raise StorageError('An IANA timezone is required before capturing requests.')


def create(text: str, *, source='cli', request_id=None, note_id=None,
           source_revision=None, timezone_name=None, **context) -> RequestEnvelope:
    # CLI captures are explicit. Notes line capture times are unknowable.
    stamp = datetime.now(timezone.utc)
    captured = stamp if source in {'cli', 'morning'} else None
    return RequestEnvelope(1, request_id or uuid4().hex, source, text, captured,
                           stamp, timezone_name or local_timezone(), 'explicit' if captured else 'observed_only',
                           note_id, source_revision, **context)


class RequestStore:
    def __init__(self, operations: OperationStore):
        self.operations = operations

    def _read(self, row) -> RequestRecord | None:
        if row is None:
            return None
        envelope = None
        if row['payload_ref']:
            raw = self.operations.payload_store.read(row['payload_ref'])
            if sha256(raw).hexdigest() != row['payload_hash']:
                raise StorageError('Request digest mismatch; processing paused.')
            envelope = RequestEnvelope.decode(raw)
            if envelope.request_id != row['request_id'] or envelope.note_id != row['note_id']:
                raise StorageError('Request identity mismatch; processing paused.')
        return RequestRecord(row['request_id'], row['status'], envelope, row['failure_code'])

    def get(self, request_id: str) -> RequestRecord | None:
        with self.operations.connection() as db:
            return self._read(db.execute('SELECT * FROM requests WHERE request_id=?', (request_id,)).fetchone())

    def _save(self, db, envelope: RequestEnvelope, *, status='prepared'):
        raw = envelope.encode()
        ref = self.operations.put_payload(raw)
        stamp = now()
        db.execute('''INSERT INTO requests(request_id,payload_hash,payload_ref,note_id,source_revision,status,created_at,updated_at)
                      VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(request_id) DO UPDATE SET
                      payload_hash=excluded.payload_hash,payload_ref=excluded.payload_ref,
                      source_revision=excluded.source_revision,updated_at=excluded.updated_at''',
                   (envelope.request_id, sha256(raw).hexdigest(), ref, envelope.note_id,
                    envelope.source_revision, status, stamp, stamp))

    def capture(self, envelope: RequestEnvelope) -> RequestEnvelope:
        with self.operations.transaction() as db:
            row = db.execute('SELECT * FROM requests WHERE request_id=?', (envelope.request_id,)).fetchone()
            if row:
                existing = self._read(row)
                if existing.envelope is None:
                    raise OperationConflict('Request identity has no replayable payload.')
                # Caller retries may have fresh observation/capture timestamps;
                # identity still belongs to the originally captured request.
                old, new = asdict(existing.envelope), asdict(envelope)
                for key in ('observed_at', 'captured_at'):
                    old.pop(key), new.pop(key)
                if old != new:
                    raise OperationConflict('Request identity already belongs to different work.')
                return existing.envelope
            self._save(db, envelope)
        return envelope

    def pending(self) -> list[RequestRecord]:
        with self.operations.connection() as db:
            return [self._read(row) for row in db.execute(
                "SELECT * FROM requests WHERE status IN ('prepared','running','needs_review') ORDER BY created_at")]

    def claim(self, request_id: str) -> bool:
        with self.operations.transaction() as db:
            row = db.execute('SELECT * FROM requests WHERE request_id=?', (request_id,)).fetchone()
            record = self._read(row)
            if not record or not record.envelope or record.status != 'prepared':
                return False
            return db.execute("UPDATE requests SET status='running',updated_at=? WHERE request_id=? AND status='prepared'",
                              (now(), request_id)).rowcount == 1

    def finish(self, request_id: str, *, needs_review=False):
        with self.operations.transaction() as db:
            count = db.execute("UPDATE requests SET status=?,updated_at=?,failure_code=? WHERE request_id=? AND status='running'",
                               ('needs_review' if needs_review else 'completed', now(),
                                'unknown_outcome' if needs_review else None, request_id)).rowcount
            if not count:
                # A policy update (including explicit new-home creation) may have
                # already purged this running request. Preserve its review tombstone.
                row = db.execute('SELECT status FROM requests WHERE request_id=?', (request_id,)).fetchone()
                if row and row['status'] == 'needs_review':
                    return
                raise OperationConflict('Request state changed; processing paused.')

    def observe(self, note_id, body, questions, *, source, title, folder, modified='', legacy_review=False) -> list[RequestEnvelope]:
        """Reconcile an entire observed set, before settling or calling inference.

        A unique unchanged anchor follows unrelated edits. Identical copies or
        reordered pending turns cannot be safely matched across an edit; pause
        them for review. Completed occurrences remain recognized while present.
        A remove/answer observation followed by resubmission creates a new ID.
        """
        from . import conversation, notedoc
        identity(note_id)
        if source not in {'ask', 'mention'}:
            raise ValueError('Occurrences require a Notes source.')
        rev = revision(body)
        blocks = [revision(text) for text in notedoc.texts(body)]
        items = [{'anchor': revision(q.text), 'after': q.after, 'raw': q.text} for q in questions]
        if len({q.after for q in questions}) != len(questions):
            raise ValueError('Occurrence spans must be distinct.')
        with self.operations.transaction() as db:
            row = db.execute('SELECT * FROM observations WHERE note_id=? AND source=?', (note_id, source)).fetchone()
            if row and row['payload_ref'] is None:
                raise OperationConflict('Source history was purged; explicit review is required.')
            snapshot = json.loads(self.operations.payload_store.read(row['payload_ref'])) if row else []
            # Accept the initial v1 list representation conservatively; only
            # newer snapshots have block evidence for duplicate-span rebasing.
            old = snapshot['items'] if isinstance(snapshot, dict) else snapshot
            old_blocks = snapshot.get('blocks', []) if isinstance(snapshot, dict) else []
            if row and row['revision'] == rev:
                # A changed caller observation with identical body is not trusted.
                if [(x['anchor'], x['after']) for x in old] != [(x['anchor'], x['after']) for x in items]:
                    raise OperationConflict('Source occurrence observation is inconsistent.')
                return [self._read(db.execute('SELECT * FROM requests WHERE request_id=?', (x['id'],)).fetchone()).envelope for x in old]
            # The latest visible observation is not the uncertainty history.
            # Temporarily removing a running/review-needed turn cannot erase its
            # durable identity and authorize the same work when it reappears.
            visible_ids = {x['id'] for x in old}
            for historical in db.execute("SELECT * FROM requests WHERE note_id=? AND status IN ('running','needs_review') AND payload_ref IS NOT NULL", (note_id,)).fetchall():
                if historical['request_id'] in visible_ids:
                    continue
                record = self._read(historical)
                envelope = record.envelope
                if envelope.source == source:
                    old.append({'id': envelope.request_id,
                                'anchor': revision(envelope.source_text or envelope.text),
                                'after': envelope.reply_to[2] if envelope.reply_to else -1})
            old_by_id = {x['id']: self._read(db.execute('SELECT * FROM requests WHERE request_id=?', (x['id'],)).fetchone()) for x in old}
            # Rebase all spans before matching either pending turns or receipts.
            # A different request can be inserted/removed without changing the
            # identity of an unchanged anchor group elsewhere in the note.
            span_map = {}
            if old_blocks:
                for old_start, new_start, length in SequenceMatcher(None, old_blocks, blocks, autojunk=False).get_matching_blocks():
                    span_map.update((old_start + offset, new_start + offset) for offset in range(length))
            def mapped_after(item):
                if item['id'] not in visible_ids:
                    return None  # historical uncertainty predates this snapshot
                return span_map.get(item['after']) if old_blocks else item['after']

            # Remove only a ledger-completed occurrence whose rebased span is
            # now visibly answered, preserving its identical pending sibling.
            old = [x for x in old if not (
                old_by_id[x['id']].status == 'completed'
                and mapped_after(x) in self._answered_anchor_spans(body, old_by_id[x['id']].envelope.source_text)
                and not any(item['after'] == mapped_after(x) for item in items))]
            counts, previous = Counter(x['anchor'] for x in items), Counter(x['anchor'] for x in old)
            stable_anchors = set()
            for anchor in previous:
                old_group = [x for x in old if x['anchor'] == anchor]
                new_group = [x for x in items if x['anchor'] == anchor]
                if (old_blocks and len(old_group) == len(new_group)
                        and all(mapped_after(before) == after['after']
                                for before, after in zip(old_group, new_group))):
                    stable_anchors.add(anchor)
            active = {'prepared', 'running', 'needs_review'}
            overlap_old = [x['anchor'] for x in old if counts[x['anchor']] and old_by_id[x['id']].status in active]
            overlap_new = [x['anchor'] for x in items if x['anchor'] in overlap_old]
            reordered = overlap_new != overlap_old and Counter(overlap_new) == Counter(overlap_old)
            uncertain_removed = any(counts[x['anchor']] == 0 and old_by_id[x['id']].status in {'running', 'needs_review'} for x in old)
            used, observations, envelopes = set(), [], []
            for item in items:
                candidates = [x for x in old if x['anchor'] == item['anchor'] and x['id'] not in used]
                duplicates = counts[item['anchor']] > 1 or previous[item['anchor']] > 1
                ambiguous = duplicates and item['anchor'] not in stable_anchors and any(old_by_id[x['id']].status in active for x in old if x['anchor'] == item['anchor'])
                match = None
                if len(candidates) == 1 and not duplicates:
                    match = candidates[0]
                else:
                    exact = [x for x in candidates if (mapped_after(x) if item['anchor'] in stable_anchors else x['after']) == item['after']]
                    if len(exact) == 1:
                        match = exact[0]
                    elif candidates:
                        ambiguous = True
                # A receipt may have landed and a new identical turn arrived
                # between two polls. The completed old turn is not the current
                # unanswered occurrence merely because its wording matches.
                if (match and old_by_id[match['id']].status == 'completed'
                        and match['after'] != item['after']
                        and self._has_answered_anchor(body, item['raw'])):
                    match = None
                needs_review = ambiguous or reordered or uncertain_removed
                if match:
                    used.add(match['id'])
                    record = old_by_id[match['id']]
                    envelope = record.envelope
                    # Pending source observations can move before inference.
                    # Once claimed, preserve the exact input of the active run.
                    if record.status == 'prepared':
                        envelope = replace(envelope, source_revision=rev,
                                           reply_to=(title, folder, item['after']), source_modified=modified,
                                           here=self._context(body) if source == 'mention' else '')
                        self._save(db, envelope)
                else:
                    text = conversation.strip_tag(conversation.tagged_lines(item['raw'])) if source == 'mention' else item['raw']
                    envelope = create(text, source=source, note_id=note_id, source_revision=rev,
                                      source_text=item['raw'], reply_to=(title, folder, item['after']),
                                      source_modified=modified,
                                      here=self._context(body) if source == 'mention' else '')
                    self._save(db, envelope, status='needs_review' if needs_review or legacy_review else 'prepared')
                    if legacy_review:
                        db.execute("UPDATE requests SET failure_code='legacy_unknown' WHERE request_id=?", (envelope.request_id,))
                if needs_review:
                    db.execute("UPDATE requests SET status='needs_review',failure_code='ambiguous_occurrence',updated_at=? "
                               "WHERE request_id=? AND status IN ('prepared','running','needs_review')", (now(), envelope.request_id))
                observations.append({'anchor': item['anchor'], 'after': item['after'], 'id': envelope.request_id})
                envelopes.append(envelope)
            for item in old:
                if item['id'] in used:
                    continue
                record = old_by_id[item['id']]
                if record.status not in active:
                    continue
                copied = counts[item['anchor']] > 0
                review = copied or record.status != 'prepared'
                db.execute('UPDATE requests SET status=?,failure_code=?,updated_at=? WHERE request_id=?',
                           ('needs_review' if review else 'cancelled', 'ambiguous_occurrence' if copied else 'source_changed', now(), item['id']))
            ref = self.operations.put_payload(json.dumps({'items': observations, 'blocks': blocks}).encode())
            db.execute('INSERT INTO observations VALUES(?,?,?,?) ON CONFLICT(note_id,source) DO UPDATE SET '
                       'revision=excluded.revision,payload_ref=excluded.payload_ref', (note_id, source, rev, ref))
        self.prune_payloads()
        return envelopes

    @staticmethod
    def _answered_anchor_spans(body, raw):
        from . import conversation, notedoc
        indexed = [(index, text.strip()) for index, text in enumerate(notedoc.texts(body)) if text.strip()]
        texts = [text for _, text in indexed]
        spans = set()
        for end in range(len(texts)):
            for start in range(end, -1, -1):
                anchor = '\n'.join(texts[start:end + 1])
                if len(anchor) > len(raw):
                    break
                if anchor == raw:
                    after = end + 1
                    if after < len(texts) and texts[after] == conversation.QA_RULE:
                        after += 1
                    if after < len(texts) and texts[after].startswith(conversation.SIGNATURE):
                        spans.add(indexed[end][0])
        return spans

    @classmethod
    def _has_answered_anchor(cls, body, raw):
        return bool(cls._answered_anchor_spans(body, raw))

    @staticmethod
    def _context(body):
        from . import markup, privacy
        return privacy.redact(markup.to_text(body))[:4000]

    def validate_source(self, envelope: RequestEnvelope) -> bool:
        """Exact admission check after settling, before any model/external call.

        Source edits between inference and a write remain Task 2's responsibility.
        The next full observation can rebind a prepared unchanged occurrence.
        """
        if envelope.source not in {'ask', 'mention'} or not envelope.source_revision:
            return True
        from . import notes, policy
        if not envelope.note_id or not policy.current().can_read(envelope.note_id):
            raise policy.PolicyError('Request source is not readable.')
        return revision(notes.read_body(envelope.note_id)) == envelope.source_revision

    def purge_sources(self, live: set[str] | None = None, *, all_content=False):
        """Keep durable tombstones, remove revoked/deleted encrypted content."""
        from . import policy
        snap = policy.current()
        with self.operations.transaction() as db:
            rows = db.execute('SELECT request_id,note_id FROM requests WHERE payload_ref IS NOT NULL').fetchall()
            removed = {row['request_id'] for row in rows if all_content or (row['note_id'] and
                       (not snap.can_read(row['note_id']) or (live is not None and row['note_id'] not in live)))}
            for request_id in removed:
                db.execute("UPDATE requests SET payload_ref=NULL,payload_hash=NULL,status=CASE WHEN status='completed' THEN status ELSE 'needs_review' END,"
                           "failure_code='policy_changed',updated_at=? WHERE request_id=?", (now(), request_id))
                db.execute("UPDATE operations SET payload_ref=NULL,status=CASE WHEN status IN ('receipted','cancelled') THEN status ELSE 'needs_review' END,"
                           "failure_code='policy_changed',updated_at=? WHERE request_id=?", (now(), request_id))
            for row in db.execute('SELECT * FROM operations WHERE payload_ref IS NOT NULL').fetchall():
                operation = self.operations._record(row)
                sources = operation.content_source_ids
                if all_content or sources is None or any(not snap.can_read(nid) or
                        (live is not None and nid not in live) for nid in sources):
                    db.execute("UPDATE operations SET payload_ref=NULL,status=CASE WHEN status IN ('receipted','cancelled') THEN status ELSE 'needs_review' END,"
                               "failure_code='policy_changed',updated_at=? WHERE operation_id=?", (now(), operation.operation_id))
            for row in db.execute('SELECT note_id,source FROM observations').fetchall():
                if all_content or not snap.can_read(row['note_id']) or (live is not None and row['note_id'] not in live):
                    db.execute('UPDATE observations SET payload_ref=NULL WHERE note_id=? AND source=?', tuple(row))
        self.prune_payloads()

    def prune_payloads(self):
        self.operations.prune_payloads()


def current() -> RequestStore:
    return RequestStore(operations.current())


_ACTIVE = ContextVar('active_execution_request', default=None)


def active_request() -> RequestEnvelope | None:
    """Execution correlation only; this value grants no read/write permission."""
    return _ACTIVE.get()


@contextmanager
def execution(envelope: RequestEnvelope):
    token = _ACTIVE.set(envelope)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


@dataclass(frozen=True)
class JobResult:
    request_id: str
    status: str
    result: object | None = None
    message: str = ''


def _run_job(envelope: RequestEnvelope, job, *, dry_run=False) -> JobResult:
    """Admit a non-graph filing batch. Uncertain jobs require review, never replay."""
    from . import policy, retention, recovery
    policy.require_ready()
    retention.reconcile()
    store = current()
    envelope = store.capture(envelope)
    record = store.get(envelope.request_id)
    resuming = recovery.available(record)
    if record.status != 'prepared' and not resuming:
        return JobResult(envelope.request_id, record.status,
                         message='This request was already completed or needs review.')
    if not resuming and not store.validate_source(envelope):
        return JobResult(envelope.request_id, 'prepared', message='Source changed; waiting for a fresh observation.')
    if not dry_run and not (recovery.claim(record) if resuming else store.claim(envelope.request_id)):
        return JobResult(envelope.request_id, 'needs_review', message='This request is active or needs review.')
    try:
        with execution(envelope):
            result = job()
    except BaseException:
        if not dry_run:
            store.finish(envelope.request_id, needs_review=True)
        raise
    if not dry_run:
        store.finish(envelope.request_id, needs_review=any(r.startswith('✗') for r in result.results))
    return JobResult(envelope.request_id, store.get(envelope.request_id).status, result)


# Lazy decoration avoids the requests/recovery import cycle.
def run_job(envelope, job, *, dry_run=False):
    from .recovery import serialized
    return serialized(_run_job)(envelope, job, dry_run=dry_run)
