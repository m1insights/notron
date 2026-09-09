"""Privileged CLI-only operations. Never mount these methods on public routes."""
from hashlib import sha256
from .billing import Billing
from .deletion import Deletion


class RateLimited(RuntimeError):
    pass


class Operations:
    def __init__(self, store):
        self.store=store

    def admit(self, principal, *, limit=120):
        with self.store._connect() as c:
            Billing.lock_account(c,principal.account_id)
            self.store._authorize(c,principal)
            row=c.execute('''INSERT INTO account_rate_windows VALUES (%s,clock_timestamp(),1)
                ON CONFLICT(account_id) DO UPDATE SET
                requests=CASE WHEN account_rate_windows.started_at<=clock_timestamp()-interval '60 seconds'
                    THEN 1 ELSE account_rate_windows.requests+1 END,
                started_at=CASE WHEN account_rate_windows.started_at<=clock_timestamp()-interval '60 seconds'
                    THEN clock_timestamp() ELSE account_rate_windows.started_at END
                WHERE account_rate_windows.started_at<=clock_timestamp()-interval '60 seconds'
                    OR account_rate_windows.requests<%s RETURNING requests''',(principal.account_id,limit)).fetchone()
            return row is not None

    def rotate_cache_key(self, key):
        if not isinstance(key,bytes) or len(key)!=32:raise ValueError('invalid_key')
        with self.store._connect() as c:
            # Same lock used by all cache writers, before checking generation.
            c.execute('SELECT pg_advisory_xact_lock(505007)')
            count=c.execute('DELETE FROM usage_cache').rowcount
            c.execute('''INSERT INTO cache_key_generation VALUES (true,%s)
                ON CONFLICT(singleton) DO UPDATE SET key_digest=excluded.key_digest''',(sha256(key).hexdigest(),))
            return count

    def reconcile_cost(self, account_id, reservation_id, actual, evidence):
        if type(actual) is not int or actual<0 or actual>2**63-1 or not isinstance(evidence,str) or not evidence.strip() or len(evidence)>2048:
            raise ValueError('invalid_evidence')
        digest=sha256(evidence.encode()).hexdigest()
        with self.store._connect() as c:
            account=c.execute('SELECT status FROM accounts WHERE id=%s FOR UPDATE',(account_id,)).fetchone()
            if not account:raise ValueError('account_missing')
            r=c.execute('SELECT * FROM usage_reservations WHERE account_id=%s AND id=%s FOR UPDATE',(account_id,reservation_id)).fetchone()
            m=c.execute('SELECT * FROM usage_meter WHERE account_id=%s AND reservation_id=%s',(account_id,reservation_id)).fetchone()
            previous=c.execute('SELECT * FROM operator_evidence WHERE account_id=%s AND reservation_id=%s',(account_id,reservation_id)).fetchone()
            if previous:
                if previous['actual_micro_usd']==actual and previous['evidence_digest']==digest:return
                raise ValueError('evidence_conflict')
            if not r or not m or r['status'] not in ('reserved','uncertain') or r['reserved_units']%m['reserved_micro_usd']:
                raise ValueError('reconciliation_requires_review')
            units=actual*(r['reserved_units']//m['reserved_micro_usd'])
            c.execute('SELECT pg_advisory_xact_lock(505004)')
            if actual<=m['reserved_micro_usd']:
                Billing.settle_locked(c,account_id,reservation_id,units)
                c.execute("UPDATE usage_reservations SET status='settled',actual_units=%s WHERE account_id=%s AND id=%s",(units,account_id,reservation_id))
            else:
                # Record the full evidenced provider debit but do not charge
                # unreserved allowance or free the held allocation.
                c.execute("UPDATE usage_reservations SET status='uncertain' WHERE account_id=%s AND id=%s",(account_id,reservation_id))
            c.execute('UPDATE usage_meter SET actual_micro_usd=%s WHERE account_id=%s AND reservation_id=%s',(actual,account_id,reservation_id))
            c.execute('INSERT INTO operator_evidence(account_id,reservation_id,evidence_digest,actual_micro_usd) VALUES (%s,%s,%s,%s)',(account_id,reservation_id,digest,actual))

    def status(self):
        with self.store._connect() as c:
            return {
                'billing_events_pending':c.execute('SELECT count(*) AS n FROM webhook_events WHERE processed_at IS NULL').fetchone()['n'],
                'billing_oldest_event_seconds':c.execute('SELECT COALESCE(EXTRACT(EPOCH FROM clock_timestamp()-min(received_at)),0)::bigint AS n FROM webhook_events WHERE processed_at IS NULL').fetchone()['n'],
                'billing_stale_accounts':c.execute("SELECT count(*) AS n FROM billing_accounts b JOIN accounts a ON a.id=b.account_id WHERE a.status='active' AND b.customer_id IS NOT NULL AND (b.reconciled_at IS NULL OR b.reconciled_at<clock_timestamp()-interval '300 seconds')").fetchone()['n'],
                'held_usage':c.execute("SELECT count(*) AS n FROM usage_reservations WHERE status IN ('reserved','uncertain')").fetchone()['n'],
                'billing_cleanup_pending':c.execute('SELECT count(*) AS n FROM account_deletions WHERE billing_cleanup_pending').fetchone()['n'],
                'identity_cleanup_pending':c.execute('SELECT count(*) AS n FROM account_deletions WHERE identity_cleanup_pending').fetchone()['n'],
            }

    def periodic(self, billing, retention_days, limit):
        result={'failed':0}
        if billing:result.update(billing.repair(limit))
        result.update(Deletion(self.store,retention_days).purge_expired(limit))
        with self.store._connect() as c:
            result['abuse_windows']=c.execute("DELETE FROM account_rate_windows WHERE account_id IN (SELECT account_id FROM account_rate_windows WHERE started_at<=clock_timestamp()-interval '60 seconds' ORDER BY started_at LIMIT %s) AND started_at<=clock_timestamp()-interval '60 seconds'",(limit,)).rowcount
        result.update(self.status())
        return result


def main(argv=None):
    import argparse,json
    from uuid import UUID
    from .config import Settings
    from .store import PostgresStore
    parser=argparse.ArgumentParser(description='Privileged managed service operations; use separately controlled DB role')
    parser.add_argument('command',choices=['migrate','periodic','status','rotate-cache-key','reconcile-cost','confirm-identity-erasure'])
    parser.add_argument('--limit',type=int,default=100)
    parser.add_argument('--account',type=UUID)
    parser.add_argument('--reservation',type=UUID)
    parser.add_argument('--actual-micro-usd',type=int)
    parser.add_argument('--evidence-file',help='Private file containing provider-confirmed evidence reference; only its digest is stored')
    args=parser.parse_args(argv)
    if not 1<=args.limit<=10000:parser.error('invalid limit')
    try:
        settings=Settings.from_env();store=PostgresStore(settings.database_url);ops=Operations(store)
        if args.command=='migrate':store.migrate();result={'migrated':True}
        else:
            if not store.readiness():raise ValueError('schema_not_ready')
            if args.command=='periodic':
                from .billing import StripeGateway
                policy=settings.billing_policy
                billing=Billing(store,StripeGateway(settings.stripe_secret_key),policy) if policy else None
                result=ops.periodic(billing,settings.financial_retention_days,args.limit)
            elif args.command=='status':result=ops.status()
            elif args.command=='rotate-cache-key':result={'cache_purged':ops.rotate_cache_key(settings.encryption_key)}
            else:
                from pathlib import Path
                if not args.account or not args.evidence_file:raise ValueError('evidence_required')
                evidence=Path(args.evidence_file).read_text().strip()
                if args.command=='reconcile-cost':ops.reconcile_cost(args.account,args.reservation,args.actual_micro_usd,evidence)
                else:Deletion(store,settings.financial_retention_days).confirm_identity_erasure(args.account,evidence)
                result={'recorded':True}
        print(json.dumps(result,sort_keys=True))
        return int(any(result.get(k,0) for k in ('failed','held_usage','billing_cleanup_pending','identity_cleanup_pending','billing_events_pending','billing_stale_accounts')))
    except Exception:
        print('{"code":"operation_failed"}')
        return 1


if __name__=='__main__':raise SystemExit(main())
