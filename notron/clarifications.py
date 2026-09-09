"""Encrypted, single-use action proposals. Conversation prose is never consent."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

from . import operations


@dataclass(frozen=True)
class PendingClarification:
    id: str
    request_id: str
    thread_id: str
    question: str
    candidate_ids: list[str]
    expires_at: datetime
    source_revision: str
    proposal: dict = field(default_factory=dict)
    candidates: dict[str, str] = field(default_factory=dict)
    slot: str = 'target'


@dataclass(frozen=True)
class Resolution:
    status: str
    request_id: str = ''
    proposal: dict = field(default_factory=dict)
    question: str = ''
    clarification_id: str = ''
    expires_at: str = ''


def resolve_reply(pending: PendingClarification, reply: str, live_revision: str) -> Resolution:
    if datetime.now(timezone.utc) >= pending.expires_at or live_revision != pending.source_revision:
        return Resolution('expired', question='That proposal has changed or expired. Please restate the action.')
    text = reply.strip().rstrip('.').casefold()
    candidates = [cid for cid in pending.candidate_ids
                  if text in {cid.casefold(), pending.candidates.get(cid, cid).casefold()}]
    if text in {'yes', 'confirm', 'yes please'} and len(pending.candidate_ids) == 1:
        candidates = list(pending.candidate_ids)
    proposal = dict(pending.proposal)
    if pending.slot == 'target' and len(candidates) == 1:
        proposal['target_id'] = candidates[0]
        if proposal.get('op') == 'create' and candidates[0] in pending.candidates:
            proposal['where'] = pending.candidates[candidates[0]]
    elif pending.slot == 'date':
        from . import when
        # Explicit calendar dates alone; relative replies would drift after delay.
        if not __import__('re').fullmatch(r'\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2})?', reply.strip()):
            return Resolution('clarify', question=pending.question)
        try:
            if when.parse(reply.strip()) is None:
                raise ValueError()
        except ValueError:
            return Resolution('clarify', question=pending.question)
        proposal['when'] = reply.strip()
    else:
        return Resolution('clarify', question=pending.question)
    return Resolution('resolved', pending.request_id, proposal, clarification_id=pending.id, expires_at=pending.expires_at.isoformat())


class ClarificationStore:
    def __init__(self, operations: operations.OperationStore):
        self.operations = operations
        with operations.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS clarification_replies (reply_hash TEXT PRIMARY KEY, payload_ref TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS clarifications (id TEXT PRIMARY KEY, payload_ref TEXT NOT NULL, consumed INTEGER NOT NULL DEFAULT 0)')

    def add(self, *, request_id, thread_id, question='', candidate_ids=(), source_revision='',
            proposal=None, candidates=None, slot='target', expires_at=None):
        c = PendingClarification(uuid4().hex, request_id, thread_id, question, list(candidate_ids),
                                 expires_at or datetime.now(timezone.utc)+timedelta(hours=24),
                                 source_revision, proposal or {}, candidates or {}, slot)
        value = asdict(c)
        value['expires_at'] = c.expires_at.isoformat()
        with self.operations.transaction() as db:
            # A new question replaces the previous proposal in this thread.
            for row in db.execute('SELECT * FROM clarifications WHERE consumed=0').fetchall():
                if self._read(row).thread_id == thread_id:
                    db.execute('UPDATE clarifications SET consumed=1 WHERE id=?', (row['id'],))
            ref = self.operations.put_payload(json.dumps(value, sort_keys=True).encode())
            db.execute('INSERT INTO clarifications(id,payload_ref) VALUES(?,?)', (c.id, ref))
        return c

    def _read(self, row):
        value = json.loads(self.operations.payload_store.read(row['payload_ref']))
        value['expires_at'] = datetime.fromisoformat(value['expires_at'])
        return PendingClarification(**value)

    def latest(self, thread_id):
        with self.operations.connection() as db:
            for row in db.execute('SELECT * FROM clarifications WHERE consumed=0 ORDER BY rowid DESC'):
                c = self._read(row)
                if c.thread_id == thread_id:
                    return c
        return None

    def resolve(self, id, *, reply, thread_id, live_revision, reply_request_id=None):
        with self.operations.transaction() as db:
            row = db.execute('SELECT * FROM clarifications WHERE id=? AND consumed=0', (id,)).fetchone()
            if not row:
                return Resolution('unmatched')
            c = self._read(row)
            if c.thread_id != thread_id:
                return Resolution('unmatched')
            result = resolve_reply(c, reply, live_revision)
            if result.status in {'resolved', 'expired'}:
                db.execute('UPDATE clarifications SET consumed=1 WHERE id=? AND consumed=0', (id,))
            if result.status == 'resolved' and reply_request_id:
                from .requests import revision
                ref = self.operations.put_payload(json.dumps(asdict(result)).encode())
                db.execute('INSERT INTO clarification_replies VALUES(?,?)', (revision(reply_request_id), ref))
            return result

    def for_reply(self, request_id):
        from .requests import revision
        with self.operations.connection() as db:
            row = db.execute('SELECT payload_ref FROM clarification_replies WHERE reply_hash=?', (revision(request_id),)).fetchone()
            if not row:
                return None
            result = Resolution(**json.loads(self.operations.payload_store.read(row[0])))
            if not result.expires_at or datetime.now(timezone.utc) >= datetime.fromisoformat(result.expires_at):
                return Resolution('expired', result.request_id, question='That proposal has expired. Please restate the action.')
            return result


def current():
    return ClarificationStore(operations.current())


def remember(state, proposal, question, *, candidates=None, slot='target'):
    """Persist the exact action, never an action inferred from an old answer."""
    from . import requests
    if not state.envelope or not state.conversation:
        return
    current().add(request_id=state.action_request_id or state.request_id,
                  thread_id=state.conversation.thread_id, question=question,
                  candidate_ids=list(candidates or {}), candidates=candidates,
                  proposal=proposal, slot=slot,
                  source_revision=requests.revision(state.action_request or state.request))


def resume(state):
    """Return True when a saved action reply was handled before model routing."""
    from . import requests
    from .state import Action
    if not state.envelope or not state.conversation:
        return False
    store = current()
    result = store.for_reply(state.request_id)
    pending = store.latest(state.conversation.thread_id) if not result else None
    if not result and not pending:
        return False
    if pending:
        # A new question is not a response to an old list/date proposal.
        import re
        text_reply = state.request.strip().rstrip('.').casefold()
        choices = {str(v).casefold() for v in (*pending.candidate_ids, *pending.candidates.values())}
        if text_reply not in choices | {'yes', 'no', 'confirm', 'yes please'} and not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2})?', text_reply):
            return False
    origin_id = result.request_id if result else pending.request_id
    origin = requests.current().get(origin_id)
    text = origin.envelope.text if origin and origin.envelope else ''
    # The original source must remain before this reply in the same bounded
    # thread. Missing/deleted/edited context cannot authorize the saved action.
    from .conversation import TAG
    normalize = lambda value: TAG.sub('', value).strip()
    visible = any(t.role == 'user' and normalize(t.text) == normalize(text)
                  for t in state.conversation.turns) if text else False
    if not visible:
        state.response_mode = 'clarify'
        state.answer = 'The original action has changed or is no longer in this conversation. Please restate it.'
        state.intent = 'question'
        return True
    if not result:
        result = store.resolve(pending.id, reply=state.request,
                               thread_id=state.conversation.thread_id,
                               live_revision=requests.revision(text), reply_request_id=state.request_id)
    if result.status != 'resolved':
        state.response_mode = 'clarify'
        state.answer = result.question or 'Please restate the action you want.'
        state.intent = 'question'
        return True
    state.clarification_id = result.clarification_id
    state.action_request_id = result.request_id
    state.action_request = text
    state.actions = [Action(**result.proposal)]
    state.actions[0].origin_request_id = result.request_id
    state.intent = 'schedule' if state.actions[0].kind == 'event' else 'remind'
    return True
