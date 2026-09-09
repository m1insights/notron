from test_store import db
from test_billing import billing, pay
from test_usage_concurrency import usage
import pytest
from uuid import uuid4

def test_deletion_erases_cache_and_identity_without_resurrection(usage):
    from notron_service.deletion import Deletion
    u,a,b=usage;r=u.reserve(a,str(uuid4()),'a'*64,40,lease_fence=1,rate_version='fixture')
    u.settle(a,r.id,10,{'content':'private'})
    service=Deletion(u.store);service.delete(a)
    with u.store._connect() as c:
        assert c.execute('SELECT count(*) AS n FROM usage_cache').fetchone()['n']==0
        assert c.execute('SELECT contact_email FROM accounts WHERE id=%s',(a.account_id,)).fetchone()['contact_email'] is None
        assert c.execute('SELECT count(*) AS n FROM identities WHERE account_id=%s',(a.account_id,)).fetchone()['n']==0
        assert c.execute('SELECT count(*) AS n FROM usage_meter').fetchone()['n']==1
    with pytest.raises(Exception):u.store.resolve_identity('https://issuer.test',str(a.account_id),'new','new@test',a.scopes)
    with pytest.raises(Exception):u.store.get_account(a)
    assert u.store.get_account(b)['status']=='active'
    # Late completion cannot restore erased content.
    try:u.settle(a,r.id,10,{'content':'late'})
    except Exception:pass
    with u.store._connect() as c:assert c.execute('SELECT count(*) AS n FROM usage_cache').fetchone()['n']==0

def test_retention_cleanup_keeps_only_nonresurrection_tombstone(usage):
    from notron_service.deletion import Deletion
    u,a,b=usage;service=Deletion(u.store);service.delete(a)
    with u.store._connect() as c:
        c.execute("UPDATE account_deletions SET financial_delete_after=now()-interval '1 second',security_delete_after=now()-interval '1 second'")
    service.confirm_identity_erasure(a.account_id,'synthetic provider erasure evidence')
    with u.store._connect() as c:c.execute('UPDATE account_deletions SET billing_cleanup_pending=false')
    assert service.purge_expired()['accounts']==1
    with u.store._connect() as c:assert c.execute('SELECT count(*) AS n FROM accounts WHERE id=%s',(a.account_id,)).fetchone()['n']==0
    with pytest.raises(Exception):u.store.resolve_identity('https://issuer.test',str(a.account_id),'new','new@test',a.scopes)

@pytest.mark.parametrize('settled',[True,False])
def test_short_retention_never_resets_current_budget_or_unknown_costs(usage,settled):
    from notron_service.deletion import Deletion
    u,a,b=usage;r=u.reserve(a,str(uuid4()),'a'*64,40,lease_fence=1,rate_version='fixture')
    if settled:u.settle(a,r.id,10,{'content':'private'})
    else:u.uncertain(a,r.id)
    service=Deletion(u.store,financial_retention_days=1);service.delete(a)
    with u.store._connect() as c:
        c.execute("UPDATE account_deletions SET financial_delete_after=now()-interval '1 second',security_delete_after=now()-interval '1 second'")
    service.confirm_identity_erasure(a.account_id,'synthetic provider erasure evidence')
    with u.store._connect() as c:c.execute('UPDATE account_deletions SET billing_cleanup_pending=false')
    assert service.purge_expired()['accounts']==0
    with u.store._connect() as c:
        assert c.execute('SELECT count(*) AS n FROM usage_meter').fetchone()['n']==1
        c.execute("UPDATE usage_meter SET month='2000-01-01'")
    assert service.purge_expired()['accounts']==int(settled)

def test_legacy_deleting_account_is_completed_by_cleanup(db):
    from test_store import store_for,seed
    from notron_service.deletion import Deletion
    store=store_for(db);store.migrate();a,b=seed(db)
    with store._connect() as c:c.execute("UPDATE accounts SET status='deleting' WHERE id=%s",(a.account_id,))
    Deletion(store).purge_expired()
    with store._connect() as c:
        assert c.execute('SELECT count(*) AS n FROM identities WHERE account_id=%s',(a.account_id,)).fetchone()['n']==0
        assert c.execute('SELECT count(*) AS n FROM devices WHERE account_id=%s',(a.account_id,)).fetchone()['n']==0
    with pytest.raises(Exception):store.resolve_identity('https://issuer.test',str(a.account_id),'new','new@test',a.scopes)

class CleanupStripe:
    def __init__(self,account):
        self.customer='cus_'+str(account);self.fail=False;self.cancelled=[];self.expired=[]
        self.subs=[{'id':'sub_cleanup','customer':self.customer,'status':'active'}]
        self.sessions=[{'id':'cs_cleanup','customer':self.customer,'client_reference_id':str(account),'status':'open','mode':'subscription'}]
    def checkout_sessions(self,customer):
        if self.fail:raise RuntimeError('synthetic outage')
        return [dict(s) for s in self.sessions]
    def expire_checkout(self,sid,key):
        self.expired.append((sid,key));self.sessions[0]['status']='expired'
        return dict(self.sessions[0])
    def subscriptions(self,customer):return [dict(s) for s in self.subs]
    def cancel_subscription(self,sid,key):
        self.cancelled.append((sid,key));self.subs[0]['status']='canceled'
        return dict(self.subs[0])

def test_deletion_cancellation_is_durable_retryable_and_owned(usage):
    from notron_service.deletion import Deletion
    u,a,b=usage;d=Deletion(u.store,financial_retention_days=30);g=CleanupStripe(a.account_id)
    response=d.delete(a)
    assert response['remote_cleanup_pending']
    g.fail=True
    assert d.repair_remote(g)['failed']==1
    with u.store._connect() as c:assert c.execute('SELECT billing_cleanup_pending FROM account_deletions').fetchone()['billing_cleanup_pending']
    g.fail=False
    assert d.repair_remote(g)['completed']==1
    assert d.repair_remote(g)['completed']==0
    assert len(g.cancelled)==len(g.expired)==1
    assert u.store.get_account(b)['status']=='active'
    with u.store._connect() as c:assert c.execute('SELECT identity_cleanup_pending FROM account_deletions').fetchone()['identity_cleanup_pending']

def test_remote_cleanup_rejects_foreign_customer(usage):
    from notron_service.deletion import Deletion
    u,a,b=usage;d=Deletion(u.store);d.delete(a);g=CleanupStripe(b.account_id)
    assert d.repair_remote(g)['failed']==1
    assert not g.cancelled and not g.expired

def test_stripe_cleanup_sdk_uses_no_invoice_or_proration():
    import json,stripe
    from urllib.parse import urlsplit,parse_qs
    from notron_service.billing import StripeGateway
    calls=[]
    class Transport(stripe.HTTPClient):
        name='synthetic'
        def request(self,method,url,headers,post_data=None):
            path=urlsplit(url).path;calls.append((method.lower(),path,headers,post_data or urlsplit(url).query))
            if path=='/v1/checkout/sessions':value={'object':'list','data':[],'has_more':False}
            elif path.endswith('/expire'):value={'id':'cs_test','object':'checkout.session','status':'expired'}
            else:value={'id':'sub_test','object':'subscription','status':'canceled'}
            return json.dumps(value).encode(),200,{}
    g=StripeGateway('sk_test_synthetic')
    g.client=stripe.StripeClient('sk_test_synthetic',stripe_version='2026-08-26.dahlia',http_client=Transport())
    assert g.checkout_sessions('cus_test')==[]
    assert g.expire_checkout('cs_test','expire-id')['status']=='expired'
    assert g.cancel_subscription('sub_test','cancel-id')['status']=='canceled'
    method,path,headers,body=calls[-1]
    assert method=='delete' and path=='/v1/subscriptions/sub_test'
    assert parse_qs(body)=={'invoice_now':['false'],'prorate':['false']}
    assert headers['Idempotency-Key']=='cancel-id'


def test_remote_cancel_response_loss_is_reconciled_without_duplicate(usage):
    from notron_service.deletion import Deletion
    u,a,b=usage;d=Deletion(u.store);d.delete(a);g=CleanupStripe(a.account_id)
    cancel=g.cancel_subscription
    def lost(sid,key):cancel(sid,key);raise ConnectionError('synthetic response loss')
    g.cancel_subscription=lost
    assert d.repair_remote(g)['failed']==1
    assert d.repair_remote(g)['completed']==1
    assert len(g.cancelled)==1

def test_concurrent_remote_repair_serializes_one_cancellation(usage):
    from concurrent.futures import ThreadPoolExecutor
    from notron_service.deletion import Deletion
    u,a,b=usage;d=Deletion(u.store);d.delete(a);g=CleanupStripe(a.account_id)
    with ThreadPoolExecutor(2) as pool:counts=list(pool.map(lambda _:d.repair_remote(g),range(2)))
    assert sum(c['completed'] for c in counts)==1
    assert len(g.cancelled)==len(g.expired)==1

def test_late_owned_webhook_reopens_cancellation_through_default_billing_repair(usage):
    from notron_service.deletion import Deletion
    from test_billing import deliver
    u,a,b=usage;d=Deletion(u.store);d.delete(a);g=CleanupStripe(a.account_id)
    assert d.repair_remote(g)['completed']==1
    g.subs[0]['status']='active'
    u.billing.gateway=g
    deliver(u.billing,customer=g.customer)
    assert u.billing.repair()['deletion_completed']==1
    assert len(g.cancelled)==2

def test_unresolved_checkout_intent_keeps_cleanup_pending_but_cancels_known_subscriptions(usage):
    from notron_service.deletion import Deletion
    u,a,b=usage
    with u.store._connect() as c:c.execute('UPDATE billing_orders SET session_id=NULL WHERE account_id=%s',(a.account_id,))
    d=Deletion(u.store);d.delete(a);g=CleanupStripe(a.account_id)
    assert d.repair_remote(g)['failed']==1
    assert len(g.cancelled)==1
    with u.store._connect() as c:assert c.execute('SELECT billing_cleanup_pending FROM account_deletions').fetchone()['billing_cleanup_pending']
