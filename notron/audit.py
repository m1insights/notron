"""Best-effort, bounded audit delivery, independent of primary effects.

Only fixed outcome labels are retained; user titles, exception strings and model
text never enter the queue. Seven days / 200 records bound both content and rows.
"""
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from uuid import uuid4

from . import operations, requests, recovery

MAX_RECORDS = 200
RETENTION_DAYS = 7
BATCH_SIZE = 5


def _records(store):
    with store.connection() as db:
        return [store._record(r) for r in db.execute(
            "SELECT * FROM operations WHERE operation_id LIKE 'audit:%' "
            "AND operation_id NOT LIKE '%:%:%' ORDER BY created_at,operation_id")]


def prune():
    store = operations.current()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
    records = _records(store)
    remove = {r.operation_id for r in records if r.created_at < cutoff}
    remove.update(r.operation_id for r in records[:max(0, len(records) - MAX_RECORDS)])
    with store.transaction() as db:
        for oid in remove:
            # Child write/evidence payloads belong to this audit delivery only.
            db.execute('DELETE FROM operations WHERE operation_id=? OR operation_id LIKE ?', (oid, oid + ':%'))
            db.execute("DELETE FROM requests WHERE payload_ref IS NULL AND request_id NOT IN "
                       "(SELECT request_id FROM operations) AND (request_id=? OR request_id LIKE ?)",
                       (oid, 'write:' + oid + ':%'))
    store.prune_payloads()


def enqueue(line, operation_id=None):
    from hashlib import sha256
    oid = 'audit:' + (sha256(operation_id.encode()).hexdigest() if operation_id else uuid4().hex)
    if operations.current().get(oid):
        return oid
    label = 'BLOCKED / NEEDS REVIEW' if 'BLOCKED' in line or 'FAILED' in line else 'Operation verified'
    recovery.put(oid, oid, {'outcome': label}, ())
    prune()
    return oid


def pending_count():
    return sum(r.status != operations.S.RECEIPTED for r in _records(operations.current()))


def drain(limit=BATCH_SIZE):
    from .executor import Executor, capture_write, write_transaction
    from .state import Write
    from . import workspace
    with write_transaction(), requests.execution(None):
        prune()
        store = operations.current()
        # APPLIED metadata is durable before enqueue. A local enqueue failure or
        # a crash in that gap cannot erase the opportunity for bounded backfill.
        cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
        with store.connection() as db:
            primary = [r[0] for r in db.execute(
                "SELECT operation_id FROM operations WHERE status IN ('applied','receipted') "
                "AND operation_id NOT LIKE 'audit:%' AND updated_at>=? "
                "ORDER BY updated_at DESC LIMIT ?", (cutoff, MAX_RECORDS))]
        for oid in primary:
            enqueue('Operation verified', operation_id=oid)
        records = [r for r in _records(store) if r.status in (operations.S.PREPARED, operations.S.APPLYING, operations.S.APPLIED) and r.payload_ref]
        for record in records[:limit]:
            try:
                oid = record.operation_id + ':write'
                existing = recovery.get(oid)
                if existing:
                    target = Write(**existing['write'])
                else:
                    target = capture_write(workspace.LOG, mode='append')
                    if not target.note_id:
                        continue
                    data = recovery.get(record.operation_id)
                    target = replace(target, operation_id=oid, markdown=
                                     f"{record.created_at[:16]} — {data['outcome']}")
                result = Executor(audit=False).apply_write(target)
                if result.ok:
                    status = record.status
                    for before, after in ((operations.S.PREPARED, operations.S.APPLYING),
                                          (operations.S.APPLYING, operations.S.APPLIED),
                                          (operations.S.APPLIED, operations.S.RECEIPTED)):
                        if status == before:
                            store.transition(record.operation_id, before, after)
                            status = after
            except Exception:
                continue  # No primary operation is touched by audit recovery.
