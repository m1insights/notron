import importlib.util
import json
import pytest
from types import SimpleNamespace
from test_billing import billing, db, pay, deliver


def test_scheduled_repair_entrypoint_exists():
    assert importlib.util.find_spec('notron_service.billing_repair') is not None


def test_scheduler_uses_real_store_and_returns_nonzero_on_incomplete_repair(billing,monkeypatch,capsys):
    import notron_service.billing_repair as command
    service,gateway,a,b=billing
    pay(billing); deliver(service,customer='cus_'+str(a.account_id))
    monkeypatch.setattr(command,'create_default_app',lambda:SimpleNamespace(state=SimpleNamespace(services=SimpleNamespace(billing=service))))
    assert command.main(['--limit','20'])==0
    assert json.loads(capsys.readouterr().out)['processed']==1
    gateway.fail=True
    assert command.main(['--limit','20'])==1
    assert 'private' not in capsys.readouterr().out


@pytest.mark.parametrize('limit',[1001,-1,0,True,1.5])
def test_direct_billing_repair_rejects_invalid_batch_before_database(limit):
    from notron_service.billing import Billing
    class Store:
        called=False
        def _connect(self):
            self.called=True
            raise ValueError('unexpected_database_access')
    store=Store()
    with pytest.raises(ValueError,match='invalid_limit'):
        Billing(store,None,None).repair(limit)
    assert store.called is False


@pytest.mark.parametrize('limit',['1001','-1','0'])
def test_legacy_cli_rejects_invalid_batch_before_app_creation(limit,monkeypatch):
    import notron_service.billing_repair as command
    def unexpected_app():pytest.fail('invalid batch created application')
    monkeypatch.setattr(command,'create_default_app',unexpected_app)
    with pytest.raises(SystemExit) as result:command.main(['--limit',limit])
    assert result.value.code==2


def test_legacy_cli_largest_batch_runs_actual_billing_and_deletion(billing,monkeypatch,capsys):
    import notron_service.billing_repair as command
    service,_,a,_=billing
    pay(billing);deliver(service,customer='cus_'+str(a.account_id))
    monkeypatch.setattr(command,'create_default_app',lambda:SimpleNamespace(state=SimpleNamespace(services=SimpleNamespace(billing=service))))
    assert command.main(['--limit','1000'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['processed']==1 and result['failed']==0 and result['deletion_completed']==0
