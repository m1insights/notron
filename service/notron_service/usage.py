"""Atomic allowance and aggregate currency reservations; no optimistic retries."""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from hashlib import sha256
import json
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .billing import BillingError

class UsageError(RuntimeError):
    pass

@dataclass(frozen=True)
class Reservation:
    id: object
    response: dict | None = None

class Usage:
    def __init__(self,store,billing,key,*,monthly_micro_usd,units_per_micro_usd,max_concurrency=2,cache_seconds=86400):
        if any(type(x) is not int or x<=0 for x in (monthly_micro_usd,units_per_micro_usd,max_concurrency,cache_seconds)):
            raise ValueError('invalid_meter_configuration')
        self.store,self.billing,self.cipher=store,billing,AESGCM(key)
        self.key_digest=sha256(key).hexdigest()
        self.monthly_micro_usd,self.units_per_micro_usd=monthly_micro_usd,units_per_micro_usd
        self.max_concurrency,self.cache_seconds=max_concurrency,cache_seconds

    def authorize_locked(self,c,p,fence):
        self.billing.lock_account(c,p.account_id)
        self.store._authorize(c,p)
        lease=c.execute('SELECT * FROM worker_leases WHERE account_id=%s',(p.account_id,)).fetchone()
        now=c.execute('SELECT clock_timestamp() AS now').fetchone()['now']
        if not lease or lease['device_id']!=p.device_id or lease['fence']!=fence or lease['expires_at']<=now or lease['not_before']>now or lease['released_at'] is not None:
            raise UsageError('permission_required')

    def check_admission(self,p,fence):
        with self.store._connect() as c: self.authorize_locked(c,p,fence)

    def _replay(self,c,p,request_id,digest):
        r=c.execute('SELECT * FROM usage_reservations WHERE account_id=%s AND request_id=%s FOR UPDATE',(p.account_id,request_id)).fetchone()
        if not r: return None
        if r['payload_digest']!=digest: raise UsageError('request_conflict')
        cache=c.execute('SELECT * FROM usage_cache WHERE account_id=%s AND reservation_id=%s AND expires_at>clock_timestamp()', (p.account_id,r['id'])).fetchone()
        if not cache: raise UsageError('outcome_uncertain')
        raw=bytes(cache['ciphertext']); aad=f'{p.account_id}:{r["id"]}:{digest}'.encode()
        try: response=json.loads(self.cipher.decrypt(raw[:12],raw[12:],aad))
        except Exception: raise UsageError('outcome_uncertain') from None
        return Reservation(r['id'],response)

    def reserve(self,p,request_id,digest,ceiling,*,lease_fence,rate_version):
        if type(ceiling) is not int or ceiling<=0 or ceiling*self.units_per_micro_usd>2**63-1:
            raise UsageError('provider_unavailable')
        # Replay needs active ownership, but never a second paid entitlement.
        with self.store._connect() as c:
            self.billing.lock_account(c,p.account_id); self.store._authorize(c,p)
            replay=self._replay(c,p,request_id,digest)
            if replay: return replay
        entitlement=self.billing.entitlement(p)
        if not entitlement.allowed: raise UsageError('subscription_required')
        with self.store._connect() as c:
            self.authorize_locked(c,p,lease_fence)
            replay=self._replay(c,p,request_id,digest)
            if replay: return replay
            now=c.execute('SELECT clock_timestamp() AS now').fetchone()['now']
            month=now.astimezone(timezone.utc).date().replace(day=1)
            # Every currency mutation locks account, then this global budget lock.
            c.execute('SELECT pg_advisory_xact_lock(505004)')
            spent=c.execute('SELECT COALESCE(sum(COALESCE(actual_micro_usd,reserved_micro_usd)),0) AS n FROM usage_meter WHERE month=%s',(month,)).fetchone()['n']
            if spent+ceiling>self.monthly_micro_usd: raise UsageError('provider_unavailable')
            running=c.execute("SELECT count(*) AS n FROM usage_reservations WHERE account_id=%s AND status IN ('reserved','uncertain')",(p.account_id,)).fetchone()['n']
            if running>=self.max_concurrency: raise UsageError('provider_unavailable')
            grants=self.billing.funding_grants_locked(c,p.account_id,now)
            grant=next((g for g in grants if g['available']>0),None)
            if not grant: raise UsageError('allowance_exhausted')
            rid=uuid4(); units=ceiling*self.units_per_micro_usd
            c.execute('''INSERT INTO usage_reservations(account_id,id,device_id,request_id,entitlement_id,payload_digest,reserved_units)
              VALUES (%s,%s,%s,%s,%s,%s,%s)''',(p.account_id,rid,p.device_id,request_id,grant['id'],digest,units))
            try: self.billing.reserve_locked(c,p.account_id,rid,units,now)
            except BillingError as e: raise UsageError(str(e)) from None
            c.execute('INSERT INTO usage_meter VALUES (%s,%s,%s,%s,%s,NULL)',(p.account_id,rid,month,rate_version,ceiling))
            return Reservation(rid)

    def settle(self,p,reservation_id,actual,response):
        if type(actual) is not int or actual<0: raise UsageError('outcome_uncertain')
        with self.store._connect() as c:
            self.billing.lock_account(c,p.account_id)
            r=c.execute('SELECT * FROM usage_reservations WHERE account_id=%s AND id=%s FOR UPDATE',(p.account_id,reservation_id)).fetchone()
            if not r: raise UsageError('outcome_uncertain')
            evidence=c.execute('SELECT actual_micro_usd FROM operator_evidence WHERE account_id=%s AND reservation_id=%s',(p.account_id,reservation_id)).fetchone()
            if evidence:
                # Operator reconciliation is authoritative and cannot create
                # new retry content or be overwritten by a late worker.
                if evidence['actual_micro_usd']!=actual:raise UsageError('outcome_uncertain')
                return
            meter=c.execute('SELECT reserved_micro_usd FROM usage_meter WHERE account_id=%s AND reservation_id=%s',(p.account_id,reservation_id)).fetchone()
            if not meter or r['reserved_units']%meter['reserved_micro_usd']: raise UsageError('outcome_uncertain')
            units=actual*(r['reserved_units']//meter['reserved_micro_usd'])
            if units>r['reserved_units']: raise UsageError('outcome_uncertain')
            c.execute('SELECT pg_advisory_xact_lock(505004)')
            self.billing.settle_locked(c,p.account_id,reservation_id,units)
            c.execute("UPDATE usage_reservations SET actual_units=%s,status='settled' WHERE account_id=%s AND id=%s",(units,p.account_id,reservation_id))
            c.execute('UPDATE usage_meter SET actual_micro_usd=%s WHERE account_id=%s AND reservation_id=%s',(actual,p.account_id,reservation_id))
            # Deletion/revocation must not be undone by late response caching.
            active=c.execute("SELECT 1 FROM accounts a JOIN devices d ON d.account_id=a.id WHERE a.id=%s AND a.status='active' AND d.id=%s AND d.revoked_at IS NULL",(p.account_id,p.device_id)).fetchone()
            c.execute('SELECT pg_advisory_xact_lock(505007)')
            generation=c.execute('SELECT key_digest FROM cache_key_generation WHERE singleton=true').fetchone()
            if active and (not generation or generation['key_digest']==self.key_digest):
                nonce=os.urandom(12); aad=f'{p.account_id}:{reservation_id}:{r["payload_digest"]}'.encode()
                encrypted=nonce+self.cipher.encrypt(nonce,json.dumps(response,separators=(',',':'),allow_nan=False).encode(),aad)
                c.execute('INSERT INTO usage_cache VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',(p.account_id,reservation_id,encrypted,datetime.now(timezone.utc)+timedelta(seconds=self.cache_seconds)))

    def uncertain(self,p,reservation_id):
        with self.store._connect() as c:
            self.billing.lock_account(c,p.account_id)
            c.execute("UPDATE usage_reservations SET status='uncertain' WHERE account_id=%s AND id=%s AND status='reserved'",(p.account_id,reservation_id))

    def purge_expired(self):
        with self.store._connect() as c:
            return c.execute('DELETE FROM usage_cache WHERE expires_at<=clock_timestamp()').rowcount
