from datetime import datetime, timedelta, timezone
import importlib.util
import pytest


def test_entitlements_module_exists():
    assert importlib.util.find_spec('notron_service.entitlements') is not None


@pytest.mark.parametrize('status,paid,trial,revoked,expected', [
    ('incomplete',True,False,False,False), ('unpaid',True,False,False,False),
    ('active',True,False,False,True), ('past_due',True,False,False,True),
    ('active',False,False,False,False), ('trialing',False,True,False,True),
    ('canceled',True,False,False,True), ('active',True,False,True,False),
])
def test_access_requires_authoritative_paid_period(status,paid,trial,revoked,expected):
    from notron_service.entitlements import evaluate
    now=datetime.now(timezone.utc)
    value=evaluate(status, now+timedelta(days=1) if paid else now-timedelta(seconds=1),
                   now+timedelta(days=1) if trial else None, 100, now=now, revoked=revoked)
    assert value.allowed is expected
    assert value.allowance == (100 if expected else 0)
