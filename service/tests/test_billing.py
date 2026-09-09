"""Disposable PostgreSQL, real Stripe signatures; only Stripe network calls are fake."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from copy import deepcopy
import hashlib
import hmac
import json
import time
import pytest
from test_store import db, seed, store_for


class FakeStripe:
    def __init__(self):
        self.subs=[]; self.invoices=[]; self.sessions={}; self.refunds=set(); self.disputes=set()
        self.fail=False; self.customer_calls=0; self.checkout_calls=0
    def customer(self, account):
        self.customer_calls+=1
        return 'cus_'+str(account)
    def subscriptions(self, customer):
        if self.fail: raise RuntimeError('private upstream details')
        return deepcopy(self.subs)
    def paid_invoices(self, customer, subscription):
        return deepcopy(self.invoices)
    def payment_reversed(self, payment_intent):
        return payment_intent in self.refunds, payment_intent in self.disputes
    def checkout(self, customer, account, order, price, kind, url):
        self.checkout_calls+=1
        sid='cs_'+str(order)
        self.sessions.setdefault(sid,dict(id=sid,customer=customer,client_reference_id=str(account),
            mode='payment' if kind=='topup' else 'subscription',payment_status='unpaid',status='open',
            payment_intent='pi_'+str(order),url='https://checkout.stripe.com/test',created=int(time.time()),
            line_items={'data':[{'price':{'id':price},'quantity':1}]}))
        return deepcopy(self.sessions[sid])
    def session(self, sid): return deepcopy(self.sessions[sid])
    def portal(self, customer, url): return 'https://billing.stripe.com/test'


@pytest.fixture
def billing(db):
    from notron_service.billing import Billing, BillingPolicy, Price
    store=store_for(db); store.migrate()
    a,b=seed(db)
    gateway=FakeStripe()
    policy=BillingPolicy({'price_plan':Price('subscription',100), 'price_topup':Price('topup',40,3600)},
                         ('https://app.example.test/billing',), 'whsec_fixture')
    service=Billing(store,gateway,policy)
    return service,gateway,a,b


def pay(billing, *, status='active', days=1):
    service,gateway,a,b=billing
    service.checkout(a,'price_plan','https://app.example.test/billing',uuid4())
    end=int((datetime.now(timezone.utc)+timedelta(days=days)).timestamp())
    customer='cus_'+str(a.account_id)
    gateway.subs=[dict(id='sub_a',customer=customer,status=status,trial_end=None,
        items={'data':[{'price':{'id':'price_plan'},'current_period_end':end}]})]
    gateway.invoices=[dict(id='in_a',customer=customer,status='paid',amount_paid=1200,subscription='sub_a',
        payment_intents=['pi_a'],lines={'data':[dict(price={'id':'price_plan'},quantity=1,
        period={'start':end-86400,'end':end},proration=False)]})]
    service.reconcile(a.account_id)
    return end


def deliver(service, event_id='evt_a', customer=None, kind='invoice.paid'):
    body=json.dumps(dict(id=event_id,type=kind,livemode=False,data={'object':{'id':'in_a','customer':customer}})).encode()
    stamp=int(time.time()); sig=hmac.new(b'whsec_fixture',str(stamp).encode()+b'.'+body,hashlib.sha256).hexdigest()
    service.ingest(body,f't={stamp},v1={sig}')


def test_signed_event_durable_duplicate_and_authoritative_invoice_once(billing):
    service,gateway,a,b=billing
    pay(billing)
    deliver(service,customer='cus_'+str(a.account_id)); deliver(service,customer='cus_'+str(a.account_id))
    with service.store._connect() as c:
        assert c.execute('SELECT count(*) AS n FROM webhook_events').fetchone()['n']==1
        assert c.execute('SELECT processed_at FROM webhook_events').fetchone()['processed_at'] is None
    assert service.repair()['failed']==0
    deliver(service,'evt_other','cus_'+str(a.account_id)); service.repair()
    assert service.entitlement(a).allowance==100
    with service.store._connect() as c:
        assert c.execute('SELECT count(*) AS n FROM billing_grants').fetchone()['n']==1


def test_forged_signature_rejected_without_storage(billing):
    from notron_service.billing import BillingError
    service,*_=billing
    with pytest.raises(BillingError): service.ingest(b'{"id":"forged"}', 't=0,v1=no')
    with service.store._connect() as c:
        assert c.execute('SELECT count(*) AS n FROM webhook_events').fetchone()['n']==0


def test_incomplete_redirect_does_not_grant_and_lifecycle_recovers(billing):
    service,gateway,a,b=billing
    service.checkout(a,'price_plan','https://app.example.test/billing',uuid4())
    assert not service.entitlement(a).allowed
    end=pay(billing,status='incomplete')
    assert not service.entitlement(a).allowed
    gateway.subs[0]['status']='active'; service.reconcile(a.account_id)
    assert service.entitlement(a).allowed
    gateway.subs[0]['status']='past_due'; service.reconcile(a.account_id)
    assert service.entitlement(a).allowed
    assert not service.entitlement(a,now=datetime.fromtimestamp(end+1,timezone.utc)).allowed
    gateway.subs[0]['status']='active'
    gateway.invoices[0]['id']='in_renew'; gateway.invoices[0]['lines']['data'][0]['period']['end']=end+86400
    service.reconcile(a.account_id)
    assert service.entitlement(a,now=datetime.fromtimestamp(end+1,timezone.utc)).allowed


def test_old_event_retrieves_current_state_and_cancel_preserves_paid_period(billing):
    service,gateway,a,b=billing
    pay(billing); gateway.subs[0]['status']='canceled'
    deliver(service,'evt_ancient','cus_'+str(a.account_id),'customer.subscription.created')
    service.repair()
    assert service.entitlement(a).allowed
    gateway.refunds.add('pi_a'); service.reconcile(a.account_id)
    assert not service.entitlement(a).allowed


def test_checkout_allowlist_idempotency_and_account_binding(billing):
    from notron_service.billing import BillingError
    from notron_service.store import AccessDenied
    from notron_service.principals import Principal
    service,gateway,a,b=billing
    for price,url in [('price_bad','https://app.example.test/billing'),('price_plan','https://evil.test')]:
        with pytest.raises(BillingError): service.checkout(a,price,url,uuid4())
    rid=uuid4()
    first=service.checkout(a,'price_plan','https://app.example.test/billing',rid)
    assert service.checkout(a,'price_plan','https://app.example.test/billing',rid)==first
    assert gateway.customer_calls==1 and gateway.checkout_calls==1
    with pytest.raises(BillingError): service.checkout(a,'price_topup','https://app.example.test/billing',rid)
    forged=Principal(a.account_id,b.device_id,a.scopes,'managed')
    with pytest.raises(AccessDenied): service.portal(forged,'https://app.example.test/billing')
    assert service.portal(a,'https://app.example.test/billing').startswith('https://billing.stripe.com')


def test_topup_failed_paid_refunded_and_cross_account(billing):
    service,gateway,a,b=billing
    pay(billing)
    rid=uuid4(); service.checkout(a,'price_topup','https://app.example.test/billing',rid)
    service.reconcile(a.account_id)
    assert service.entitlement(a).allowance==100
    session=gateway.sessions['cs_'+str(rid)]; session.update(payment_status='paid',status='complete')
    session['customer']='cus_'+str(b.account_id)
    from notron_service.billing import BillingError
    with pytest.raises(BillingError,match='billing_ownership_mismatch'): service.reconcile(a.account_id)
    assert service.entitlement(a).allowance==100
    session['customer']='cus_'+str(a.account_id); service.reconcile(a.account_id); service.reconcile(a.account_id)
    assert service.entitlement(a).allowance==140
    gateway.refunds.add(session['payment_intent']); service.reconcile(a.account_id)
    assert service.entitlement(a).allowance==100


def test_provider_failure_is_retryable_and_scheduler_repairs_without_event(billing):
    service,gateway,a,b=billing
    pay(billing); deliver(service,customer='cus_'+str(a.account_id)); gateway.fail=True
    assert service.repair()['failed']>=1
    gateway.fail=False; assert service.repair()['failed']==0
    gateway.refunds.add('pi_a'); service.repair()
    assert service.entitlement(a).allowance==0


def test_dispute_immediately_revokes_account(billing):
    from notron_service.store import AccessDenied
    service,gateway,a,b=billing
    pay(billing); gateway.disputes.add('pi_a'); service.reconcile(a.account_id)
    with pytest.raises(AccessDenied): service.entitlement(a)


def test_pilot_explicit_capped_and_expiring(billing):
    service,gateway,a,b=billing
    end=datetime.now(timezone.utc)+timedelta(hours=1)
    service.grant_pilot(a.account_id,'approved-ticket-1',20,end)
    service.grant_pilot(a.account_id,'approved-ticket-1',20,end)
    assert service.entitlement(a).allowance==20
    assert not service.entitlement(a,now=end).allowed


def reservation(service,a,units):
    rid=uuid4()
    with service.store._connect() as c:
        service.lock_account(c,a.account_id)
        grant=c.execute('SELECT id FROM entitlements WHERE account_id=%s LIMIT 1',(a.account_id,)).fetchone()['id']
        c.execute('''INSERT INTO usage_reservations(account_id,id,device_id,request_id,entitlement_id,payload_digest,reserved_units)
          VALUES (%s,%s,%s,%s,%s,%s,%s)''',(a.account_id,rid,a.device_id,str(rid),grant,'a'*64,units))
        service.reserve_locked(c,a.account_id,rid,units,datetime.now(timezone.utc))
    return rid


def test_reserve_settle_refund_never_resurrects_spent_capacity(billing):
    service,gateway,a,b=billing
    pay(billing); rid=reservation(service,a,60)
    assert service.entitlement(a).allowance==40
    with service.store._connect() as c:
        service.lock_account(c,a.account_id); service.settle_locked(c,a.account_id,rid,30)
    assert service.entitlement(a).allowance==70
    gateway.refunds.add('pi_a'); service.reconcile(a.account_id)
    with service.store._connect() as c:
        service.lock_account(c,a.account_id); service.settle_locked(c,a.account_id,rid,30)
    assert service.entitlement(a).allowance==0


def test_concurrent_reservations_cannot_overspend(billing):
    from concurrent.futures import ThreadPoolExecutor
    from notron_service.billing import BillingError
    service,gateway,a,b=billing
    pay(billing)
    def run():
        try: reservation(service,a,60); return True
        except BillingError: return False
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sum(executor.map(lambda _:run(),range(2)))==1
    assert service.entitlement(a).allowance==40


def test_official_sdk_adapter_wire_contract_without_network():
    import stripe
    from notron_service.billing import StripeGateway
    class Transport(stripe.HTTPClient):
        name='test'
        def request(self,method,url,headers,post_data=None):
            from urllib.parse import urlsplit
            path=urlsplit(url).path
            objects={
              '/v1/customers':{'id':'cus_a','object':'customer'},
              '/v1/disputes/dp_a':{'id':'dp_a','object':'dispute','charge':'ch_a'},
              '/v1/refunds/re_a':{'id':'re_a','object':'refund','charge':'ch_a'},
              '/v1/charges/ch_a':{'id':'ch_a','object':'charge','customer':'cus_a'},
              '/v1/subscriptions':{'object':'list','data':[{'id':'sub_a','object':'subscription','customer':'cus_a'}],'has_more':False},
              '/v1/invoices':{'object':'list','data':[{'id':'in_a','object':'invoice','parent':{'subscription_details':{'subscription':'sub_a'}}}],'has_more':False},
              '/v1/invoices/in_a/lines':{'object':'list','data':[{'id':'il_a','object':'line_item','pricing':{'price_details':{'price':'price_plan'}},'parent':{'subscription_item_details':{'proration':False}},'quantity':1,'period':{'end':100}}],'has_more':False},
              '/v1/invoice_payments':{'object':'list','data':[{'id':'ip_a','object':'invoice_payment','payment':{'type':'payment_intent','payment_intent':'pi_a'}}],'has_more':False},
              '/v1/payment_intents/pi_a':{'id':'pi_a','object':'payment_intent','latest_charge':{'id':'ch_a','object':'charge','amount_refunded':0,'disputed':False}},
              '/v1/checkout/sessions':{'id':'cs_a','object':'checkout.session','url':'https://checkout.stripe.com/a'},
              '/v1/checkout/sessions/cs_a':{'id':'cs_a','object':'checkout.session'},
              '/v1/checkout/sessions/cs_a/line_items':{'object':'list','data':[],'has_more':False},
              '/v1/billing_portal/sessions':{'id':'bps_a','object':'billing_portal.session','url':'https://billing.stripe.com/a'},
            }
            assert headers['Stripe-Version']=='2026-08-26.dahlia'
            if method.lower()=='get' and path=='/v1/checkout/sessions':
                objects[path]={'object':'list','has_more':False,'data':[{'id':'cs_a','object':'checkout.session','client_reference_id':'a','metadata':{'order_id':'o'}}]}
            return json.dumps(objects[path]).encode(),200,{}
    gateway=StripeGateway('sk_test_fake')
    gateway.client=stripe.StripeClient('sk_test_fake',stripe_version='2026-08-26.dahlia',http_client=Transport())
    assert gateway.customer('a')=='cus_a'
    assert gateway.subscriptions('cus_a')[0]['id']=='sub_a'
    assert gateway.paid_invoices('cus_a','sub_a')[0]['payment_intents']==['pi_a']
    assert gateway.payment_reversed('pi_a')==(False,False)
    assert gateway.checkout('cus_a','a','o','price_plan','subscription','https://a.test')['id']=='cs_a'
    assert gateway.session('cs_a')['line_items']=={'data':[]}
    assert gateway.portal('cus_a','https://a.test')=='https://billing.stripe.com/a'
    assert gateway.event_customer('charge.dispute.created','dp_a')=='cus_a'
    assert gateway.event_customer('refund.updated','re_a')=='cus_a'
    assert gateway.find_session('cus_a','a','o')['id']=='cs_a'


def test_zero_payment_and_trial_extension_never_double_grant(billing):
    service,gateway,a,b=billing
    pay(billing)
    gateway.invoices=[]
    gateway.subs[0].update(status='trialing',trial_end=int(time.time())+3600)
    service.reconcile(a.account_id)
    gateway.subs[0]['trial_end']+=3600
    service.reconcile(a.account_id)
    with service.store._connect() as c:
        assert c.execute("SELECT count(*) AS n FROM billing_grants WHERE kind='trial'").fetchone()['n']==1


def test_unpaid_invoice_cannot_mint_allowance(billing):
    service,gateway,a,b=billing
    pay(billing)
    gateway.invoices[0].update(id='in_zero',amount_paid=0,payment_intents=[])
    service.reconcile(a.account_id)
    assert service.entitlement(a).allowance==100


def test_stale_reservation_fails_closed_until_reconciled(billing):
    from notron_service.billing import BillingError
    service,gateway,a,b=billing
    pay(billing)
    with service.store._connect() as c:
        c.execute("UPDATE billing_accounts SET reconciled_at=now()-interval '1 hour'")
    with pytest.raises(BillingError,match='billing_stale'):
        reservation(service,a,1)
    assert service.entitlement(a).allowed
    reservation(service,a,1)


def test_settlement_above_reserve_and_conflicting_retry_hold_funds(billing):
    from notron_service.billing import BillingError
    service,gateway,a,b=billing
    pay(billing); rid=reservation(service,a,60)
    with pytest.raises(BillingError,match='reservation_exceeded'):
        with service.store._connect() as c:
            service.lock_account(c,a.account_id); service.settle_locked(c,a.account_id,rid,61)
    assert service.entitlement(a).allowance==40
    with service.store._connect() as c:
        service.lock_account(c,a.account_id); service.release_locked(c,a.account_id,rid)
    assert service.entitlement(a).allowance==100
    with pytest.raises(BillingError,match='settlement_conflict'):
        with service.store._connect() as c:
            service.lock_account(c,a.account_id); service.settle_locked(c,a.account_id,rid,1)


def test_checkout_failure_leaves_durable_intent_for_safe_retry(billing):
    service,gateway,a,b=billing
    original=gateway.checkout
    def unavailable(*args): raise RuntimeError('provider unavailable')
    gateway.checkout=unavailable
    rid=uuid4()
    with pytest.raises(RuntimeError): service.checkout(a,'price_topup','https://app.example.test/billing',rid)
    with service.store._connect() as c:
        assert c.execute('SELECT count(*) AS n FROM billing_orders WHERE id=%s',(rid,)).fetchone()['n']==1
    gateway.checkout=original
    assert service.checkout(a,'price_topup','https://app.example.test/billing',rid)['url'].startswith('https://')


def test_dispute_without_customer_pointer_resolves_and_revokes(billing):
    service,gateway,a,b=billing
    pay(billing)
    gateway.event_customer=lambda kind,oid:'cus_'+str(a.account_id)
    gateway.disputes.add('pi_a')
    deliver(service,kind='charge.dispute.created')
    counts=service.repair(limit=1)
    with service.store._connect() as c:
        assert c.execute('SELECT status FROM accounts WHERE id=%s',(a.account_id,)).fetchone()['status']=='suspended'
        assert c.execute('SELECT account_id FROM webhook_events').fetchone()['account_id']==a.account_id


def test_paid_grant_starts_with_its_period_and_trial_units_end_on_conversion(billing):
    service,gateway,a,b=billing
    end=pay(billing)
    gateway.subs[0].update(status='trialing',trial_end=int(time.time())+3600)
    service.reconcile(a.account_id)
    gateway.subs[0]['status']='active'; service.reconcile(a.account_id)
    assert service.entitlement(a).allowance==100
    gateway.invoices[0].update(id='in_future')
    gateway.invoices[0]['lines']['data'][0]['period']={'start':end,'end':end+86400}
    service.reconcile(a.account_id)
    assert service.entitlement(a).allowance==100


def test_topup_expiry_and_refund_after_reservation_do_not_restore_capacity(billing):
    service,gateway,a,b=billing
    pay(billing); rid=uuid4(); service.checkout(a,'price_topup','https://app.example.test/billing',rid)
    session=gateway.sessions['cs_'+str(rid)]; session.update(payment_status='paid',status='complete')
    service.reconcile(a.account_id); reservation_id=reservation(service,a,120)
    gateway.refunds.add(session['payment_intent']); service.reconcile(a.account_id)
    with service.store._connect() as c:
        service.lock_account(c,a.account_id); service.release_locked(c,a.account_id,reservation_id)
    assert service.entitlement(a).allowance==100
    assert service.entitlement(a,now=datetime.now(timezone.utc)+timedelta(hours=2)).allowance==100


def test_repair_recovers_checkout_after_remote_success_local_failure(billing):
    service,gateway,a,b=billing
    pay(billing)
    original=gateway.checkout
    def crash(*args):
        original(*args)
        raise RuntimeError('lost response')
    gateway.checkout=crash
    rid=uuid4()
    with pytest.raises(RuntimeError): service.checkout(a,'price_topup','https://app.example.test/billing',rid)
    session=gateway.sessions['cs_'+str(rid)]; session.update(payment_status='paid',status='complete')
    gateway.find_session=lambda customer,account,order:gateway.sessions.get('cs_'+str(order))
    service.repair()
    assert service.entitlement(a).allowance==140


def test_manual_revocation_pauses_existing_entitlement(billing):
    from notron_service.store import AccessDenied
    service,gateway,a,b=billing
    pay(billing)
    service.suspend(a.account_id)
    with pytest.raises(AccessDenied): service.entitlement(a)


def test_revoked_account_webhooks_are_drained_without_restoring_access(billing):
    service,gateway,a,b=billing
    pay(billing); service.suspend(a.account_id)
    deliver(service,customer='cus_'+str(a.account_id))
    assert service.repair()['failed']==0
    with service.store._connect() as c:
        assert c.execute('SELECT processed_at FROM webhook_events').fetchone()['processed_at'] is not None
        assert c.execute('SELECT status FROM accounts WHERE id=%s',(a.account_id,)).fetchone()['status']=='suspended'


def test_policy_repr_does_not_disclose_webhook_secret(billing):
    service,*_=billing
    assert 'whsec_fixture' not in repr(service.policy)


def test_failed_account_does_not_starve_scheduled_repair(billing):
    service,gateway,a,b=billing
    service.checkout(a,'price_plan','https://app.example.test/billing',uuid4())
    service.checkout(b,'price_plan','https://app.example.test/billing',uuid4())
    calls=[]
    def subscriptions(customer):
        calls.append(customer)
        raise RuntimeError('temporary outage')
    gateway.subscriptions=subscriptions
    service.repair(limit=1); service.repair(limit=1)
    assert set(calls)=={'cus_'+str(a.account_id),'cus_'+str(b.account_id)}
