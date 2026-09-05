"""Exercise real admission/queue/lifecycle with synthetic native/provider work."""
from argparse import Namespace
import json
import threading
import time

import pytest

from notron import requests, watch


def test_second_producer_enqueues_instead_of_executing(monkeypatch):
    from notron import worker
    from notron.health import WorkerLock
    ran = []
    monkeypatch.setattr(worker, 'probe', lambda: None)
    with WorkerLock():
        thread = threading.Thread(target=lambda: worker.submit('index', {'rebuild': False}, lambda args: ran.append('ran')))
        thread.start()
        thread.join(3)
        assert not thread.is_alive()
        assert ran == []
        assert worker.Queue().pending()[0]['status'] == 'queued'
    monkeypatch.setattr(worker, 'dispatch', lambda kind, args: ran.append(kind))
    with WorkerLock():
        assert worker.drain_one()
    assert ran == ['index']
    assert not worker.Queue().pending()


def test_pause_preserves_queue_and_resume_runs_original_capture(monkeypatch):
    from notron import worker
    from notron.health import HealthStore, WorkerLock
    store = HealthStore()
    store.set_paused(True)
    worker.submit('ask', {'request': ['tomorrow call Sam'], 'dry_run': False}, lambda args: pytest.fail('paused worker ran'))
    job = worker.Queue().pending()[0]
    original = requests.current().get(job['request_id']).envelope
    assert original.captured_at is not None
    assert worker.Queue().payload(job)['envelope'] == json.loads(original.encode())
    seen = []
    monkeypatch.setattr(worker, 'dispatch', lambda kind, args: seen.append(args._envelope.captured_at))
    with WorkerLock():
        assert not worker.drain_one()
        store.set_paused(False)
        assert worker.drain_one()
    assert seen == [original.captured_at]


def test_interrupted_maintenance_is_reviewed_without_replay(monkeypatch):
    from notron import worker
    from notron.health import WorkerLock
    queue = worker.Queue()
    job = queue.enqueue('morning', {'dry_run': False})
    with WorkerLock():
        assert queue.claim(job['job_id'])
    with WorkerLock():
        worker.Queue().recover_interrupted()
        assert worker.Queue().pending()[0]['status'] == 'needs_review'
        monkeypatch.setattr(worker, 'dispatch', lambda *a: pytest.fail('uncertain morning replayed'))
        assert not worker.drain_one()


def test_queue_cannot_claim_without_worker_ownership():
    from notron import worker
    job = worker.Queue().enqueue('index', {'rebuild': False})
    with pytest.raises(RuntimeError):
        worker.Queue().claim(job['job_id'])


def test_queue_metadata_contains_no_request_text():
    from notron import worker
    from notron.health import HealthStore
    job = worker.Queue().enqueue('ask', {'request': ['secret synthetic words'], 'dry_run': False})
    assert 'secret synthetic words' in json.dumps(worker.Queue().payload(job))
    for path in HealthStore().path.parent.glob('worker.sqlite3*'):
        assert b'secret synthetic words' not in path.read_bytes()


def test_locked_keychain_pauses_without_losing_pending(monkeypatch, _task3_storage):
    from notron import worker, credentials
    from notron.health import HealthStore, WorkerLock
    envelope = requests.current().capture(requests.create('pending question'))
    def locked():
        raise credentials.CredentialUnavailable('synthetic private error')
    monkeypatch.setattr(worker, 'probe', locked)
    w = watch.Watcher(brain=None)
    with WorkerLock():
        assert not w.prepare_runtime()
        assert HealthStore().row()['state'] == 'paused'
        assert HealthStore().row()['reason_code'] == 'keychain_locked'
    assert requests.current().get(envelope.request_id).status == 'prepared'


def test_failed_scanner_prime_is_retried_before_scanning(monkeypatch):
    from notron import worker
    from notron.health import WorkerLock
    monkeypatch.setattr(worker, 'probe', lambda: None)
    attempts = []
    w = watch.Watcher(brain=None)
    def prime():
        attempts.append('prime')
        if len(attempts) == 1:
            raise TimeoutError('native app busy')
        w.scanner.primed = True
        return 0
    monkeypatch.setattr(w.scanner, 'prime', prime)
    with WorkerLock():
        assert not w.prepare_runtime()
        assert w.prepare_runtime()
    assert attempts == ['prime', 'prime']


def test_recovery_runs_before_queued_or_fresh_jobs_after_resume(monkeypatch):
    from notron import worker
    from notron.health import HealthStore, WorkerLock
    sequence = []
    monkeypatch.setattr(worker, 'probe', lambda: sequence.append('probe'))
    monkeypatch.setattr(worker, 'drain_one', lambda: sequence.append('queue') or False)
    w = watch.Watcher(brain=object(), settle=0)
    monkeypatch.setattr(w.scanner, 'prime', lambda: setattr(w.scanner, 'primed', True) or 0)
    monkeypatch.setattr(w, 'recover_pending', lambda: sequence.append('recover') or False)
    monkeypatch.setattr(w, 'check_ask', lambda: sequence.append('fresh'))
    with WorkerLock():
        w.tick(resumed=True)
    assert sequence.index('probe') < sequence.index('recover') < sequence.index('queue') < sequence.index('fresh')


def test_cooldown_survives_restart_without_storing_request_words():
    from notron.health import HealthStore
    w = watch.Watcher(brain=None)
    w._attempted('ask:sensitive request label', False)
    w._attempted('ask:sensitive request label', False)
    assert not watch.Watcher(brain=None)._worth_trying('ask:sensitive request label')
    for path in HealthStore().path.parent.glob('worker.sqlite3*'):
        assert b'sensitive request label' not in path.read_bytes()


def test_listen_status_and_pause_are_versioned_json_without_brain(monkeypatch, capsys):
    from notron import cli
    monkeypatch.setattr(watch, 'is_running', lambda: False)
    monkeypatch.setattr(cli, '_brain', lambda: pytest.fail('status opened provider credentials'))
    cli.main(['listen', '--pause'])
    assert json.loads(capsys.readouterr().out)['state'] == 'paused'
    cli.main(['listen', '--status'])
    assert json.loads(capsys.readouterr().out)['version'] == 1
    cli.main(['listen', '--resume'])
    assert json.loads(capsys.readouterr().out)['state'] == 'stopped'


def test_direct_morning_call_also_queues_behind_listener(monkeypatch):
    from notron import daily, worker
    from notron.health import WorkerLock
    out = []
    with WorkerLock():
        t = threading.Thread(target=lambda: out.append(daily.morning(None)))
        t.start()
        t.join(3)
        assert not t.is_alive()
        assert out[0]['status'] == 'queued'
        assert worker.Queue().pending()[0]['kind'] == 'morning'


def test_completed_job_discards_encrypted_args_but_rejects_changed_retry(monkeypatch):
    from notron import worker
    from notron.health import WorkerLock
    queue = worker.Queue()
    job = queue.enqueue('ask', {'request': ['first request'], 'request_id': 'retry-id', 'dry_run': True})
    with WorkerLock():
        worker.execute(job, lambda args: None)
    assert queue.get(job['job_id'])['payload_ref'] is None
    assert not list((queue.health.path.parent / 'worker-payloads').glob('job-*.enc'))
    from notron.operations import OperationConflict
    with pytest.raises(OperationConflict):
        queue.enqueue('ask', {'request': ['different request'], 'request_id': 'retry-id', 'dry_run': True})


def test_cli_repairs_prior_requests_before_new_job(monkeypatch):
    from notron import worker
    order = []
    monkeypatch.setattr(worker, 'probe', lambda: None)
    monkeypatch.setattr(watch.Watcher, 'recover_pending', lambda self: order.append('recovery') or False)
    worker.submit('index', {'rebuild': False}, lambda args: order.append('new'))
    assert order == ['recovery', 'new']


def test_second_process_cannot_claim_operation_and_crash_releases_lock():
    import os
    from notron import worker
    from notron.health import WorkerLock
    queue = worker.Queue()
    job = queue.enqueue('index', {'rebuild': False})
    read_fd, write_fd = os.pipe()
    with WorkerLock():
        child = os.fork()
        if child == 0:
            try:
                os.close(read_fd)
                with WorkerLock() as second:
                    os.write(write_fd, b'bad' if second.acquired else b'blocked')
            finally:
                os._exit(0)
        os.close(write_fd)
        assert os.read(read_fd, 16) == b'blocked'
        os.close(read_fd)
        assert os.waitpid(child, 0)[1] == 0
    child = os.fork()
    if child == 0:
        with WorkerLock():
            queue.claim(job['job_id'])
            os._exit(0)  # Simulate a crash without releasing Python contexts.
    assert os.waitpid(child, 0)[1] == 0
    with WorkerLock() as replacement:
        assert replacement.acquired
        queue.recover_interrupted()
    assert queue.get(job['job_id'])['status'] == 'needs_review'


def test_foreground_stop_intent_exits_without_fresh_jobs(monkeypatch):
    from notron import worker
    from notron.health import HealthStore
    monkeypatch.setattr(worker, 'probe', lambda: None)
    watcher = watch.Watcher(brain=None, ask_poll=0.01)
    watcher.scanner.primed = True
    calls = []
    monkeypatch.setattr(watcher, 'recover_pending', lambda: False)
    def ask():
        calls.append('ask')
        HealthStore().set_stop(True)
    monkeypatch.setattr(watcher, 'check_ask', ask)
    t = threading.Thread(target=watcher.run_forever)
    t.start()
    t.join(2)
    if t.is_alive():
        # The regression should fail with no runaway test thread left behind.
        monkeypatch.setattr(watcher, 'tick', lambda **kw: (_ for _ in ()).throw(SystemExit()))
        t.join(1)
    assert not t.is_alive()
    assert calls == ['ask']
    assert HealthStore().status()['state'] == 'stopped'


def test_damaged_queue_payload_does_not_block_later_work(monkeypatch):
    from notron import worker
    from notron.health import WorkerLock
    from notron.securestore import StorageError
    queue = worker.Queue()
    bad = queue.enqueue('index', {'rebuild': False})
    good = queue.enqueue('index', {'rebuild': True})
    (queue._payloads().root / (bad['payload_ref'] + '.enc')).write_bytes(b'damaged')
    seen = []
    monkeypatch.setattr(worker, 'dispatch', lambda kind, args: seen.append(args.rebuild))
    with WorkerLock():
        assert worker.drain_one()  # Quarantine this entry and leave the worker usable.
        assert queue.get(bad['job_id'])['status'] == 'needs_review'
        assert worker.drain_one()
    assert seen == [True]
    assert queue.get(good['job_id'])['status'] == 'completed'


def test_repaired_request_clears_stale_queue_review():
    from notron import worker
    from notron.health import WorkerLock, HealthStore
    queue = worker.Queue()
    job = queue.enqueue('ask', {'request': ['hello'], 'dry_run': False})
    with WorkerLock():
        queue.claim(job['job_id'])
        queue.finish(job['job_id'], 'needs_review')
        store = requests.current()
        store.claim(job['request_id'])
        store.finish(job['request_id'])  # The watcher repaired its verified receipt.
        assert not worker.drain_one()
        assert queue.get(job['job_id'])['status'] == 'completed'
        assert HealthStore().status()['pending_count'] == 0


def test_policy_revocation_drops_queued_payload_without_erasing_identity():
    from notron import worker, library
    from notron.health import HealthStore
    queue = worker.Queue()
    job = queue.enqueue('ask', {'request': ['private command'], 'dry_run': False})
    library.save(library.Library(decided={'n1'}))
    assert queue.get(job['job_id'])['status'] == 'needs_review'
    assert queue.get(job['job_id'])['payload_ref'] is None
    assert not list(queue._payloads().root.glob('job-*.enc'))
    assert HealthStore().status()['pending_count'] == 1


def test_one_shot_owner_drains_jobs_accepted_while_it_was_busy(monkeypatch):
    from notron import worker
    monkeypatch.setattr(worker, 'probe', lambda: None)
    observed = []
    def first(args):
        observed.append('first')
        second = threading.Thread(target=lambda: worker.submit('index', {'rebuild': True},
                                  lambda args: pytest.fail('second producer executed')))
        second.start()
        second.join(2)
        assert not second.is_alive()
    monkeypatch.setattr(worker, 'dispatch', lambda kind, args: observed.append('second'))
    worker.submit('index', {'rebuild': False}, first)
    assert observed == ['first', 'second']
    assert not worker.Queue().pending()


def test_setup_refuses_concurrent_native_mutation(monkeypatch):
    from notron import cli, workspace
    from notron.health import WorkerLock
    reached = []
    monkeypatch.setattr(workspace, 'bootstrap', lambda: reached.append('unsafe') or {})
    monkeypatch.setattr(cli, 'cmd_permissions', lambda args: None)
    errors = []
    def second():
        try:
            cli.cmd_setup(Namespace())
        except RuntimeError:
            errors.append('busy')
    with WorkerLock():
        thread = threading.Thread(target=second)
        thread.start()
        thread.join(2)
    assert reached == []
    assert errors == ['busy']


def test_orphaned_encrypted_queue_payload_is_cleaned_after_restart(monkeypatch):
    from notron import worker, persistence
    from notron.health import WorkerLock
    queue = worker.Queue()
    job = queue.enqueue('index', {'rebuild': False})
    unlink = persistence.durable_unlink
    monkeypatch.setattr(persistence, 'durable_unlink', lambda path: (_ for _ in ()).throw(OSError('crash before unlink')))
    with WorkerLock():
        with pytest.raises(OSError):
            worker.execute(job, lambda args: None)
    assert queue.get(job['job_id'])['status'] == 'completed'
    monkeypatch.setattr(persistence, 'durable_unlink', unlink)
    with WorkerLock():
        worker.Queue().recover_interrupted()
    assert not list(queue._payloads().root.glob('job-*.enc'))


def test_stop_during_mention_prevents_new_filing_job(monkeypatch):
    from notron import worker
    from notron.health import HealthStore, WorkerLock
    monkeypatch.setattr(worker, 'probe', lambda: None)
    watcher = watch.Watcher(brain=None)
    watcher.scanner.primed = True
    monkeypatch.setattr(watcher, 'recover_pending', lambda: False)
    monkeypatch.setattr(watcher, 'check_ask', lambda: None)
    monkeypatch.setattr(watcher, 'sweep_mentions', lambda: HealthStore().set_stop(True))
    monkeypatch.setattr(watcher, 'check_dump', lambda: pytest.fail('filing started after stop'))
    with WorkerLock():
        watcher.tick()


def test_queued_morning_retains_original_capture_time():
    from notron import worker
    job = worker.Queue().enqueue('morning', {'dry_run': False})
    value = worker.Queue().payload(job)
    assert value['envelope']['source'] == 'morning'
    assert value['envelope']['captured_at'] is not None
