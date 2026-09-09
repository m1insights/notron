"""Immediate content erasure and explicit bounded accounting retention.

Identity digests are permanent non-resurrection tombstones, not login identities.
No Apple records or desktop recovery data are reachable from this service.
"""
from datetime import timedelta
import hashlib
import json
from .billing import BillingError, identifier

def identity_digest(issuer,subject):
    return hashlib.sha256(json.dumps([issuer,subject],separators=(',',':')).encode()).hexdigest()

class Deletion:
    def __init__(self,store,financial_retention_days=None):
        if financial_retention_days is not None and (type(financial_retention_days) is not int or financial_retention_days<=0):
            raise ValueError('invalid_retention')
        self.store=store;self.financial_retention_days=financial_retention_days

    def delete(self,p):
        with self.store._connect() as c:
            c.execute('SELECT id FROM accounts WHERE id=%s FOR UPDATE',(p.account_id,))
            self.store._authorize(c,p)
            self._erase_locked(c,p.account_id)
        return {'status':'deleted','financial_retention_days':self.financial_retention_days,
                'retention_pending':self.financial_retention_days is None,'identity_tombstone':'indefinite','remote_cleanup_pending':True}

    def _erase_locked(self,c,account_id):
        rows=c.execute('SELECT issuer,subject FROM identities WHERE account_id=%s',(account_id,)).fetchall()
        for row in rows:
            c.execute('INSERT INTO identity_deletion_tombstones(digest) VALUES (%s) ON CONFLICT DO NOTHING',
                      (identity_digest(row['issuer'],row['subject']),))
        c.execute("UPDATE accounts SET status='deleted',contact_email=NULL WHERE id=%s",(account_id,))
        c.execute('UPDATE devices SET revoked_at=COALESCE(revoked_at,now()) WHERE account_id=%s',(account_id,))
        for table in ('usage_cache','worker_leases','identity_sessions','identities','audit_metadata'):
            c.execute(f'DELETE FROM {table} WHERE account_id=%s',(account_id,))
        c.execute("UPDATE entitlements SET status='revoked' WHERE account_id=%s",(account_id,))
        c.execute("UPDATE billing_orders SET return_url='',checkout_url=NULL WHERE account_id=%s",(account_id,))
        # No content-bearing retry digests survive; retained UUIDs prevent reuse.
        c.execute("UPDATE usage_reservations SET payload_digest=%s WHERE account_id=%s",('0'*64,account_id))
        now=c.execute('SELECT clock_timestamp() AS now').fetchone()['now']
        financial=c.execute('''SELECT 1 FROM billing_accounts WHERE account_id=%s
            UNION ALL SELECT 1 FROM usage_reservations WHERE account_id=%s
            UNION ALL SELECT 1 FROM billing_grants WHERE account_id=%s LIMIT 1''',(account_id,account_id,account_id)).fetchone()
        until=now+timedelta(days=self.financial_retention_days) if self.financial_retention_days else None
        if not financial:until=now
        c.execute('''DELETE FROM devices d WHERE account_id=%s AND NOT EXISTS
            (SELECT 1 FROM usage_reservations r WHERE r.account_id=d.account_id AND r.device_id=d.id)''',(account_id,))
        customer=c.execute('SELECT customer_id FROM billing_accounts WHERE account_id=%s',(account_id,)).fetchone()
        c.execute('''INSERT INTO account_deletions(account_id,financial_delete_after,security_delete_after,billing_cleanup_pending)
            VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING''',(account_id,until,now,bool(customer and customer['customer_id'])))

    def purge_expired(self,limit=100):
        """Task 6 scheduler seam. Bounded batches; account lock precedes all erasure."""
        if type(limit) is not int or not 1<=limit<=1000:raise ValueError('invalid_limit')
        counts={'accounts':0,'cache':0,'security':0,'pending_completed':0}
        with self.store._connect() as c:
            pending=c.execute("SELECT id FROM accounts WHERE status='deleting' ORDER BY id LIMIT %s",(limit,)).fetchall()
        for row in pending:
            with self.store._connect() as c:
                account=c.execute("SELECT id FROM accounts WHERE id=%s AND status='deleting' FOR UPDATE",(row['id'],)).fetchone()
                if account:
                    self._erase_locked(c,account['id']);counts['pending_completed']+=1
        with self.store._connect() as c:
            counts['cache']=c.execute('DELETE FROM usage_cache WHERE ctid IN (SELECT ctid FROM usage_cache WHERE expires_at<=clock_timestamp() ORDER BY expires_at LIMIT %s)',(limit,)).rowcount
            counts['security']=c.execute('DELETE FROM audit_metadata WHERE ctid IN (SELECT ctid FROM audit_metadata WHERE expires_at<=clock_timestamp() ORDER BY expires_at LIMIT %s)',(limit,)).rowcount
            rows=c.execute('''SELECT account_id FROM account_deletions WHERE financial_delete_after<=clock_timestamp()
                AND security_delete_after<=clock_timestamp() AND NOT billing_cleanup_pending AND NOT identity_cleanup_pending ORDER BY financial_delete_after LIMIT %s''',(limit,)).fetchall()
        for row in rows:
            account=row['account_id']
            with self.store._connect() as c:
                locked=c.execute("SELECT id FROM accounts WHERE id=%s AND status='deleted' FOR UPDATE",(account,)).fetchone()
                if not locked:continue
                eligible=c.execute('''SELECT 1 FROM account_deletions WHERE account_id=%s
                    AND financial_delete_after<=clock_timestamp() AND security_delete_after<=clock_timestamp()
                    AND NOT billing_cleanup_pending AND NOT identity_cleanup_pending''',(account,)).fetchone()
                if not eligible:continue
                # Never reset this month's aggregate ceiling, or erase unknown costs.
                held=c.execute('''SELECT 1 FROM usage_reservations r LEFT JOIN usage_meter m
                    ON m.account_id=r.account_id AND m.reservation_id=r.id WHERE r.account_id=%s
                    AND (r.status IN ('reserved','uncertain') OR m.month>=date_trunc('month',clock_timestamp() AT TIME ZONE 'UTC')::date)
                    LIMIT 1''',(account,)).fetchone()
                if held:continue
                for table in ('usage_cache','billing_allocations','usage_meter','usage_reservations','billing_grants',
                              'entitlements','billing_orders','billing_subscriptions','billing_accounts','webhook_events',
                              'worker_leases','audit_metadata','identity_sessions','identities','devices','account_deletions','accounts'):
                    column='id' if table=='accounts' else 'account_id'
                    c.execute(f'DELETE FROM {table} WHERE {column}=%s',(account,))
                counts['accounts']+=1
        return counts

    def confirm_identity_erasure(self,account_id,evidence_reference):
        """Operator-only attestation after provider-confirmed erasure; no public route.

        Keep only a digest of the private evidence reference, never its contents.
        This cannot verify an unspecified OIDC vendor's erasure API.
        """
        if not isinstance(evidence_reference,str) or not evidence_reference.strip():raise ValueError('evidence_required')
        with self.store._connect() as c:
            owner=c.execute("SELECT id FROM accounts WHERE id=%s AND status='deleted' FOR UPDATE",(account_id,)).fetchone()
            if not owner:raise ValueError('deleted_account_required')
            return c.execute('''UPDATE account_deletions SET identity_cleanup_pending=false,identity_confirmation_digest=%s
                WHERE account_id=%s''',(hashlib.sha256(evidence_reference.encode()).hexdigest(),account_id)).rowcount

    def repair_remote(self,gateway,limit=100):
        """Retry account-owned Stripe cancellation; never invoice, prorate or refund.

        Complete authoritative lists include Checkout completed before persistence.
        Account lock serializes this work with checkout and final erasure.
        """
        if type(limit) is not int or not 1<=limit<=1000:raise ValueError('invalid_limit')
        counts={'completed':0,'failed':0}
        with self.store._connect() as c:
            rows=c.execute('''SELECT account_id FROM account_deletions WHERE billing_cleanup_pending
                ORDER BY remote_attempt_at NULLS FIRST,account_id LIMIT %s''',(limit,)).fetchall()
        for row in rows:
            account=row['account_id']
            try:
                with self.store._connect() as c:
                    owner=c.execute("SELECT id FROM accounts WHERE id=%s AND status='deleted' FOR UPDATE",(account,)).fetchone()
                    job=c.execute('SELECT billing_cleanup_pending FROM account_deletions WHERE account_id=%s',(account,)).fetchone()
                    if not owner or not job or not job['billing_cleanup_pending']:continue
                    billing=c.execute('SELECT customer_id FROM billing_accounts WHERE account_id=%s',(account,)).fetchone()
                    if billing and billing['customer_id']:
                        customer=billing['customer_id']
                        unresolved={str(r['id']) for r in c.execute('SELECT id FROM billing_orders WHERE account_id=%s AND session_id IS NULL',(account,)).fetchall()}
                        async_pending=False
                        for session in gateway.checkout_sessions(customer):
                            sid=session.get('id')
                            if not sid or identifier(session.get('customer'))!=customer or session.get('client_reference_id')!=str(account):
                                raise BillingError('billing_ownership_mismatch')
                            order_id=(session.get('metadata') or {}).get('order_id')
                            unresolved.discard(order_id)
                            if session.get('status')=='open':
                                closed=gateway.expire_checkout(sid,f'notron-delete-checkout-{account}-{sid}')
                                if closed.get('id')!=sid or closed.get('status')!='expired' or identifier(closed.get('customer'))!=customer:
                                    raise BillingError('remote_cleanup_pending')
                            elif session.get('status') not in ('expired','complete'):
                                raise BillingError('remote_cleanup_pending')
                            elif session.get('status')=='complete' and session.get('mode')=='subscription' and not session.get('subscription'):
                                async_pending=True
                        for sub in gateway.subscriptions(customer):
                            sid=sub.get('id')
                            if not sid or identifier(sub.get('customer'))!=customer:raise BillingError('billing_ownership_mismatch')
                            existing=c.execute('SELECT account_id FROM billing_subscriptions WHERE subscription_id=%s',(sid,)).fetchone()
                            if existing and existing['account_id']!=account:raise BillingError('billing_ownership_mismatch')
                            if sub.get('status') not in ('canceled','incomplete_expired'):
                                canceled=gateway.cancel_subscription(sid,f'notron-delete-subscription-{account}-{sid}')
                                if (canceled.get('id')!=sid or canceled.get('status')!='canceled'
                                        or identifier(canceled.get('customer'))!=customer):raise BillingError('remote_cleanup_pending')
                            c.execute("UPDATE billing_subscriptions SET status='canceled' WHERE account_id=%s AND subscription_id=%s",(account,sid))
                        if unresolved or async_pending:
                            # An issued Checkout request may still be completing
                            # remotely. Cancel known subscriptions, but keep retrying.
                            raise BillingError('remote_cleanup_pending')
                    c.execute('''UPDATE account_deletions SET billing_cleanup_pending=false,
                        remote_attempt_at=now(),remote_attempts=remote_attempts+1 WHERE account_id=%s''',(account,))
                    counts['completed']+=1
            except Exception:
                with self.store._connect() as c:
                    c.execute('SELECT id FROM accounts WHERE id=%s FOR UPDATE',(account,))
                    c.execute('UPDATE account_deletions SET remote_attempt_at=now(),remote_attempts=remote_attempts+1 WHERE account_id=%s',(account,))
                counts['failed']+=1
        return counts
