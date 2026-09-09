"""Fail-closed managed Mac admission. Direct/BYO mode has no cloud lease claim."""
import atexit
import threading
import time
from .transport import ManagedError

class ManagedLease:
    def __init__(self,transport,*,clock=time.monotonic):
        self.transport=transport;self.clock=clock
        self.stopped=False;self._fence=0;self.expires=0.;self.ready=0.
        self.lock=threading.RLock();self.event=threading.Event();self.thread=None

    def _accept(self,data,started):
        try:
            fence=data['fence'];valid=data['valid_seconds'];wait=data['wait_seconds']
            if (type(fence) is not int or fence<=0 or type(valid) not in (int,float) or not 0<valid<=120
                    or type(wait) is not int or not 0<=wait<=60 or data['renew_seconds']!=20):raise ValueError()
            self._fence=fence;self.expires=started+valid
            # Wait is measured at response receipt (conservative), expiry at send.
            self.ready=self.clock()+wait
            if self.expires<=self.clock():raise ValueError()
        except Exception:
            self.stopped=True;raise ManagedError('permission_required') from None

    def acquire(self):
        with self.lock:
            if self.stopped:raise ManagedError('permission_required')
            started=self.clock()
            try:self._accept(self.transport.control('acquire',{}),started)
            except Exception:
                self.stopped=True;raise

    def fence(self):
        with self.lock:
            if self.stopped or getattr(self.transport,'stopped',False):
                raise ManagedError('permission_required')
            if self.clock()>=self.expires:
                self.stopped=True;raise ManagedError('permission_required')
            if self.clock()<self.ready:raise ManagedError('permission_required')
            return self._fence

    def renew(self):
        with self.lock:
            fence=self.fence();started=self.clock()
            try:self._accept(self.transport.control('renew',{'fence':fence}),started)
            except Exception:
                self.stopped=True;self.event.set();raise

    def require_effect(self):
        with self.lock:
            fence=self.fence()
            try:
                data=self.transport.control('check',{'fence':fence})
                if data.get('fence')!=fence:raise ManagedError('permission_required')
                self.fence()  # HTTP/token refresh may outlive local expiry.
            except Exception:
                self.stopped=True;self.event.set();raise

    def start(self):
        self.acquire()
        def heartbeat():
            while not self.event.wait(20):
                try:
                    if self.clock()<self.ready:continue
                    self.renew()
                except Exception:
                    self.event.set();return
        self.thread=threading.Thread(target=heartbeat,name='managed-lease',daemon=True)
        self.thread.start();atexit.register(self.stop)

    def stop(self):
        with self.lock:
            if self.stopped:return
            self.stopped=True;self.event.set()
            try:self.transport.control('release',{'fence':self._fence})
            except Exception:pass


def _verified_recovery(write):
    """Only exact local snapshots and fixed, ledger-backed receipts are exempt.

    Called after Executor's full identity/source/policy/revision validation; no
    model-provided flag alone can exempt a primary effect.
    """
    if write is None:return False
    from . import undo,recovery,operations,policy,workspace,conversation
    if _verified_action_receipt(write):return True
    snapshot=undo.peek(write.recovery_note_id or write.note_id) if (write.recovery_note_id or write.note_id) else None
    if snapshot and snapshot.snapshot_id==write.snapshot_id:
        if write.mode=='restore' and write.markdown==undo.restore_body(snapshot,write.title,write.folder,write.restore_receipt):return True
        if write.recovery_note_id and write.markdown==undo.copy_markdown(snapshot):return True
        if write.undo_reply:
            expected=undo.COPY_REPLY if write.recovery_receipt_id else (undo.ASK_REPLY if write.title==workspace.ASK else undo.offer_text(snapshot))
            if write.mode=='insert' and write.markdown==conversation.turn(expected):return True
    if write.operation_id.startswith('audit:') and write.operation_id.endswith(':write'):
        parent_id=write.operation_id.removesuffix(':write')
        record=operations.current().get(parent_id)
        data=recovery.get(parent_id) if record else None
        original=operations.current().get(data.get('primary_id','')) if data else None
        from hashlib import sha256
        if (original and original.status in (operations.S.APPLIED,operations.S.RECEIPTED)
                and parent_id=='audit:'+sha256(original.operation_id.encode()).hexdigest()
                and data.get('outcome')=='Operation verified' and write.mode=='append'
                and policy.current().system_role(write.note_id)==workspace.LOG
                and write.markdown==f"{record.created_at[:16]} — Operation verified"):
            return True
    return False


def require_effect(write=None):
    from .transport import configured
    transport=configured()
    if transport is None:return  # BYO supports only one active Mac, locally enforced.
    if _verified_recovery(write):return
    lease=getattr(transport,'worker_lease',None)
    if lease is None:raise ManagedError('permission_required')
    lease.require_effect()


def _verified_action_receipt(write):
    from dataclasses import asdict
    from . import requests,recovery,operations,conversation
    from .state import Action
    from .executor import WriteResult
    from .nodes import _confirmation
    envelope=requests.active_request()
    if envelope is None or write.mode not in ('append','insert'):return False
    checkpoint=recovery.get(envelope.request_id+':checkpoint:writer')
    if not checkpoint or checkpoint.get('intent') not in ('remind','schedule'):return False
    actions=checkpoint.get('actions',[])
    if len(actions)!=1:return False
    action=actions[0];oid=action.get('operation_id')
    primary=operations.current().get(oid) if oid else None
    if not primary or primary.request_id!=envelope.request_id or primary.status not in (operations.S.APPLIED,operations.S.RECEIPTED):return False
    saved=recovery.get(oid)
    if not saved or saved.get('action')!=action:return False
    # The writer checkpoint fixes the destination/anchor. The deterministic
    # confirmation is regenerated from the verified action, never cached prose.
    actual=asdict(write);actual.pop('content_sources')
    candidates=[]
    for target in checkpoint.get('writes',[]):
        target=dict(target);target.pop('content_sources',None);candidates.append(target)
    import json
    if json.dumps(actual,sort_keys=True) not in [json.dumps(t,sort_keys=True) for t in candidates]:return False
    answer=_confirmation(Action(**action),WriteResult(True,'verified'))
    if checkpoint.get('stuck'):
        title=checkpoint['reply_to'][0] if checkpoint.get('reply_to') else 'the source note'
        answer=f'You asked in **{title}**. That note holds a picture, so I answered here to preserve it.\n\n'+answer
    return write.markdown==conversation.turn(answer)
