from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from test_store import db
from test_billing import billing, pay

@pytest.fixture
def usage(billing):
    from notron_service.usage import Usage
    service,_,a,b=billing
    pay(billing)
    with service.store._connect() as c:
        c.execute("INSERT INTO worker_leases VALUES (%s,%s,1,now()+interval '1 hour')",(a.account_id,a.device_id))
    return Usage(service.store,service,b'x'*32,monthly_micro_usd=1000,units_per_micro_usd=1,max_concurrency=2),a,b

def test_concurrent_allowance_boundary(usage):
    u,a,b=usage
    def attempt(_):
        try: return u.reserve(a,str(uuid4()),'a'*64,60,lease_fence=1,rate_version='fixture')
        except Exception as e: return str(e)
    with ThreadPoolExecutor(2) as pool: rows=list(pool.map(attempt,range(2)))
    assert sum(not isinstance(r,str) for r in rows)==1
    assert 'allowance_exhausted' in rows
    assert u.billing.entitlement(a).allowance==40

def test_cache_is_encrypted_scoped_and_expired_never_reexecutes(usage):
    from notron_service.usage import UsageError
    u,a,b=usage; rid=str(uuid4())
    r=u.reserve(a,rid,'a'*64,40,lease_fence=1,rate_version='fixture')
    u.settle(a,r.id,10,{'content':'private answer'})
    replay=u.reserve(a,rid,'a'*64,40,lease_fence=1,rate_version='fixture')
    assert replay.response=={'content':'private answer'}
    with pytest.raises(UsageError,match='request_conflict'): u.reserve(a,rid,'b'*64,40,lease_fence=1,rate_version='fixture')
    with u.store._connect() as c:
        row=c.execute('SELECT ciphertext FROM usage_cache').fetchone()
        assert b'private answer' not in bytes(row['ciphertext'])
        c.execute("UPDATE usage_cache SET expires_at=now()-interval '1 second'")
    with pytest.raises(UsageError,match='outcome_uncertain'): u.reserve(a,rid,'a'*64,40,lease_fence=1,rate_version='fixture')
    with pytest.raises(Exception): u.reserve(b,rid,'a'*64,40,lease_fence=1,rate_version='fixture')
    assert u.billing.entitlement(a).allowance==90

def test_missing_lease_and_revocation_deny(usage):
    from notron_service.usage import UsageError
    u,a,b=usage
    with pytest.raises(UsageError,match='permission_required'): u.reserve(a,str(uuid4()),'a'*64,10,lease_fence=2,rate_version='fixture')
    u.store.revoke_device(a,a.device_id)
    with pytest.raises(Exception): u.reserve(a,str(uuid4()),'a'*64,10,lease_fence=1,rate_version='fixture')

def test_uncertainty_holds_balance_and_aggregate_ceiling(usage):
    from notron_service.usage import UsageError
    u,a,b=usage; u.monthly_micro_usd=50
    r=u.reserve(a,str(uuid4()),'a'*64,40,lease_fence=1,rate_version='fixture')
    u.uncertain(a,r.id)
    assert u.billing.entitlement(a).allowance==60
    with pytest.raises(UsageError,match='provider_unavailable'): u.reserve(a,str(uuid4()),'b'*64,20,lease_fence=1,rate_version='fixture')

def test_cross_account_cache_and_global_budget_are_independent_of_allowance(usage):
    from notron_service.usage import UsageError
    u,a,b=usage
    u.billing.grant_pilot(b.account_id,'fixture',100,datetime.now(timezone.utc)+timedelta(hours=1))
    with u.store._connect() as c:c.execute("INSERT INTO worker_leases VALUES (%s,%s,1,now()+interval '1 hour')",(b.account_id,b.device_id))
    rid=str(uuid4());r=u.reserve(a,rid,'a'*64,40,lease_fence=1,rate_version='fixture')
    u.settle(a,r.id,20,{'content':'account A'})
    other=u.reserve(b,rid,'a'*64,40,lease_fence=1,rate_version='fixture')
    assert other.response is None and other.id!=r.id
    u.monthly_micro_usd=65
    with pytest.raises(UsageError,match='provider_unavailable'):u.reserve(a,str(uuid4()),'b'*64,10,lease_fence=1,rate_version='fixture')

def test_completed_cache_replay_after_allowance_removed_but_not_revocation(usage):
    u,a,b=usage;rid=str(uuid4());r=u.reserve(a,rid,'a'*64,40,lease_fence=1,rate_version='fixture')
    u.settle(a,r.id,10,{'content':'paid answer'})
    with u.store._connect() as c:c.execute("UPDATE entitlements SET allowance_units=0,status='revoked'")
    assert u.reserve(a,rid,'a'*64,40,lease_fence=1,rate_version='fixture').response=={'content':'paid answer'}
    u.store.revoke_device(a,a.device_id)
    with pytest.raises(Exception):u.reserve(a,rid,'a'*64,40,lease_fence=1,rate_version='fixture')

def test_settlement_overflow_holds_full_allocation(usage):
    from notron_service.usage import UsageError
    u,a,b=usage;r=u.reserve(a,str(uuid4()),'a'*64,40,lease_fence=1,rate_version='fixture')
    with pytest.raises(UsageError,match='outcome_uncertain'):u.settle(a,r.id,41,{'content':'answer'})
    assert u.billing.entitlement(a).allowance==60
    with u.store._connect() as c:assert c.execute('SELECT count(*) AS n FROM usage_cache').fetchone()['n']==0

def test_settlement_uses_reserved_conversion_after_configuration_change(usage):
    u,a,b=usage;r=u.reserve(a,str(uuid4()),'a'*64,40,lease_fence=1,rate_version='fixture')
    u.units_per_micro_usd=2
    u.settle(a,r.id,10,{'content':'answer'})
    assert u.billing.entitlement(a).allowance==90
