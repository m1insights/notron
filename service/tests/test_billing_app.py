from dataclasses import replace
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from test_config import env
from test_billing import billing, db, pay
from notron_service.config import Settings, ConfigError
from notron_service.app import create_app, Services
from notron_service.auth import require_principal


def test_billing_routes_require_auth_and_forbid_customer_override(billing,env):
    service,gateway,a,b=billing
    from types import SimpleNamespace
    app=create_app(Settings.from_env(env),Services(service.store,auth=SimpleNamespace(authenticate=lambda _:a),billing=service))
    client=TestClient(app)
    body={'price_id':'price_plan','return_url':'https://app.example.test/billing','request_id':str(uuid4())}
    assert client.post('/v1/billing/checkout',json=body).status_code==401
    app.dependency_overrides[require_principal]=lambda:a
    assert client.post('/v1/billing/checkout',json={**body,'customer_id':'cus_b'}).status_code==422
    assert client.post('/v1/billing/checkout',json=body).status_code==200
    assert client.post('/v1/billing/portal',json={'return_url':body['return_url']}).status_code==200
    pay(billing)
    data=client.get('/v1/me').json()
    assert data['entitlement']['allowance']==100
    assert data['subscription_status']==['active']
    assert 'customer_id' not in str(data)
    assert client.post('/v1/webhooks/stripe',content=b'{}',headers={'stripe-signature':'forged'}).status_code==400


def test_unconfigured_billing_fails_closed(env):
    class Store:
        def readiness(self): return True
        def get_account(self,p): return {'status':'active'}
    app=create_app(Settings.from_env(env),Services(Store()))
    from notron_service.principals import Principal
    app.dependency_overrides[require_principal]=lambda:Principal(uuid4(),uuid4(),frozenset({'account:read'}),'managed')
    client=TestClient(app)
    assert client.post('/v1/billing/checkout',json={'price_id':'price_plan','return_url':'https://example.test','request_id':str(uuid4())}).status_code==503
    assert client.get('/v1/me').json()['entitlement']['allowance']==0


def test_billing_configuration_optional_but_complete_and_strict(env):
    assert Settings.from_env(env).billing_policy is None
    env['NOTRON_SERVICE_BILLING_PRICES']='{"price_plan":{"kind":"subscription","units":100}}'
    with pytest.raises(ConfigError): Settings.from_env(env)
    env['NOTRON_SERVICE_BILLING_RETURN_URLS']='["https://app.example.test/billing"]'
    settings=Settings.from_env(env)
    assert settings.billing_policy.prices['price_plan'].units==100
    env['NOTRON_SERVICE_BILLING_PRICES']='{"price_plan":{"kind":"subscription","units":true}}'
    with pytest.raises(ConfigError): Settings.from_env(env)


def test_default_factory_constructs_real_billing_without_network(env,monkeypatch):
    import notron_service.app as module
    from notron_service.billing import Billing, StripeGateway
    env['NOTRON_SERVICE_BILLING_PRICES']='{"price_plan":{"kind":"subscription","units":100}}'
    env['NOTRON_SERVICE_BILLING_RETURN_URLS']='["https://app.example.test/billing"]'
    settings=Settings.from_env(env)
    monkeypatch.setattr(module.Settings,'from_env',lambda:settings)
    app=module.create_default_app()
    assert isinstance(app.state.services.billing,Billing)
    assert isinstance(app.state.services.billing.gateway,StripeGateway)
