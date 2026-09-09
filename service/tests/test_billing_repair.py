import importlib.util
import json
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
