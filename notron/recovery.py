"""Encrypted completed-node checkpoints and conservative effect reconciliation.

A checkpoint is cached work, never a permission capability. Losing its content or
an inconclusive external read requires review, not fresh inference or another save.
"""
from dataclasses import asdict
from hashlib import sha256
import json

from . import operations, requests


def boundary(name: str, operation_id: str) -> None:
    """Instrumentation seam for crash tests; production has no injected handler."""


def reference(operation_id: str) -> str:
    digest = sha256(operation_id.encode()).hexdigest()
    return 'notron-operation:' + '-'.join(digest[i:i+8] for i in range(0, len(digest), 8))


def put(request_id, operation_id, value, sources=()):
    from .executor import Executor
    sources = tuple(sorted(set(sources)))
    if not Executor._content_readable(sources):
        raise ValueError('Recovery content source is no longer readable.')
    envelope = requests.active_request()
    active = bool(envelope and envelope.request_id == request_id)
    data = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    return operations.current().prepare(request_id, operation_id, sha256(data).hexdigest(),
                                         payload=data, content_source_ids=sources,
                                         require_active_request=active)


def get(operation_id):
    store = operations.current()
    op = store.get(operation_id)
    if not op:
        return None
    from .executor import Executor
    if op.payload_ref is None or not Executor._content_readable(op.content_source_ids or ()):
        raise ValueError('Recovery content unavailable; review required.')
    return json.loads(store.payload(operation_id))


def state_sources(state):
    sources = {p.note_id for p in [*state.context, *state.system_sources.values()] if p.note_id}
    if state.source_note_id:
        sources.add(state.source_note_id)
    from .executor import Executor
    for w in [*state.write_targets.values(), *state.writes]:
        sources.update(Executor._content_sources(w))
    return sorted(sources)


def checkpoint(state, name):
    value = asdict(state)
    value.pop('envelope')  # The immutable request owns its separately encrypted envelope.
    try:
        put(state.request_id, state.request_id + ':checkpoint:' + name, value, state_sources(state))
    except ValueError:
        return False
    return True


def latest(envelope, order):
    from .state import State, Write, Action
    from .outbound import Passage
    for name in reversed(order):
        data = get(envelope.request_id + ':checkpoint:' + name)
        if data is None:
            continue
        data['actions'] = [Action(**a) for a in data['actions']]
        data['writes'] = [Write(**w) for w in data['writes']]
        data['write_targets'] = {k: Write(**v) for k, v in data['write_targets'].items()}
        data['context'] = [Passage(**p) for p in data['context']]
        from .attachments import Attachment
        data['carried'] = [Attachment(**a) for a in data.get('carried', [])]
        data['system_sources'] = {k: Passage(**v) for k, v in data['system_sources'].items()}
        if data['reply_to']:
            data['reply_to'] = tuple(data['reply_to'])
        return name, State(envelope=envelope, **data)
    return None


def available(record):
    # Revocation, edited occurrences and ambiguous observations are never retries.
    if not record or not record.envelope or record.status not in {'running', 'needs_review'}:
        return False
    if record.failure_code not in {None, 'unknown_outcome', 'interrupted'}:
        return False
    return any(op.request_id == record.request_id and (':checkpoint:' in op.operation_id or op.operation_id.endswith((':filing_plan', ':approval_plan')))
               and op.payload_ref for op in operations.current().pending())


def claim(record):
    if not available(record):
        return False
    with operations.current().transaction() as db:
        return bool(db.execute("UPDATE requests SET status='running',failure_code=NULL,updated_at=? "
                              "WHERE request_id=? AND status=? AND payload_ref IS NOT NULL "
                              "AND (failure_code IS NULL OR failure_code IN ('unknown_outcome','interrupted'))",
                              (operations.now(), record.request_id, record.status)).rowcount)


def serialized(fn):
    """Share worker ownership; nested graph/filing work retains the same lease."""
    from functools import wraps
    @wraps(fn)
    def run(envelope, *args, **kwargs):
        from .health import WorkerLock
        with WorkerLock(blocking=True):
            return fn(envelope, *args, **kwargs)
    return run
