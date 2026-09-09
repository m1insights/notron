from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4
import pytest
from test_store import db, store_for, seed
from test_config import env

@pytest.fixture
def devices(db):
    from notron_service.devices import Devices
    store=store_for(db);store.migrate();a,b=seed(db)
    other=replace(a,device_id=uuid4())
    with store._connect() as c:c.execute('INSERT INTO devices(account_id,id) VALUES (%s,%s)',(a.account_id,other.device_id))
    return Devices(store),a,other,b

def test_concurrent_acquisition_one_mac(devices):
    d,a,other,b=devices
    def acquire(p):
        try:return d.acquire(p)
        except Exception:return None
    with ThreadPoolExecutor(2) as pool:result=list(pool.map(acquire,[a,other]))
    assert sum(x is not None for x in result)==1
    assert d.acquire(b)['fence']==1

def test_transfer_fences_immediately_but_waits_old_expiry(devices):
    d,a,other,b=devices
    old=d.acquire(a);new=d.transfer(other)
    assert new['fence']>old['fence'] and new['wait_seconds']>0
    with pytest.raises(Exception):d.check(a,old['fence'])
    with pytest.raises(Exception):d.check(other,new['fence'])
    with pytest.raises(Exception):d.renew(a,old['fence'])
    with d.store._connect() as c:c.execute("UPDATE worker_leases SET not_before=clock_timestamp()-interval '1 second' WHERE account_id=%s",(a.account_id,))
    assert d.check(other,new['fence'])['fence']==new['fence']
    assert d.renew(other,new['fence'])['renew_seconds']==20

def test_release_and_expiry_never_reset_fence(devices):
    d,a,other,b=devices;old=d.acquire(a)
    d.release(a,old['fence'])
    with pytest.raises(Exception):d.check(a,old['fence'])
    with pytest.raises(Exception):d.acquire(other)
    with d.store._connect() as c:c.execute("UPDATE worker_leases SET expires_at=clock_timestamp()-interval '1 second' WHERE account_id=%s",(a.account_id,))
    assert d.acquire(other)['fence']>old['fence']

def test_ownership_and_revoked_principal_rechecked(devices):
    d,a,other,b=devices;old=d.acquire(a)
    with pytest.raises(Exception):d.acquire(replace(b,device_id=a.device_id))
    with pytest.raises(Exception):d.release(b,old['fence'])
    d.store.revoke_device(other,a.device_id)
    with pytest.raises(Exception):d.renew(a,old['fence'])
    with pytest.raises(Exception):d.store.revoke_device(a,other.device_id)

def test_default_app_lease_and_delete_routes(devices,env):
    from fastapi.testclient import TestClient
    from notron_service.app import create_app,Services
    from notron_service.config import Settings
    from notron_service.auth import require_principal
    d,a,other,b=devices
    app=create_app(Settings.from_env(env),Services(store=d.store))
    app.dependency_overrides[require_principal]=lambda:a
    with TestClient(app) as client:
        response=client.post('/v1/worker/lease/acquire',json={})
        assert response.status_code==200
        fence=response.json()['fence']
        assert client.post('/v1/worker/lease/check',json={'fence':fence}).status_code==200
        assert client.post('/v1/worker/lease/renew',json={'fence':fence}).status_code==200
        assert client.post('/v1/worker/lease/release',json={'fence':fence}).status_code==200
        assert client.delete('/v1/account').status_code==200

def test_revoked_auth_waiting_for_account_lock_cannot_mutate(devices):
    import threading
    d,a,other,b=devices
    ready=threading.Event()
    with d.store._connect() as c:
        c.execute('SELECT id FROM accounts WHERE id=%s FOR UPDATE',(a.account_id,))
        def acquire():
            ready.set()
            try:d.acquire(a);return 'admitted'
            except Exception:return 'denied'
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(acquire);assert ready.wait(2)
            c.execute('UPDATE devices SET revoked_at=now() WHERE account_id=%s AND id=%s',(a.account_id,a.device_id))
            c.commit()
            assert future.result(timeout=5)=='denied'

def test_repeated_pending_transfer_keeps_original_drain_barrier(devices):
    d,a,other,b=devices;d.acquire(a);pending=d.transfer(other)
    third=replace(a,device_id=uuid4())
    with d.store._connect() as c:c.execute('INSERT INTO devices(account_id,id) VALUES (%s,%s)',(a.account_id,third.device_id))
    next_grant=d.transfer(third)
    assert next_grant['fence']>pending['fence']
    assert 0<next_grant['wait_seconds']<=pending['wait_seconds']<=60
