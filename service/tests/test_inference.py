import json
from uuid import uuid4
import pytest
from test_store import db
from test_billing import billing
from test_usage_concurrency import usage

@pytest.fixture
def engine(usage):
    from notron_service.inference import Inference, RateTable, PaidRequest
    from notron_service.providers import ProviderAdapter
    u,a,b=usage; calls=[]
    with u.store._connect() as c: c.execute('UPDATE entitlements SET allowance_units=100000')
    u.monthly_micro_usd=100000
    def http(url,payload,headers,timeout):
        calls.append((url,payload,headers))
        return {'choices':[{'message':{'content':'answer'}}],'usage':{'prompt_tokens':1,'completion_tokens':2}}
    rates=RateTable('fixture', {op:{'input':1,'output':1,'fixed':1} for op in ['infer:fast','infer:smart','infer:deep','embed:fast','vision:fast','search:fast']},vision_input_tokens=32768)
    provider=ProviderAdapter('test-nebius','test-search',http=http)
    e=Inference(u,rates,provider)
    body={'request_id':str(uuid4()),'lease_fence':1,'passages':[{'text':'password: hunter2','origin':'user_request'}],'max_tokens':4}
    return e,a,calls,body

def test_server_redacts_and_replays_once(engine):
    from notron_service.inference import PaidRequest
    e,a,calls,body=engine
    assert e.execute(a,'infer',PaidRequest(**body))['content']=='answer'
    assert e.execute(a,'infer',PaidRequest(**body))['content']=='answer'
    assert len(calls)==1
    assert 'hunter2' not in json.dumps(calls)
    assert calls[0][1]['model']=='nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B'

def test_schema_rejects_model_and_url_injection(engine):
    from notron_service.inference import PaidRequest
    from pydantic import ValidationError
    _,_,_,body=engine
    for change in [{'model':'evil'},{'url':'http://127.0.0.1'},{'tier':'unknown'},{'max_tokens':999999}]:
        with pytest.raises(ValidationError): PaidRequest(**(body|change))

def test_missing_usage_holds_reservation(engine):
    from notron_service.inference import PaidRequest
    from notron_service.usage import UsageError
    e,a,calls,body=engine
    def missing(*args): return {'choices':[{'message':{'content':'answer'}}]}
    e.provider.http=missing
    with pytest.raises(UsageError,match='outcome_uncertain'): e.execute(a,'infer',PaidRequest(**body))
    with pytest.raises(UsageError,match='outcome_uncertain'): e.execute(a,'infer',PaidRequest(**body))
    assert e.usage.billing.entitlement(a).allowance<100000

def test_rates_require_complete_positive_configuration():
    from notron_service.inference import RateTable
    with pytest.raises(ValueError): RateTable('',{})
    with pytest.raises(ValueError): RateTable('v1',{'infer:fast':{'input':0}})

from test_config import env

def test_paid_routes_and_missing_configuration(engine,env):
    from fastapi.testclient import TestClient
    from notron_service.app import create_app, Services
    from notron_service.config import Settings
    from notron_service.auth import require_principal
    e,a,calls,body=engine
    from types import SimpleNamespace
    app=create_app(Settings.from_env(env),Services(e.usage.store,auth=SimpleNamespace(authenticate=lambda _:a),billing=e.usage.billing,inference=e))
    client=TestClient(app)
    assert client.post('/v1/infer',json=body).status_code==401
    app.dependency_overrides[require_principal]=lambda:a
    assert client.post('/v1/infer',json=body).status_code==200
    with e.usage.store._connect() as c: c.execute('UPDATE entitlements SET allowance_units=0')
    body['request_id']=str(uuid4())
    response=client.post('/v1/infer',json=body)
    assert response.status_code==429 and response.json()['code']=='allowance_exhausted'
    assert len(calls)==1

def test_default_factory_complete_rates_required(env,monkeypatch):
    from notron_service.config import Settings,ConfigError
    import notron_service.app as module
    assert Settings.from_env(env).inference_rates is None
    env['NOTRON_SERVICE_PROVIDER_RATES']='{}'
    with pytest.raises(ConfigError): Settings.from_env(env)

@pytest.mark.parametrize('bad_usage',[None,{}, {'prompt_tokens':True,'completion_tokens':2}, {'prompt_tokens':1,'completion_tokens':99999}, {'prompt_tokens':1,'completion_tokens':2,'completion_tokens_details':{'reasoning_tokens':3}}])
def test_malformed_usage_never_refunds_or_retries(engine,bad_usage):
    from notron_service.inference import PaidRequest
    from notron_service.usage import UsageError
    e,a,calls,body=engine
    def http(*args):
        calls.append(args)
        return {'choices':[{'message':{'content':'answer'}}],'usage':bad_usage}
    e.provider.http=http
    for _ in range(2):
        with pytest.raises(UsageError,match='outcome_uncertain'): e.execute(a,'infer',PaidRequest(**body))
    assert len(calls)==1
    assert e.usage.billing.entitlement(a).allowance<100000

@pytest.mark.parametrize('operation,response,expected',[
    ('embed',{'data':[{'index':0,'embedding':[.1,.2]}],'usage':{'prompt_tokens':2}},'embeddings'),
    ('search',{'answer':'summary','results':[]},'answer'),
    ('vision',{'choices':[{'message':{'content':'picture'}}],'usage':{'prompt_tokens':10,'completion_tokens':2}},'content')])
def test_real_adapters_fixed_operations(engine,operation,response,expected):
    from notron_service.inference import PaidRequest
    import base64
    e,a,calls,body=engine
    if operation=='vision':
        body.update(image=base64.b64encode(b'\x89PNG\r\n\x1a\nfixture').decode(),mime='image/png',passages=[{'text':'describe','origin':'note','note_id':'source'}])
    def http(url,payload,headers,timeout):calls.append((url,payload));return response
    e.provider.http=http
    assert expected in e.execute(a,operation,PaidRequest(**body))
    assert len(calls)==1
    assert calls[0][0] in ('https://api.tokenfactory.nebius.com/v1/embeddings','https://api.tokenfactory.nebius.com/v1/chat/completions','https://api.tavily.com/search')

def test_provider_timeout_and_late_completion_do_not_duplicate(engine):
    from notron_service.inference import PaidRequest
    from notron_service.usage import UsageError
    import threading
    e,a,calls,body=engine;release=threading.Event()
    def http(*args):
        calls.append(args);release.wait(2)
        return {'choices':[{'message':{'content':'late'}}],'usage':{'prompt_tokens':1,'completion_tokens':2}}
    e.provider.http=http;e.provider.timeout=.01
    try:
        for _ in range(2):
            with pytest.raises(UsageError,match='outcome_uncertain'):e.execute(a,'infer',PaidRequest(**body))
        assert len(calls)==1
    finally:release.set();e.provider.pool.shutdown(wait=True)

def test_default_factory_wires_all_real_inference_dependencies(env,monkeypatch):
    from notron_service.config import Settings
    from notron_service.providers import ProviderAdapter
    from notron_service.inference import Inference
    from notron_service.usage import Usage
    import notron_service.app as module
    env.update({
        'NOTRON_SERVICE_BILLING_PRICES':'{"price_plan":{"kind":"subscription","units":100}}',
        'NOTRON_SERVICE_BILLING_RETURN_URLS':'["https://app.example.test/billing"]',
        'NOTRON_SERVICE_UNITS_PER_MICRO_USD':'10',
        'NOTRON_SERVICE_NEBIUS_KEY':'fixture-nebius','NOTRON_SERVICE_TAVILY_KEY':'fixture-tavily',
        'NOTRON_SERVICE_PROVIDER_RATES':json.dumps({'version':'fixture','vision_input_tokens':32768,'rates':{k:{'input':1,'output':1,'fixed':1} for k in ['infer:fast','infer:smart','infer:deep','embed:fast','vision:fast','search:fast']}})})
    settings=Settings.from_env(env)
    monkeypatch.setattr(module.Settings,'from_env',lambda:settings)
    app=module.create_default_app();engine=app.state.services.inference
    assert isinstance(engine,Inference) and isinstance(engine.provider,ProviderAdapter) and isinstance(engine.usage,Usage)
    assert engine.usage.units_per_micro_usd==10
    assert engine.usage.monthly_micro_usd==int(settings.monthly_spend_ceiling*1000000)

from test_auth import tokens,verifier

def test_expired_access_and_prototype_tokens_block_paid_routes(engine,env,tokens):
    from fastapi.testclient import TestClient
    from notron_service.config import Settings
    from notron_service.app import Services,create_app
    e,a,calls,body=engine
    app=create_app(Settings.from_env(env),Services(e.usage.store,auth=verifier(tokens,e.usage.store),billing=e.usage.billing,inference=e))
    client=TestClient(app)
    for token in (tokens[0](exp=1),'prototype-token'):
        assert client.post('/v1/infer',json=body,headers={'Authorization':'Bearer '+token}).status_code==401
    assert calls==[]

def test_expired_response_cache_never_reexecutes_provider(engine):
    from notron_service.inference import PaidRequest
    from notron_service.usage import UsageError
    e,a,calls,body=engine
    e.execute(a,'infer',PaidRequest(**body))
    with e.usage.store._connect() as c:c.execute("UPDATE usage_cache SET expires_at=now()-interval '1 day'")
    assert e.usage.purge_expired()==1
    with pytest.raises(UsageError,match='outcome_uncertain'):e.execute(a,'infer',PaidRequest(**body))
    assert len(calls)==1

def test_vision_maximum_input_budget_requires_operator_configuration():
    from notron_service.inference import RateTable
    rates={k:{'input':1,'output':1,'fixed':1} for k in ['infer:fast','infer:smart','infer:deep','embed:fast','vision:fast','search:fast']}
    with pytest.raises(ValueError):RateTable('fixture',rates)
