"""Single-worker admission and an encrypted, durable queue for CLI producers."""
from __future__ import annotations

from argparse import Namespace
from contextlib import contextmanager, redirect_stdout
from functools import wraps
from hashlib import sha256
import json
import io
import time
from uuid import uuid4

from . import credentials, operations, policy, requests, retention
from .health import HealthStore, Heartbeat, WorkerLock
from .securestore import EncryptedStore, StorageError

KINDS = {'ask', 'plan', 'file', 'morning', 'index', 'care', 'reflect'}


class QueuedPayloadError(StorageError):
    """One quarantined job must not block unrelated queued work."""


def require_owner():
    if not WorkerLock.owned():
        raise RuntimeError('Worker ownership is required before execution.')


def probe():
    """Run finite native/provider checks on startup and after interruptions.

    This runs on the serial worker, never on the heartbeat thread. Listing models
    is an authenticated connectivity check and performs no inference.
    """
    from . import permissions
    from .brain import Brain
    if credentials._provider is None:
        credentials.startup()
    retention.require_ready()
    policy.require_ready()
    if not all(check.ok for check in permissions.check()):
        raise policy.PolicyError('Native app permission requires attention.')
    brain = Brain.from_credentials()
    brain.available_models()
    return brain


def failure(exc):
    from .network import ProviderConnectivityError, ProviderCooldownError, ProviderStateError
    if isinstance(exc, credentials.CredentialUnavailable):
        state, reason = 'paused', 'keychain_locked'
    elif isinstance(exc, (StorageError, ProviderStateError)):
        state, reason = 'paused', 'storage_unavailable'
    elif isinstance(exc, ProviderCooldownError):
        state, reason = 'offline', 'provider_cooldown'
    elif isinstance(exc, (ProviderConnectivityError, ConnectionError)):
        state, reason = 'offline', 'network_unavailable'
    elif isinstance(exc, policy.PolicyError):
        state, reason = 'permission_needed', 'permission_denied'
    else:
        state, reason = 'error', 'worker_failed'
    HealthStore().update(state=state, reason_code=reason)


class Queue:
    def __init__(self):
        self.health = HealthStore()

    def _payloads(self):
        return EncryptedStore(self.health.path.parent / 'worker-payloads', credentials.storage_key())

    @contextmanager
    def admission(self):
        import os, fcntl
        fd = os.open(self.health.path.parent / 'queue-admission.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def pending(self):
        with self.health.connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM jobs WHERE status IN ('queued','running','needs_review') ORDER BY created_at,job_id")]

    def get(self, job_id):
        with self.health.connection() as db:
            row = db.execute('SELECT * FROM jobs WHERE job_id=?', (job_id,)).fetchone()
            return dict(row) if row else None

    def payload(self, job):
        if not job['payload_ref']:
            raise StorageError('Queued content is unavailable; review required.')
        raw = self._payloads().read(job['payload_ref'])
        if sha256(raw).hexdigest() != job['payload_hash']:
            raise StorageError('Queued content validation failed.')
        value = json.loads(raw)
        if value.get('kind') != job['kind']:
            raise StorageError('Queued job identity mismatch.')
        return value

    def enqueue(self, kind, args):
        with self.admission():
            return self._enqueue(kind, args)

    def _enqueue(self, kind, args):
        if kind not in KINDS:
            raise ValueError('Unsupported worker job.')
        # No key/plaintext fallback: an unavailable key leaves existing work intact
        # and refuses to pretend a new producer was durably accepted.
        credentials.storage_key()
        policy.require_ready()
        args = {k: v for k, v in args.items() if k not in {'fn', 'cmd', '_envelope'}}
        args_hash = sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
        envelope = None
        if kind in {'ask', 'plan', 'file', 'morning'}:
            text = (' '.join(args['request']) if kind == 'ask' else
                    ('plan my week' if args.get('week') else 'plan my day') if kind == 'plan' else
                    'plan my day' if kind == 'morning' else 'file my brain dump')
            envelope = requests.create(text, request_id=args.get('request_id'), source='morning' if kind == 'morning' else 'cli')
        job_id = (sha256((kind + ':' + args['request_id']).encode()).hexdigest()
                  if args.get('request_id') else uuid4().hex)
        value = {'kind': kind, 'args': args, 'envelope': json.loads(envelope.encode()) if envelope else None}
        raw = json.dumps(value, sort_keys=True).encode()
        with self.health.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM jobs WHERE job_id=?', (job_id,)).fetchone()
            if old:
                if old['args_hash'] != args_hash:
                    raise operations.OperationConflict('Queued identity belongs to different work.')
                db.commit()
                return dict(old)
            if db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running','needs_review')").fetchone()[0] >= 200:
                raise StorageError('Worker queue is full; review pending work before adding more.')
            ref = 'job-' + job_id
            self._payloads().write(ref, raw)
            stamp = time.time()
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',
                       (job_id, kind, ref, sha256(raw).hexdigest(), args_hash, 'queued',
                        envelope.request_id if envelope else None, stamp, stamp))
            db.commit()
        if envelope:
            requests.current().capture(envelope)
        return self.get(job_id)

    def claim(self, job_id):
        require_owner()
        if self.health.paused:
            return False
        with self.health.connection() as db:
            return bool(db.execute("UPDATE jobs SET status='running',updated_at=? WHERE job_id=? AND status='queued'",
                                   (time.time(), job_id)).rowcount)

    def finish(self, job_id, status):
        require_owner()
        if status not in {'completed', 'needs_review', 'queued'}:
            raise ValueError('Invalid queue result.')
        job = self.get(job_id)
        with self.health.connection() as db:
            db.execute('UPDATE jobs SET status=?,updated_at=?,payload_ref=? WHERE job_id=?',
                       (status, time.time(), None if status == 'completed' else job['payload_ref'], job_id))
        if status == 'completed' and job['payload_ref']:
            from .persistence import durable_unlink
            durable_unlink(self._payloads().root / (job['payload_ref'] + '.enc'))

    def recover_interrupted(self):
        require_owner()
        from . import recovery
        for job in self.pending():
            if job['request_id'] and job['kind'] in {'ask', 'plan', 'file'}:
                record = requests.current().get(job['request_id'])
                if record and record.status == 'completed':
                    self.finish(job['job_id'], 'completed')
                    continue
            if job['status'] != 'running':
                continue
            status = 'needs_review'
            if job['request_id'] and job['kind'] in {'ask', 'plan', 'file'}:
                record = requests.current().get(job['request_id'])
                if record is None or record.status == 'prepared' or recovery.available(record):
                    status = 'queued'
                elif record.status == 'completed':
                    status = 'completed'
            self.finish(job['job_id'], status)
        self.prune_payloads()

    def prune_payloads(self):
        from .persistence import durable_unlink
        # Serialize with enqueue's encrypted publication, including its SQL commit.
        with self.health.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            refs = {r[0] for r in db.execute('SELECT payload_ref FROM jobs WHERE payload_ref IS NOT NULL')}
            root = self.health.path.parent / 'worker-payloads'
            if root.is_symlink():
                raise StorageError('Unsafe worker payload path.')
            for path in root.glob('job-*.enc'):
                if path.stem not in refs:
                    durable_unlink(path)
            db.commit()

    def purge_revoked(self):
        """Revocation removes content, never the tombstone that forbids replay."""
        if not operations.PATH.exists():
            return
        with operations.current().connection() as ledger:
            revoked = {r[0] for r in ledger.execute('SELECT request_id FROM requests WHERE payload_ref IS NULL')}
        with self.health.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for request_id in revoked:
                db.execute("UPDATE jobs SET payload_ref=NULL,status=CASE WHEN status='completed' THEN status ELSE 'needs_review' END "
                           "WHERE request_id=?", (request_id,))
            db.commit()
        self.prune_payloads()


def dispatch(kind, args):
    from . import cli
    return getattr(cli, 'cmd_' + kind)(args)


def execute(job, fn=None):
    require_owner()
    queue = Queue()
    if not queue.claim(job['job_id']):
        return None
    try:
        try:
            value = queue.payload(job)
            args = Namespace(**value['args'])
            args._envelope = (requests.RequestEnvelope.decode(json.dumps(value['envelope']).encode())
                              if value['envelope'] else None)
        except (StorageError, FileNotFoundError, ValueError, TypeError, KeyError) as exc:
            raise QueuedPayloadError('Queued payload requires review.') from exc
        if args._envelope:
            args._envelope = requests.current().capture(args._envelope)
        result = fn(args) if fn else dispatch(job['kind'], args)
    except BaseException:
        queue.finish(job['job_id'], 'needs_review')
        raise
    status = 'completed'
    if job['request_id'] and not getattr(args, 'dry_run', False):
        record = requests.current().get(job['request_id'])
        if record and record.status != 'completed':
            status = 'needs_review'
    queue.finish(job['job_id'], status)
    if status == 'completed':
        queue.health.success()
    return result


def drain_one():
    require_owner()
    queue = Queue()
    if queue.health.paused:
        return False
    queue.recover_interrupted()
    for job in queue.pending():
        if job['status'] == 'queued':
            try:
                execute(job)
            except QueuedPayloadError:
                queue.health.update(reason_code='queue_review')
            return True
    return False


def submit(kind, args, fn):
    """Enqueue first; a second producer never opens a second executor."""
    if WorkerLock.owned():
        return fn(Namespace(**args))
    if credentials._provider is None:
        credentials.startup()
    job = Queue().enqueue(kind, args)
    with WorkerLock() as lock:
        if not lock.acquired or HealthStore().paused:
            print(json.dumps({'status': 'queued', 'job_id': job['job_id']}))
            return {'status': 'queued', 'job_id': job['job_id']}
        if job['status'] != 'queued':
            print(json.dumps({'status': job['status'], 'job_id': job['job_id']}))
            return {'status': job['status'], 'job_id': job['job_id']}
        with Heartbeat(HealthStore()) as heartbeat:
            try:
                from .worker_migration import migrate_filer
                migrate_filer()
                brain = probe()
                Queue().recover_interrupted()
                from .watch import Watcher
                recovery = Watcher(brain)
                for _ in range(200):
                    if HealthStore().paused:
                        return {'status': 'queued', 'job_id': job['job_id']}
                    if not recovery.recover_pending():
                        break
                HealthStore().update(state='ready', reason_code=None)
                result = execute(job, fn)
                # A one-shot process also serves work accepted while it was busy.
                # Exit and enqueue share a short admission mutex, closing the gap
                # between the final empty-queue check and releasing the worker.
                queue = Queue()
                while not queue.health.paused:
                    with redirect_stdout(io.StringIO()):
                        if drain_one():
                            continue
                    with queue.admission():
                        if any(j['status'] == 'queued' for j in queue.pending()):
                            continue
                        heartbeat.close()
                        lock.release()
                        return result
                return result
            except Exception as exc:
                failure(exc)
                raise


def command(kind):
    def decorate(fn):
        @wraps(fn)
        def run(args):
            if WorkerLock.owned():
                return fn(args)
            return submit(kind, vars(args), fn)
        return run
    return decorate


def maintenance(fn):
    """Setup precedes a ready policy, so refuse concurrent maintenance explicitly."""
    @wraps(fn)
    def run(*args, **kwargs):
        with WorkerLock() as lock:
            if not lock.acquired:
                raise RuntimeError('A worker is active; stop it before changing setup or storage.')
            return fn(*args, **kwargs)
    return run
