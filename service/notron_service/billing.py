"""Stripe billing, durable inbox, and integer grant accounting.

Lock order: accounts FOR UPDATE, then billing/entitlement/usage rows. Authoritative
network reads occur inside that account transaction to prevent older reads winning.
Webhook storage contains identifiers only, never the raw content-bearing payload.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import stripe
from .entitlements import Entitlement, evaluate
from .store import AccessDenied

UTC=timezone.utc

def timestamp(value):
    return datetime.fromtimestamp(value,UTC) if value else None

def identifier(value):
    return value.get('id') if isinstance(value,dict) else value


class BillingError(ValueError):
    """Safe, fixed billing failure code."""


@dataclass(frozen=True)
class Price:
    kind: str
    units: int
    ttl_seconds: int | None = None

    def __post_init__(self):
        if self.kind not in {'subscription','topup'} or type(self.units) is not int or not 0<self.units<=10**12:
            raise BillingError('invalid_billing_policy')
        if self.kind=='topup' and (type(self.ttl_seconds) is not int or not 0<self.ttl_seconds<=366*86400):
            raise BillingError('invalid_billing_policy')


@dataclass(frozen=True)
class BillingPolicy:
    prices: dict[str,Price]
    return_urls: tuple[str,...]
    webhook_secret: str = field(repr=False)
    stale_seconds: int = 300

    def __post_init__(self):
        from urllib.parse import urlsplit
        if not self.prices or not self.return_urls or not self.webhook_secret.startswith('whsec_'):
            raise BillingError('invalid_billing_policy')
        if any(not key.startswith('price_') or not isinstance(value,Price) for key,value in self.prices.items()):
            raise BillingError('invalid_billing_policy')
        for url in self.return_urls:
            p=urlsplit(url)
            if p.scheme!='https' or not p.hostname or p.username or p.password or p.fragment or '\\' in url or any(ord(c)<33 for c in url):
                raise BillingError('invalid_billing_policy')
        if type(self.stale_seconds) is not int or not 1<=self.stale_seconds<=3600:
            raise BillingError('invalid_billing_policy')


class StripeGateway:
    """Official SDK 15.6.1 / 2026-08-26.dahlia. Construction makes no requests."""
    def __init__(self, secret):
        self.client=stripe.StripeClient(secret,stripe_version='2026-08-26.dahlia',
            max_network_retries=2,http_client=stripe.RequestsClient(timeout=10))

    def customer(self, account):
        return self.client.v1.customers.create({'metadata':{'account_id':str(account)}},
            options={'idempotency_key':'notron-customer-'+str(account)}).id

    def subscriptions(self, customer):
        return [s.to_dict() for s in self.client.v1.subscriptions.list(
            {'customer':customer,'status':'all','limit':100}).auto_paging_iter()]

    def checkout_sessions(self,customer):
        return [s.to_dict() for s in self.client.v1.checkout.sessions.list(
            {'customer':customer,'limit':100}).auto_paging_iter()]

    def expire_checkout(self,session_id,idempotency_key):
        return self.client.v1.checkout.sessions.expire(session_id,{},options={'idempotency_key':idempotency_key}).to_dict()

    def cancel_subscription(self,subscription_id,idempotency_key):
        return self.client.v1.subscriptions.cancel(subscription_id,{'invoice_now':False,'prorate':False},
            options={'idempotency_key':idempotency_key}).to_dict()

    def paid_invoices(self, customer, subscription):
        result=[]
        for inv in self.client.v1.invoices.list({'customer':customer,'subscription':subscription,
                                               'status':'paid','limit':100}).auto_paging_iter():
            value=inv.to_dict()
            value['subscription']=identifier((value.get('parent') or {}).get('subscription_details',{}).get('subscription'))
            lines=[]
            for line in self.client.v1.invoices.line_items.list(inv.id,{'limit':100}).auto_paging_iter():
                line=line.to_dict()
                parent=(line.get('parent') or {}).get('subscription_item_details') or {}
                lines.append(dict(price={'id':identifier((line.get('pricing') or {}).get('price_details',{}).get('price'))},
                    quantity=line.get('quantity'),period=line['period'],proration=parent.get('proration',True)))
            value['lines']={'data':lines}
            intents=[]
            for payment in self.client.v1.invoice_payments.list({'invoice':inv.id,'status':'paid','limit':100}).auto_paging_iter():
                p=payment.to_dict()['payment']
                if p['type']!='payment_intent':
                    # Out-of-band/payment-record support requires a reviewed grant policy.
                    raise BillingError('unsupported_invoice_payment')
                intents.append(identifier(p['payment_intent']))
            value['payment_intents']=intents
            result.append(value)
        return result

    def payment_reversed(self, payment_intent):
        pi=self.client.v1.payment_intents.retrieve(payment_intent,{'expand':['latest_charge']}).to_dict()
        charge=pi.get('latest_charge')
        if not charge or isinstance(charge,str):
            raise BillingError('payment_unverified')
        return charge.get('amount_refunded',0)>0, bool(charge.get('disputed'))

    def checkout(self, customer, account, order, price, kind, url):
        return self.client.v1.checkout.sessions.create(dict(customer=customer,
            client_reference_id=str(account),metadata={'order_id':str(order)},mode='payment' if kind=='topup' else 'subscription',
            line_items=[{'price':price,'quantity':1}],success_url=url,cancel_url=url),
            options={'idempotency_key':f'notron-checkout-{account}-{order}'}).to_dict()

    def session(self, sid):
        value=self.client.v1.checkout.sessions.retrieve(sid).to_dict()
        value['line_items']={'data':[v.to_dict() for v in
            self.client.v1.checkout.sessions.line_items.list(sid,{'limit':100}).auto_paging_iter()]}
        return value

    def event_customer(self, kind, object_id):
        if kind.startswith('charge.dispute.'):
            value=self.client.v1.disputes.retrieve(object_id).to_dict()
            return identifier(self.client.v1.charges.retrieve(identifier(value['charge'])).to_dict().get('customer'))
        if kind.startswith('refund.'):
            value=self.client.v1.refunds.retrieve(object_id).to_dict()
            if value.get('charge'):
                return identifier(self.client.v1.charges.retrieve(identifier(value['charge'])).to_dict().get('customer'))
        return None

    def find_session(self, customer, account, order):
        for session in self.client.v1.checkout.sessions.list({'customer':customer,'limit':100}).auto_paging_iter():
            value=session.to_dict()
            if value.get('metadata',{}).get('order_id')==str(order) and value.get('client_reference_id')==str(account):
                return value
        return None

    def portal(self, customer, url):
        return self.client.v1.billing_portal.sessions.create({'customer':customer,'return_url':url}).url


class Billing:
    def __init__(self, store, gateway, policy):
        self.store,self.gateway,self.policy=store,gateway,policy

    @staticmethod
    def lock_account(conn, account_id):
        row=conn.execute('SELECT status FROM accounts WHERE id=%s FOR UPDATE',(account_id,)).fetchone()
        if not row or row['status']!='active':
            raise AccessDenied('managed account access required')
        return row

    def _customer_locked(self, conn, account_id):
        conn.execute('INSERT INTO billing_accounts(account_id) VALUES (%s) ON CONFLICT DO NOTHING',(account_id,))
        row=conn.execute('SELECT customer_id FROM billing_accounts WHERE account_id=%s',(account_id,)).fetchone()
        if row['customer_id']:
            return row['customer_id']
        customer=self.gateway.customer(account_id)
        conn.execute('UPDATE billing_accounts SET customer_id=%s WHERE account_id=%s',(customer,account_id))
        return customer

    def checkout(self, principal, price_id, return_url, request_id):
        price=self.policy.prices.get(price_id)
        if price is None or return_url not in self.policy.return_urls:
            raise BillingError('billing_option_not_allowed')
        request_id=UUID(str(request_id))
        with self.store._connect() as conn:
            self.lock_account(conn,principal.account_id)
            self.store._authorize(conn,principal)
            order=conn.execute('SELECT * FROM billing_orders WHERE account_id=%s AND id=%s',
                               (principal.account_id,request_id)).fetchone()
            if order:
                if (order['price_id'],order['return_url'])!=(price_id,return_url):
                    raise BillingError('idempotency_conflict')
                if order['checkout_url']:
                    return {'url':order['checkout_url']}
            customer=self._customer_locked(conn,principal.account_id)
            if not order:
                conn.execute('''INSERT INTO billing_orders(account_id,id,price_id,kind,units,ttl_seconds,return_url)
                 VALUES (%s,%s,%s,%s,%s,%s,%s)''',(principal.account_id,request_id,price_id,price.kind,price.units,price.ttl_seconds,return_url))
        # Commit intent before Checkout can exist remotely. Retries use these exact inputs.
        with self.store._connect() as conn:
            self.lock_account(conn,principal.account_id); self.store._authorize(conn,principal)
            order=conn.execute('SELECT * FROM billing_orders WHERE account_id=%s AND id=%s',
                               (principal.account_id,request_id)).fetchone()
            if order['checkout_url']: return {'url':order['checkout_url']}
            customer=conn.execute('SELECT customer_id FROM billing_accounts WHERE account_id=%s',
                                  (principal.account_id,)).fetchone()['customer_id']
            if order['created_at']<datetime.now(UTC)-timedelta(hours=23):
                raise BillingError('checkout_repair_required')
            session=self.gateway.checkout(customer,principal.account_id,request_id,order['price_id'],order['kind'],order['return_url'])
            if session.get('customer')!=customer or session.get('client_reference_id')!=str(principal.account_id):
                raise BillingError('billing_ownership_mismatch')
            conn.execute('UPDATE billing_orders SET session_id=%s,checkout_url=%s WHERE account_id=%s AND id=%s',
                         (session['id'],session['url'],principal.account_id,request_id))
            return {'url':session['url']}

    def portal(self, principal, return_url):
        if return_url not in self.policy.return_urls:
            raise BillingError('billing_option_not_allowed')
        with self.store._connect() as conn:
            self.lock_account(conn,principal.account_id); self.store._authorize(conn,principal)
            row=conn.execute('SELECT customer_id FROM billing_accounts WHERE account_id=%s',(principal.account_id,)).fetchone()
            if not row or not row['customer_id']:
                raise BillingError('billing_customer_required')
            return self.gateway.portal(row['customer_id'],return_url)

    def ingest(self, body, signature):
        try:
            event=stripe.Webhook.construct_event(body,signature,self.policy.webhook_secret).to_dict()
            if event.get('livemode') is not False:
                raise ValueError()
            event_id=event['id']; event_type=event['type']; obj=event['data']['object']
            object_id=obj['id']; customer=identifier(obj.get('customer'))
            if any(not isinstance(v,str) or not 0<len(v)<=255 for v in (event_id,event_type,object_id)):
                raise ValueError()
            if customer is not None and (not isinstance(customer,str) or len(customer)>255):
                raise ValueError()
        except (ValueError,KeyError,TypeError,stripe.SignatureVerificationError):
            raise BillingError('invalid_webhook') from None
        with self.store._connect() as conn:
            conn.execute('''INSERT INTO webhook_events(provider,event_id,event_type,object_id,customer_id)
                VALUES ('stripe',%s,%s,%s,%s) ON CONFLICT DO NOTHING''',(event_id,event_type,object_id,customer))

    def _grant_locked(self, conn, account, source, kind, units, until, subscription=None, starts_at=None):
        old=conn.execute('SELECT account_id,entitlement_id FROM billing_grants WHERE source_id=%s',(source,)).fetchone()
        if old:
            if old['account_id']!=account:
                raise BillingError('billing_ownership_mismatch')
            return old['entitlement_id']
        eid=uuid4()
        conn.execute("INSERT INTO entitlements(account_id,id,status,access_until,allowance_units) VALUES (%s,%s,'active',%s,%s)",
                     (account,eid,until,units))
        conn.execute('''INSERT INTO billing_grants(account_id,entitlement_id,source_id,kind,subscription_id,starts_at)
                        VALUES (%s,%s,%s,%s,%s,%s)''',(account,eid,source,kind,subscription,starts_at or datetime.now(UTC)))
        return eid

    @staticmethod
    def _revoke_locked(conn, account, source):
        row=conn.execute('''UPDATE billing_grants SET revoked_at=COALESCE(revoked_at,now())
              WHERE account_id=%s AND source_id=%s RETURNING entitlement_id''',(account,source)).fetchone()
        if row:
            conn.execute("UPDATE entitlements SET status='revoked' WHERE account_id=%s AND id=%s",(account,row['entitlement_id']))

    def suspend(self, account_id):
        """Administrative revocation. Recovery requires separate owner review."""
        with self.store._connect() as conn:
            self.lock_account(conn,account_id)
            conn.execute("UPDATE accounts SET status='suspended' WHERE id=%s",(account_id,))
            conn.execute("INSERT INTO audit_metadata(account_id,id,event_code) VALUES (%s,%s,'billing_revoked')",(account_id,uuid4()))

    def grant_pilot(self, account_id, approval_id, units, access_until):
        """Administrative Python API only. No app endpoint grants pilot access."""
        if not isinstance(approval_id,str) or not 1<=len(approval_id)<=128 or type(units) is not int or not 0<units<=10**12:
            raise BillingError('invalid_pilot_grant')
        now=datetime.now(UTC)
        if not access_until.tzinfo or not now<access_until<=now+timedelta(days=366):
            raise BillingError('invalid_pilot_grant')
        with self.store._connect() as conn:
            self.lock_account(conn,account_id)
            return self._grant_locked(conn,account_id,'pilot:'+approval_id,'pilot',units,access_until)

    def _reconcile_locked(self, conn, account):
        row=conn.execute('SELECT customer_id FROM billing_accounts WHERE account_id=%s',(account,)).fetchone()
        if not row or not row['customer_id']:
            return
        customer=row['customer_id']
        for pending in conn.execute('SELECT id FROM billing_orders WHERE account_id=%s AND session_id IS NULL',(account,)).fetchall():
            session=self.gateway.find_session(customer,account,pending['id'])
            if session:
                if identifier(session.get('customer'))!=customer:
                    raise BillingError('billing_ownership_mismatch')
                conn.execute('UPDATE billing_orders SET session_id=%s,checkout_url=%s WHERE account_id=%s AND id=%s',
                    (session['id'],session.get('url'),account,pending['id']))
        subscriptions=self.gateway.subscriptions(customer)
        seen=[]; disputed=False
        for sub in subscriptions:
            if identifier(sub.get('customer'))!=customer:
                raise BillingError('billing_ownership_mismatch')
            sid=sub['id']; seen.append(sid)
            owner=conn.execute('SELECT account_id FROM billing_subscriptions WHERE subscription_id=%s',(sid,)).fetchone()
            if owner and owner['account_id']!=account:
                raise BillingError('billing_ownership_mismatch')
            items=sub.get('items',{}).get('data',[])
            price=self.policy.prices.get(identifier(items[0].get('price'))) if len(items)==1 else None
            valid=price is not None and price.kind=='subscription'
            status=sub['status'] if valid else 'unpaid'
            trial=timestamp(sub.get('trial_end')) if valid and status=='trialing' else None
            conn.execute('''INSERT INTO billing_subscriptions(account_id,subscription_id,status,trial_until)
                VALUES (%s,%s,%s,%s) ON CONFLICT(subscription_id) DO UPDATE SET status=EXCLUDED.status,trial_until=EXCLUDED.trial_until''',
                (account,sid,status,trial))
            if trial:
                self._grant_locked(conn,account,f'trial:{sid}','trial',price.units,trial,sid)
            for inv in self.gateway.paid_invoices(customer,sid):
                if identifier(inv.get('customer'))!=customer or inv.get('subscription')!=sid:
                    raise BillingError('billing_ownership_mismatch')
                # Financial reversals outlive price retirement and every new-grant
                # eligibility rule. Inspect authoritative payments before filtering
                # invoice lines, and revoke any already-issued source monotonically.
                source='invoice:'+inv['id']
                refunded=False; invoice_disputed=False
                for intent in inv.get('payment_intents',[]):
                    r,d=self.gateway.payment_reversed(intent)
                    refunded=refunded or r; invoice_disputed=invoice_disputed or d
                disputed=disputed or invoice_disputed
                if refunded or invoice_disputed:
                    self._revoke_locked(conn,account,source)
                if inv.get('status')!='paid' or inv.get('amount_paid',0)<=0 or not inv.get('payment_intents'): continue
                lines=[line for line in inv.get('lines',{}).get('data',[]) if not line.get('proration',True)
                       and line.get('quantity')==1 and identifier(line.get('price')) in self.policy.prices
                       and self.policy.prices[identifier(line.get('price'))].kind=='subscription']
                if len(lines)!=1: continue
                line=lines[0]; until=timestamp(line['period']['end'])
                units=self.policy.prices[identifier(line['price'])].units
                self._grant_locked(conn,account,source,'subscription',units,until,sid,timestamp(line['period']['start']))
                if refunded or invoice_disputed:
                    self._revoke_locked(conn,account,source)
            paid=conn.execute('''SELECT max(e.access_until) AS until FROM entitlements e JOIN billing_grants g
                ON (e.account_id,e.id)=(g.account_id,g.entitlement_id)
                WHERE g.account_id=%s AND g.subscription_id=%s AND g.kind='subscription' AND g.revoked_at IS NULL''',
                (account,sid)).fetchone()['until']
            conn.execute('UPDATE billing_subscriptions SET paid_until=%s WHERE account_id=%s AND subscription_id=%s',(paid,account,sid))
        conn.execute("UPDATE billing_subscriptions SET status='unpaid',trial_until=NULL WHERE account_id=%s AND NOT(subscription_id=ANY(%s))",(account,seen))
        for order in conn.execute("SELECT * FROM billing_orders WHERE account_id=%s AND kind='topup' AND session_id IS NOT NULL",(account,)).fetchall():
            session=self.gateway.session(order['session_id'])
            if identifier(session.get('customer'))!=customer or session.get('client_reference_id')!=str(account) or session.get('id')!=order['session_id']:
                raise BillingError('billing_ownership_mismatch')
            if session.get('payment_status')!='paid' or session.get('status')!='complete': continue
            lines=session.get('line_items',{}).get('data',[])
            if session.get('mode')!='payment' or len(lines)!=1 or identifier(lines[0].get('price'))!=order['price_id'] or lines[0].get('quantity')!=1:
                raise BillingError('billing_purchase_mismatch')
            intent=identifier(session.get('payment_intent'))
            if not intent: raise BillingError('payment_unverified')
            refunded,dispute=self.gateway.payment_reversed(intent); disputed=disputed or dispute
            source='topup:'+intent
            until=timestamp(session['created'])+timedelta(seconds=order['ttl_seconds'])
            self._grant_locked(conn,account,source,'topup',order['units'],until)
            if refunded or dispute: self._revoke_locked(conn,account,source)
        if disputed:
            conn.execute("UPDATE accounts SET status='suspended' WHERE id=%s",(account,))
        conn.execute('UPDATE billing_accounts SET reconciled_at=now() WHERE account_id=%s',(account,))

    def reconcile(self, account_id):
        with self.store._connect() as conn:
            self.lock_account(conn,account_id)
            self._reconcile_locked(conn,account_id)

    def repair(self, limit=100):
        """Restart-safe inbox replay plus independent scheduled authoritative repair."""
        if type(limit) is not int or not 1<=limit<=1000:
            raise ValueError('invalid_limit')
        counts={'processed':0,'reconciled':0,'failed':0}
        with self.store._connect() as conn:
            events=conn.execute("SELECT * FROM webhook_events WHERE processed_at IS NULL ORDER BY COALESCE(last_attempt_at,received_at),event_id LIMIT %s",(limit,)).fetchall()
        for event in events:
            try:
                customer=event['customer_id'] or self.gateway.event_customer(event['event_type'],event['object_id'])
                with self.store._connect() as conn:
                    row=conn.execute('SELECT account_id FROM billing_accounts WHERE customer_id=%s',(customer,)).fetchone()
                    active=False
                    if row:
                        account=conn.execute('SELECT status FROM accounts WHERE id=%s FOR UPDATE',(row['account_id'],)).fetchone()
                        active=account['status']=='active'
                    current=conn.execute("SELECT processed_at FROM webhook_events WHERE provider='stripe' AND event_id=%s FOR UPDATE",(event['event_id'],)).fetchone()
                    if current['processed_at']: continue
                    if active: self._reconcile_locked(conn,row['account_id'])
                    elif row and account['status']=='deleted':
                        conn.execute('UPDATE account_deletions SET billing_cleanup_pending=true WHERE account_id=%s',(row['account_id'],))
                    conn.execute("UPDATE webhook_events SET account_id=%s,status='processed',processed_at=now(),attempts=attempts+1 WHERE provider='stripe' AND event_id=%s",
                                 (row['account_id'] if row else None,event['event_id']))
                    counts['processed']+=1
            except Exception:
                with self.store._connect() as conn:
                    conn.execute("UPDATE webhook_events SET status='failed',attempts=attempts+1,last_attempt_at=now() WHERE provider='stripe' AND event_id=%s AND processed_at IS NULL",(event['event_id'],))
                counts['failed']+=1
        with self.store._connect() as conn:
            accounts=conn.execute("""SELECT b.account_id FROM billing_accounts b JOIN accounts a ON a.id=b.account_id
               WHERE a.status='active' AND b.customer_id IS NOT NULL ORDER BY b.last_attempt_at NULLS FIRST,b.account_id LIMIT %s""",(limit,)).fetchall()
        for account in accounts:
            try:
                with self.store._connect() as conn:
                    self.lock_account(conn,account['account_id'])
                    conn.execute('UPDATE billing_accounts SET last_attempt_at=now() WHERE account_id=%s',(account['account_id'],))
                self.reconcile(account['account_id']); counts['reconciled']+=1
            except Exception: counts['failed']+=1
        from .deletion import Deletion
        cleanup=Deletion(self.store).repair_remote(self.gateway,limit)
        counts['deletion_completed']=cleanup['completed'];counts['failed']+=cleanup['failed']
        return counts

    @staticmethod
    def funding_grants_locked(conn, account, now):
        return conn.execute('''SELECT e.id,e.allowance_units-COALESCE(sum(COALESCE(a.charged_units,a.reserved_units)),0)::bigint AS available
          FROM billing_grants g JOIN entitlements e ON (g.account_id,g.entitlement_id)=(e.account_id,e.id)
          LEFT JOIN billing_allocations a ON (g.account_id,g.entitlement_id)=(a.account_id,a.entitlement_id)
          WHERE g.account_id=%s AND g.revoked_at IS NULL AND e.status='active' AND e.access_until>%s AND g.starts_at<=%s
          AND (g.kind NOT IN ('trial','subscription') OR EXISTS (
            SELECT 1 FROM billing_subscriptions s WHERE s.account_id=g.account_id AND s.subscription_id=g.subscription_id
            AND ((g.kind='trial' AND s.status='trialing') OR (g.kind='subscription' AND s.status IN ('active','past_due','canceled')))))
          GROUP BY e.id,e.allowance_units,e.access_until ORDER BY e.access_until,e.id''',(account,now,now)).fetchall()

    @classmethod
    def balance_locked(cls, conn, account_id, now):
        return sum(max(0,r['available']) for r in cls.funding_grants_locked(conn,account_id,now))

    def entitlement_locked(self, conn, account_id, now):
        account=conn.execute('SELECT status FROM accounts WHERE id=%s',(account_id,)).fetchone()
        if not account or account['status']!='active': return Entitlement('revoked',None,0,'revoked')
        balance=self.balance_locked(conn,account_id,now)
        options=[]
        for sub in conn.execute('SELECT * FROM billing_subscriptions WHERE account_id=%s',(account_id,)).fetchall():
            paid=conn.execute('''SELECT max(e.access_until) AS until FROM billing_grants g JOIN entitlements e
              ON (g.account_id,g.entitlement_id)=(e.account_id,e.id) WHERE g.account_id=%s AND g.subscription_id=%s
              AND g.kind='subscription' AND g.revoked_at IS NULL AND g.starts_at<=%s''',
              (account_id,sub['subscription_id'],now)).fetchone()['until']
            options.append(evaluate(sub['status'],paid,sub['trial_until'],balance,now=now))
        pilot=conn.execute('''SELECT max(e.access_until) AS until FROM billing_grants g JOIN entitlements e
           ON (g.account_id,g.entitlement_id)=(e.account_id,e.id) WHERE g.account_id=%s AND g.kind='pilot' AND g.revoked_at IS NULL''',(account_id,)).fetchone()['until']
        if pilot: options.append(evaluate('pilot',pilot,None,balance,now=now))
        allowed=[o for o in options if o.allowed]
        return max(allowed,key=lambda o:o.access_until) if allowed else Entitlement('paused',None,0,'subscription_required')

    def subscription_status(self, principal):
        with self.store._connect() as conn:
            self.store._authorize(conn,principal)
            return [r['status'] for r in conn.execute(
                'SELECT status FROM billing_subscriptions WHERE account_id=%s ORDER BY subscription_id',
                (principal.account_id,)).fetchall()]

    def entitlement(self, principal, *, now=None):
        now=now or datetime.now(UTC)
        with self.store._connect() as conn:
            self.lock_account(conn,principal.account_id); self.store._authorize(conn,principal)
            row=conn.execute('SELECT reconciled_at FROM billing_accounts WHERE account_id=%s',(principal.account_id,)).fetchone()
            if row and (row['reconciled_at'] is None or row['reconciled_at']<datetime.now(UTC)-timedelta(seconds=self.policy.stale_seconds)):
                self._reconcile_locked(conn,principal.account_id)
            return self.entitlement_locked(conn,principal.account_id,now)

    def reserve_locked(self, conn, account_id, reservation_id, units, now):
        if type(units) is not int or units<=0: raise BillingError('invalid_units')
        if conn.execute('SELECT 1 FROM billing_allocations WHERE account_id=%s AND reservation_id=%s',(account_id,reservation_id)).fetchone():
            raise BillingError('reservation_already_allocated')
        row=conn.execute('SELECT reconciled_at FROM billing_accounts WHERE account_id=%s',(account_id,)).fetchone()
        if row and (row['reconciled_at'] is None or row['reconciled_at']<now-timedelta(seconds=self.policy.stale_seconds)):
            raise BillingError('billing_stale')
        if not self.entitlement_locked(conn,account_id,now).allowed: raise BillingError('subscription_required')
        grants=self.funding_grants_locked(conn,account_id,now)
        if sum(max(0,g['available']) for g in grants)<units: raise BillingError('allowance_exhausted')
        for grant in grants:
            take=min(units,max(0,grant['available']))
            if take:
                conn.execute('''INSERT INTO billing_allocations(account_id,reservation_id,entitlement_id,reserved_units)
                    VALUES (%s,%s,%s,%s)''',(account_id,reservation_id,grant['id'],take)); units-=take
            if not units: break

    @staticmethod
    def settle_locked(conn, account_id, reservation_id, actual_units):
        if type(actual_units) is not int or actual_units<0: raise BillingError('invalid_units')
        rows=conn.execute('SELECT * FROM billing_allocations WHERE account_id=%s AND reservation_id=%s ORDER BY entitlement_id',
                          (account_id,reservation_id)).fetchall()
        if not rows: raise BillingError('reservation_not_found')
        if any(r['charged_units'] is not None for r in rows):
            if all(r['charged_units'] is not None for r in rows) and sum(r['charged_units'] for r in rows)==actual_units: return
            raise BillingError('settlement_conflict')
        if actual_units>sum(r['reserved_units'] for r in rows):
            # Caller retains uncertain/full reservation; never free capacity on underestimation.
            raise BillingError('reservation_exceeded')
        for row in rows:
            charge=min(actual_units,row['reserved_units']); actual_units-=charge
            conn.execute('''UPDATE billing_allocations SET charged_units=%s
               WHERE account_id=%s AND reservation_id=%s AND entitlement_id=%s''',(charge,account_id,reservation_id,row['entitlement_id']))

    @classmethod
    def release_locked(cls, conn, account_id, reservation_id):
        cls.settle_locked(conn,account_id,reservation_id,0)
