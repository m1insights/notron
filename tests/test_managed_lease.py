import pytest
from notron.transport import ManagedError

class Service:
    def __init__(self): self.calls=[];self.fail=False
    def control(self,op,body):
        self.calls.append(op)
        if self.fail:raise ManagedError('permission_required')
        return {'fence':4,'lease_seconds':60,'renew_seconds':20,'wait_seconds':0,'valid_seconds':60}

def test_expiry_and_failed_renew_stop_new_effects():
    from notron.managed_lease import ManagedLease
    clock=[100.];s=Service();lease=ManagedLease(s,clock=lambda:clock[0]);lease.acquire()
    assert lease.fence()==4
    lease.require_effect();assert s.calls[-1]=='check'
    s.fail=True
    with pytest.raises(ManagedError):lease.renew()
    s.fail=False
    with pytest.raises(ManagedError):lease.require_effect()
    assert lease.stopped

def test_local_expiry_never_reacquires_silently():
    from notron.managed_lease import ManagedLease
    clock=[0.];s=Service();lease=ManagedLease(s,clock=lambda:clock[0]);lease.acquire()
    clock[0]=61
    with pytest.raises(ManagedError):lease.fence()
    assert s.calls==['acquire']

def test_transfer_wait_blocks_until_activation():
    from notron.managed_lease import ManagedLease
    clock=[0.];s=Service()
    s.control=lambda op,body:{'fence':5,'lease_seconds':60,'renew_seconds':20,'wait_seconds':60,'valid_seconds':120}
    lease=ManagedLease(s,clock=lambda:clock[0]);lease.acquire()
    with pytest.raises(ManagedError):lease.fence()
    assert not lease.stopped
    clock[0]=61
    assert lease.fence()==5

def test_effect_boundary_rechecks_server_after_long_provider_wait(monkeypatch):
    from notron import transport
    from notron.managed_lease import ManagedLease,require_effect
    clock=[0.];s=Service();lease=ManagedLease(s,clock=lambda:clock[0]);lease.acquire()
    monkeypatch.setattr(transport,'_managed',s)
    s.worker_lease=lease
    require_effect()
    # A response can arrive after transfer revoked its issuer; never write it.
    s.fail=True
    with pytest.raises(ManagedError):require_effect()

def test_executor_stops_new_note_write_when_managed_fence_lost(monkeypatch):
    from test_executor import in_note
    from notron import transport,workspace,executor
    live=in_note(monkeypatch)
    monkeypatch.setattr(transport,'_managed',Service())
    result=executor.Executor(audit=False).insert(workspace.ASK,'answer',after=1,anchor='original question')
    assert not result.ok
    assert live['writes']==[]

def test_verified_restore_survives_managed_lease_loss(monkeypatch):
    from test_executor import in_note
    from notron import transport,workspace,executor
    live=in_note(monkeypatch,workspace.TODAY)
    original='<div>Today</div><div>original</div>'
    executor.undo.save('n1',original,executor.revision(live['body']),'previous')
    executor.undo.promote('n1','previous')
    monkeypatch.setattr(transport,'_managed',Service())
    result=executor.Executor(audit=False).restore(workspace.TODAY,original)
    assert result.ok and live['writes']==[original]

def test_verified_audit_receipt_remains_local_after_lease_loss(monkeypatch):
    from test_executor import in_note
    from notron import transport,workspace,audit,operations
    live=in_note(monkeypatch,workspace.LOG)
    store=operations.current();store.prepare('r','primary','a'*64)
    store.transition('primary',operations.S.PREPARED,operations.S.APPLYING)
    store.transition('primary',operations.S.APPLYING,operations.S.APPLIED)
    audit.enqueue('Operation verified',operation_id='primary')
    monkeypatch.setattr(transport,'_managed',Service())
    audit.drain()
    assert len(live['writes'])==1
    assert 'Operation verified' in live['body']

@pytest.mark.parametrize('status',[200,403])
def test_protected_bootstrap_acquires_and_releases_real_worker_manager(monkeypatch,status):
    import socket,os,threading
    from notron import credentials,transport
    parent,child=socket.socketpair();calls=[]
    def native():
        assert parent.recv(4096)==b'{"operation":"configuration"}\n'
        parent.sendall(b'{"service_url":"https://service.example.test"}\n')
        while True:
            raw=parent.recv(4096)
            if not raw:break
            parent.sendall(b'{"access_token":"synthetic-access"}\n')
        parent.close()
    thread=threading.Thread(target=native);thread.start()
    # Inject the documented protected-store seam; no helper execution or gate edit.
    monkeypatch.setattr(credentials,'_provider',credentials.KeychainStore('/nonexistent-synthetic-helper'))
    monkeypatch.setattr(transport,'_managed',None)
    monkeypatch.setattr(transport,'_lease_fence_supplier',lambda:0)
    monkeypatch.setenv('NOTRON_MANAGED_SESSION_FD','0')
    original_dup=os.dup
    monkeypatch.setattr(os,'dup',lambda fd:original_dup(child.fileno()) if fd==0 else original_dup(fd))
    def http(self,op,body,token,deadline):
        calls.append((op,token))
        return status,{'fence':9,'lease_seconds':60,'renew_seconds':20,'wait_seconds':0,'valid_seconds':60}
    monkeypatch.setattr(transport.ManagedTransport,'_http',http)
    try:
        if status==403:
            with pytest.raises(ManagedError,match='permission_required'):transport.bootstrap_inherited()
            return
        transport.bootstrap_inherited()
        assert transport.configured().lease_fence()==9
        assert transport.configured().worker_lease.thread.is_alive()
        transport.configured().stop()
        assert [x[0] for x in calls]==['worker/lease/acquire','worker/lease/release']
    finally:
        if transport.configured():transport.configured().stop()
        child.close();thread.join(timeout=2)
    assert not thread.is_alive()

from test_action_recovery import recovery_harness

def test_verified_action_receipt_recovers_offline_without_repeating_effect(recovery_harness,monkeypatch):
    from notron import transport,requests
    h=recovery_harness;h.submit('r1','Call dentist');h.fail_at('before_receipt');h.run_once()
    assert len(h.rows)==1
    monkeypatch.setattr(transport,'_managed',Service())
    h.run_once()
    assert len(h.rows)==1
    assert requests.current().get('r1').status=='completed'

def test_watcher_repairs_verified_receipt_before_cloud_probe(recovery_harness,monkeypatch):
    from notron import transport,requests,watch,worker
    from notron.health import WorkerLock
    h=recovery_harness;h.submit('r1','Call dentist');h.fail_at('before_receipt');h.run_once()
    monkeypatch.setattr(transport,'_managed',Service())
    monkeypatch.setattr(worker,'probe',lambda:(_ for _ in ()).throw(AssertionError('cloud probe must not gate receipt')))
    watcher=watch.Watcher(None)
    with WorkerLock():watcher.tick()
    assert requests.current().get('r1').status=='completed'
    assert len(h.rows)==1

def test_heartbeat_renews_on_twenty_second_schedule():
    from notron.managed_lease import ManagedLease
    s=Service();lease=ManagedLease(s,clock=lambda:100.)
    class Wake:
        def __init__(self):self.waits=[]
        def wait(self,seconds):self.waits.append(seconds);return len(self.waits)>1
        def set(self):pass
    wake=Wake();lease.event=wake
    lease.start();lease.thread.join(timeout=2)
    try:
        assert not lease.thread.is_alive()
        assert wake.waits==[20,20]
        assert s.calls==['acquire','renew']
    finally:lease.stop()

def test_new_eventkit_action_is_not_issued_without_managed_grant(recovery_harness,monkeypatch):
    from notron import transport,operations
    h=recovery_harness;h.submit('r1','Call dentist')
    monkeypatch.setattr(transport,'_managed',Service())
    h.run_once()
    assert h.rows==[]
    op=operations.current().get('r1:action:0')
    assert op.status==operations.S.NEEDS_REVIEW and op.failure_code=='policy_changed'
