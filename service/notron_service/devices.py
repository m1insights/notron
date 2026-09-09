"""Account-serialized, server-clock worker grants. Apple writes are not cancellable."""
from datetime import timedelta
import math
from .usage import UsageError

class Devices:
    lease_seconds=60
    renew_seconds=20

    def __init__(self,store): self.store=store

    def _lock(self,c,p):
        c.execute('SELECT id FROM accounts WHERE id=%s FOR UPDATE',(p.account_id,))
        self.store._authorize(c,p)  # Recheck after waiting for account ownership.
        return c.execute('SELECT clock_timestamp() AS now').fetchone()['now']

    def _row(self,c,p):
        return c.execute('SELECT * FROM worker_leases WHERE account_id=%s',(p.account_id,)).fetchone()

    def _response(self,row,now):
        return {'fence':row['fence'],'lease_seconds':self.lease_seconds,'renew_seconds':self.renew_seconds,
                'wait_seconds':max(0,math.ceil((row['not_before']-now).total_seconds())),
                'valid_seconds':max(0,(row['expires_at']-now).total_seconds())}

    def acquire(self,p):
        with self.store._connect() as c:
            now=self._lock(c,p);row=self._row(c,p)
            if row and row['expires_at']>now:
                if row['device_id']!=p.device_id or row['released_at'] is not None:
                    raise UsageError('permission_required')
                return self._response(row,now)
            return self._grant(c,p,row,now,now)

    def _grant(self,c,p,row,now,start):
        fence=row['fence']+1 if row else 1
        result=c.execute('''INSERT INTO worker_leases(account_id,device_id,fence,expires_at,not_before,released_at)
            VALUES (%s,%s,%s,%s,%s,NULL) ON CONFLICT(account_id) DO UPDATE SET
            device_id=excluded.device_id,fence=excluded.fence,expires_at=excluded.expires_at,
            not_before=excluded.not_before,released_at=NULL RETURNING *''',
            (p.account_id,p.device_id,fence,start+timedelta(seconds=self.lease_seconds),start)).fetchone()
        return self._response(result,now)

    def transfer(self,p):
        # Destination is authenticated caller, never a supplied account/device ID.
        with self.store._connect() as c:
            now=self._lock(c,p);row=self._row(c,p)
            if row and row['device_id']==p.device_id and row['released_at'] is None and row['expires_at']>now:
                return self._response(row,now)  # Safe response-loss replay.
            # A pending destination has never been admitted. Preserve the
            # original drain barrier instead of extending it on repeated transfer.
            barrier=(row['not_before'] if row['not_before']>now else row['expires_at']) if row else now
            start=max(now,barrier)
            return self._grant(c,p,row,now,start)

    def _valid(self,c,p,fence,now):
        row=self._row(c,p)
        if (type(fence) is not int or not row or row['device_id']!=p.device_id or row['fence']!=fence
                or row['released_at'] is not None or row['expires_at']<=now or row['not_before']>now):
            raise UsageError('permission_required')
        return row

    def check(self,p,fence):
        with self.store._connect() as c:
            now=self._lock(c,p)
            return self._response(self._valid(c,p,fence,now),now)

    def renew(self,p,fence):
        with self.store._connect() as c:
            now=self._lock(c,p);self._valid(c,p,fence,now)
            row=c.execute("UPDATE worker_leases SET expires_at=%s WHERE account_id=%s RETURNING *",
                          (now+timedelta(seconds=self.lease_seconds),p.account_id)).fetchone()
            return self._response(row,now)

    def release(self,p,fence):
        with self.store._connect() as c:
            now=self._lock(c,p);row=self._row(c,p)
            if not row or row['device_id']!=p.device_id or row['fence']!=fence:raise UsageError('permission_required')
            # Keep expiry/fence: disconnect cannot prove an issued write stopped.
            c.execute('UPDATE worker_leases SET released_at=COALESCE(released_at,%s) WHERE account_id=%s',(now,p.account_id))
