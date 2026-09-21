"""Health must describe a live worker, without opening protected payloads."""
import json
import threading
import time

import pytest

from notron import operations


def test_registered_job_with_expired_heartbeat_is_not_ready():
    from notron.health import classify
    assert classify(registered=True, heartbeat_age=120, paused=False,
                    permission_ok=True, network_ok=True) == 'error'


@pytest.mark.parametrize('registered,age,paused,permission,network,want', [
    (False, None, False, True, True, 'stopped'),
    (True, None, False, True, True, 'starting'),
    (True, 5, False, True, True, 'ready'),
    (True, 31, True, True, True, 'error'),
    (True, 5, True, True, True, 'paused'),
    (True, 5, False, False, True, 'permission_needed'),
    (True, 5, False, True, False, 'offline'),
])
def test_classification(registered, age, paused, permission, network, want):
    from notron.health import classify
    assert classify(registered=registered, heartbeat_age=age, paused=paused,
                    permission_ok=permission, network_ok=network) == want


def test_pause_intent_survives_restart_and_needs_no_keychain(_task3_storage):
    from notron.health import HealthStore
    store = HealthStore()
    store.set_paused(True)
    _task3_storage.values.clear()
    assert HealthStore().paused
    assert HealthStore().status(registered=False)['state'] == 'paused'
    HealthStore().set_paused(False)
    assert not HealthStore().paused


def test_live_heartbeat_does_not_report_success_until_work_completes():
    from notron.health import HealthStore, Heartbeat, WorkerLock
    store = HealthStore()
    with WorkerLock() as lock:
        assert lock.acquired
        with Heartbeat(store, interval=0.02):
            store.update(state='ready')
            before = store.status()['heartbeat_at']
            # Poll to a deadline instead of assuming that an 0.08s sleep bought
            # four beats. It did locally and on the 3.12 runner, and failed on
            # the 3.11 one: the beat thread only takes the GIL between this
            # thread's SQLite opens, so a loaded machine can swallow several
            # 20ms intervals. The property under test is that the beat keeps
            # running *independently of serial work* — not that it wins a race
            # against a fixed sleep.
            deadline = time.monotonic() + 5
            while store.status()['heartbeat_at'] == before and time.monotonic() < deadline:
                time.sleep(0.01)
            after = store.status()
            assert after['heartbeat_at'] != before, 'beat thread never ran during serial work'
            assert after['state'] == 'ready'
            assert after['last_success_at'] is None
            store.success()
            assert store.status()['last_success_at'] is not None
    assert store.status()['state'] == 'stopped'


def test_status_counts_pending_without_decrypting_or_exposing_text(_task3_storage):
    from notron import requests
    from notron.health import HealthStore
    store = HealthStore()
    requests.current().capture(requests.create('private request body'))
    _task3_storage.values.clear()
    result = store.status()
    assert result['pending_count'] == 1
    assert result['version'] == 1
    assert 'private request body' not in json.dumps(result)
    assert set(result) >= {'state', 'heartbeat_at', 'last_success_at', 'pending_count', 'reason_code'}


def test_stale_or_dead_owner_never_looks_ready():
    from notron.health import HealthStore, WorkerLock
    store = HealthStore()
    with WorkerLock():
        store.update(state='ready', heartbeat_at=time.time() - 120)
        assert store.status(registered=True)['state'] == 'error'
    store.update(state='ready', heartbeat_at=time.time())
    assert store.status(registered=True)['state'] == 'error'


def test_worker_lock_is_reentrant_only_in_owning_thread():
    from notron.health import WorkerLock
    results = []
    with WorkerLock() as first:
        assert first.acquired
        with WorkerLock() as nested:
            assert nested.acquired
        thread = threading.Thread(target=lambda: results.append(WorkerLock().try_acquire()))
        thread.start()
        thread.join(2)
    assert results == [False]
    with WorkerLock() as restarted:
        assert restarted.acquired


def test_invalid_health_schema_fails_closed():
    from notron.health import HealthStore
    store = HealthStore()
    with store.connection() as db:
        db.execute('PRAGMA user_version=999')
    from notron.securestore import StorageError
    with pytest.raises(StorageError):
        HealthStore()


def test_status_reports_corrupt_schema_as_versioned_error(monkeypatch, capsys):
    from notron import cli, watch
    from notron.health import HealthStore
    with HealthStore().connection() as db:
        db.execute('PRAGMA user_version=999')
    monkeypatch.setattr(watch, 'is_running', lambda: False)
    cli.main(['listen', '--status'])
    out = json.loads(capsys.readouterr().out)
    assert out['version'] == 1
    assert out['state'] == 'error'
    assert out['reason_code'] == 'storage_unavailable'
    assert out['pending_count'] is None
