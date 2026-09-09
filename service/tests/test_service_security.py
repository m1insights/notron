"""Operations execute against disposable PostgreSQL, without external traffic."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from test_store import db, store_for, seed
from test_auth import tokens
from test_config import env
from test_billing import billing
from test_usage_concurrency import usage


def test_account_window_is_atomic_shared_and_expires(db):
    from notron_service.operations import Operations
    s=store_for(db); s.migrate(); a,b=seed(db); ops=Operations(s)
    with ThreadPoolExecutor(8) as pool:
        results=list(pool.map(lambda _:ops.admit(a,limit=3),range(8)))
    assert results.count(True)==3
    assert ops.admit(b,limit=3)
    with s._connect() as c:c.execute("UPDATE account_rate_windows SET started_at=now()-interval '61 seconds'")
    assert ops.admit(a,limit=3)


def test_deleted_account_cost_requires_evidence_never_restores_access(usage):
    from notron_service.operations import Operations
    from notron_service.deletion import Deletion
    u,a,_=usage; r=u.reserve(a,str(uuid4()),'a'*64,40,lease_fence=1,rate_version='fixture')
    u.uncertain(a,r.id); Deletion(u.store,30).delete(a); ops=Operations(u.store)
    with pytest.raises(ValueError):ops.reconcile_cost(a.account_id,r.id,10,'')
    assert ops.status()['held_usage']==1
    ops.reconcile_cost(a.account_id,r.id,10,'private/provider/invoice/123')
    ops.reconcile_cost(a.account_id,r.id,10,'private/provider/invoice/123')
    with pytest.raises(ValueError):ops.reconcile_cost(a.account_id,r.id,11,'different')
    with u.store._connect() as c:
        assert c.execute('SELECT status FROM accounts WHERE id=%s',(a.account_id,)).fetchone()['status']=='deleted'
        assert c.execute('SELECT actual_micro_usd FROM usage_meter').fetchone()['actual_micro_usd']==10
        assert c.execute('SELECT count(*) AS n FROM usage_cache').fetchone()['n']==0
        assert 'private/provider' not in str(c.execute('SELECT * FROM operator_evidence').fetchall())
    assert ops.status()['held_usage']==0


def test_cache_key_rotation_purges_ciphertext_and_blocks_old_writers(usage):
    from notron_service.operations import Operations
    from notron_service.usage import UsageError
    u,a,_=usage; rid=str(uuid4());r=u.reserve(a,rid,'a'*64,40,lease_fence=1,rate_version='fixture')
    u.settle(a,r.id,10,{'content':'private'})
    ops=Operations(u.store); assert ops.rotate_cache_key(b'y'*32)==1
    with pytest.raises(UsageError):u.reserve(a,rid,'a'*64,40,lease_fence=1,rate_version='fixture')
    u.settle(a,r.id,10,{'content':'late private'})
    with u.store._connect() as c:assert c.execute('SELECT count(*) AS n FROM usage_cache').fetchone()['n']==0


def test_periodic_purges_expired_content_and_metadata(usage):
    from notron_service.operations import Operations
    u,a,_=usage;r=u.reserve(a,str(uuid4()),'a'*64,40,lease_fence=1,rate_version='fixture');u.settle(a,r.id,10,{'content':'private'})
    with u.store._connect() as c:c.execute("UPDATE usage_cache SET expires_at=now()-interval '1 second'")
    result=Operations(u.store).periodic(None,30,100)
    assert result['cache']==1
    assert result['held_usage']==0


def test_default_factory_routes_apply_shared_account_limit(db,env,monkeypatch):
    from fastapi.testclient import TestClient
    from notron_service import app as module
    from notron_service.config import Settings
    from notron_service.auth import Authenticator
    from notron_service.store import PostgresStore
    s=store_for(db);s.migrate();a,_=seed(db)
    settings=Settings.from_env(dict(env,NOTRON_SERVICE_OIDC_CLIENT_ID='native-client'))
    monkeypatch.setattr(Settings,'from_env',classmethod(lambda cls:settings))
    original_connect=PostgresStore._connect
    monkeypatch.setattr(PostgresStore,'_connect',lambda self:original_connect(s))
    monkeypatch.setattr(Authenticator,'authenticate',lambda self,bearer:a)
    client=TestClient(module.create_default_app())
    with s._connect() as c:c.execute('INSERT INTO account_rate_windows VALUES (%s,now(),120)',(a.account_id,))
    response=client.get('/v1/me',headers={'authorization':'Bearer synthetic'})
    assert response.status_code==429 and response.json()=={'code':'rate_limited'}
    assert response.headers['retry-after']=='60'


def test_network_guard_blocks_dns_and_tcp():
    import socket
    with pytest.raises(AssertionError,match='external network'):socket.getaddrinfo('example.com',443)
    with socket.socket() as client:
        with pytest.raises(AssertionError,match='external network'):client.connect(('127.0.0.1',443))
    a,b=socket.socketpair()
    try:a.sendall(b'ok');assert b.recv(2)==b'ok'
    finally:a.close();b.close()


def test_real_discovery_adapter_rotation_outage_and_revocation(db,tokens,monkeypatch):
    import json,jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from notron_service import auth
    from test_auth import verifier
    s=store_for(db);s.migrate()
    key2=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    keys=[dict(json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(tokens[1].public_key())),kid='one',use='sig',alg='RS256')]
    class Response:
        def __init__(self,data):self.raw=json.dumps(data).encode()
        def read(self,n):return self.raw[:n]
        def __enter__(self):return self
        def __exit__(self,*args):pass
    class Transport:
        fail=False
        def open(self,request,timeout):
            assert timeout==5
            if self.fail:raise TimeoutError('secret must not escape')
            if request.full_url.endswith('openid-configuration'):
                return Response({'issuer':'https://issuer.test','jwks_uri':'https://issuer.test/keys'})
            assert request.full_url=='https://issuer.test/keys'
            return Response({'keys':keys})
    transport=Transport()
    monkeypatch.setattr('urllib.request.build_opener',lambda *args:transport)
    verifier=auth.Authenticator('https://issuer.test','notron-api','native-client',s)
    a=verifier.authenticate(tokens[0]())
    claims=jwt.decode(tokens[0](),options={'verify_signature':False})
    rotated=jwt.encode(claims,key2,algorithm='RS256',headers={'kid':'two','typ':'at+jwt'})
    transport.fail=True
    with pytest.raises(auth.AuthenticationError):verifier.authenticate(rotated)
    transport.fail=False
    keys.append(dict(json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key2.public_key())),kid='two',use='sig',alg='RS256'))
    assert verifier.authenticate(rotated)==a
    s.revoke_device(a,a.device_id)
    with pytest.raises(auth.AuthenticationError):verifier.authenticate(rotated)


def test_cli_migration_status_and_fixed_failure_output(db,env,monkeypatch,capsys):
    from dataclasses import replace
    import json
    from notron_service.config import Settings
    from notron_service.operations import main
    from notron_service.store import PostgresStore
    settings=Settings.from_env(env)
    monkeypatch.setattr(Settings,'from_env',classmethod(lambda cls:settings))
    original=PostgresStore._connect
    s=store_for(db)
    monkeypatch.setattr(PostgresStore,'_connect',lambda self:original(s))
    assert main(['migrate'])==0
    assert json.loads(capsys.readouterr().out)=={'migrated':True}
    assert main(['periodic','--limit','1000'])==0
    assert json.loads(capsys.readouterr().out)['cache']==0
    assert main(['status'])==0
    capsys.readouterr()
    monkeypatch.setattr(PostgresStore,'_connect',lambda self:(_ for _ in ()).throw(RuntimeError('secret body')))
    assert main(['status'])==1
    assert json.loads(capsys.readouterr().out)=={'code':'operation_failed'}


def test_status_exposes_unprocessed_billing_and_stale_snapshots(billing):
    from notron_service.operations import Operations
    from test_billing import deliver
    service,_,a,_=billing
    deliver(service,customer='cus_'+str(a.account_id))
    state=Operations(service.store).status()
    assert state['billing_events_pending']==1
    assert state['billing_oldest_event_seconds']>=0


def test_native_libpq_network_is_also_isolated():
    import psycopg
    with pytest.raises(AssertionError,match='external database'):psycopg.connect('postgresql://user:secret@example.com/db')


def test_operator_records_evidenced_overrun_without_unreserved_charge_or_release(usage):
    from notron_service.operations import Operations
    u,a,_=usage;r=u.reserve(a,str(uuid4()),'a'*64,40,lease_fence=1,rate_version='fixture')
    Operations(u.store).reconcile_cost(a.account_id,r.id,70,'private/provider/overrun')
    with u.store._connect() as c:
        assert c.execute('SELECT actual_micro_usd FROM usage_meter').fetchone()['actual_micro_usd']==70
        assert c.execute('SELECT status FROM usage_reservations').fetchone()['status']=='uncertain'
        assert c.execute('SELECT charged_units FROM billing_allocations').fetchone()['charged_units'] is None
    assert u.billing.entitlement(a).allowance==60


def test_late_settlement_cannot_overwrite_operator_evidence(usage):
    from notron_service.operations import Operations
    from notron_service.usage import UsageError
    u,a,_=usage;r=u.reserve(a,str(uuid4()),'a'*64,40,lease_fence=1,rate_version='fixture')
    Operations(u.store).reconcile_cost(a.account_id,r.id,70,'private/provider/overrun')
    with pytest.raises(UsageError):u.settle(a,r.id,10,{'content':'late'})
    with u.store._connect() as c:assert c.execute('SELECT actual_micro_usd FROM usage_meter').fetchone()['actual_micro_usd']==70


def test_default_factory_signed_login_lease_provider_replay_and_deletion(db,env,tokens,monkeypatch):
    """Only socket HTTP and DB address are injected; all production services run."""
    import io,json
    import jwt
    from datetime import datetime,timedelta,timezone
    from fastapi.testclient import TestClient
    from notron_service.config import Settings
    from notron_service.store import PostgresStore
    from notron_service.app import create_default_app
    env.update({
        'NOTRON_SERVICE_OIDC_ISSUER':'https://issuer.test',
        'NOTRON_SERVICE_OIDC_CLIENT_ID':'native-client',
        'NOTRON_SERVICE_BILLING_PRICES':'{"price_plan":{"kind":"subscription","units":100000}}',
        'NOTRON_SERVICE_BILLING_RETURN_URLS':'["https://app.example.test/billing"]',
        'NOTRON_SERVICE_UNITS_PER_MICRO_USD':'1',
        'NOTRON_SERVICE_NEBIUS_KEY':'fixture-nebius','NOTRON_SERVICE_TAVILY_KEY':'fixture-tavily',
        'NOTRON_SERVICE_PROVIDER_RATES':json.dumps({'version':'fixture','vision_input_tokens':32768,'rates':{k:{'input':1,'output':1,'fixed':1} for k in ['infer:fast','infer:smart','infer:deep','embed:fast','vision:fast','search:fast']}})})
    settings=Settings.from_env(env);monkeypatch.setattr(Settings,'from_env',classmethod(lambda cls:settings))
    store=store_for(db);store.migrate();connect=PostgresStore._connect
    monkeypatch.setattr(PostgresStore,'_connect',lambda self:connect(store))
    calls=[]
    class HTTP:
        def open(self,request,timeout):
            url=request.full_url
            if url.endswith('openid-configuration'):
                value={'issuer':'https://issuer.test','jwks_uri':'https://issuer.test/keys'}
            elif url=='https://issuer.test/keys':
                value={'keys':[dict(json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(tokens[1].public_key())),kid='one',alg='RS256',use='sig')]}
            else:
                assert url=='https://api.tokenfactory.nebius.com/v1/chat/completions'
                calls.append(json.loads(request.data))
                value={'choices':[{'message':{'content':'answer'}}],'usage':{'prompt_tokens':1,'completion_tokens':2}}
            return io.BytesIO(json.dumps(value).encode())
    monkeypatch.setattr('urllib.request.build_opener',lambda *args:HTTP())
    app=create_default_app();client=TestClient(app);headers={'Authorization':'Bearer '+tokens[0]()}
    status=client.get('/v1/me',headers=headers);assert status.status_code==200
    account=status.json()['account_id']
    app.state.services.billing.grant_pilot(account,'fixture-approval',100000,datetime.now(timezone.utc)+timedelta(hours=1))
    lease=client.post('/v1/worker/lease/acquire',headers=headers,json={});assert lease.status_code==200
    body={'request_id':str(uuid4()),'lease_fence':lease.json()['fence'],'passages':[{'text':'password: hunter2','origin':'user_request'}],'max_tokens':4}
    for _ in range(2):
        response=client.post('/v1/infer',headers=headers,json=body)
        assert response.status_code==200 and response.json()['content']=='answer'
    assert len(calls)==1 and 'hunter2' not in str(calls)
    assert client.delete('/v1/account',headers=headers).status_code==200
    assert client.post('/v1/infer',headers=headers,json=body).status_code==401
    with store._connect() as c:assert c.execute('SELECT count(*) AS n FROM usage_cache').fetchone()['n']==0


def test_periodic_expires_abuse_metadata_and_deletion_erases_current_window(db):
    from notron_service.operations import Operations
    from notron_service.deletion import Deletion
    s=store_for(db);s.migrate();a,b=seed(db);ops=Operations(s)
    assert ops.admit(a) and ops.admit(b)
    with s._connect() as c:c.execute("UPDATE account_rate_windows SET started_at=now()-interval '61 seconds' WHERE account_id=%s",(a.account_id,))
    assert ops.periodic(None,30,100)['abuse_windows']==1
    Deletion(s,30).delete(b)
    with s._connect() as c:assert c.execute('SELECT count(*) AS n FROM account_rate_windows').fetchone()['n']==0


def test_deployment_runtime_role_cannot_rotate_keys_or_alter_schema(db):
    from pathlib import Path
    import psycopg
    from psycopg import sql
    s=store_for(db);s.migrate()
    runtime='runtime_'+uuid4().hex;operator='operator_'+uuid4().hex
    with s._connect() as c:
        schema=c.execute('SELECT current_schema() AS name').fetchone()['name']
        for role in (runtime,operator):c.execute(sql.SQL('CREATE ROLE {}').format(sql.Identifier(role)))
    try:
        template=(Path(__file__).parents[1]/'deploy/permissions.sql').read_text()
        template=template.replace(':"runtime_role"','"'+runtime+'"').replace(':"operations_role"','"'+operator+'"').replace('SCHEMA public','SCHEMA "'+schema+'"')
        with s._connect() as c:c.execute(template)
        for statement in ["INSERT INTO cache_key_generation VALUES(true,repeat('a',64))",'DELETE FROM operator_evidence','DELETE FROM schema_migrations','CREATE TABLE forbidden(id integer)']:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with s._connect() as c:
                    c.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(runtime)))
                    c.execute(statement)
        with s._connect() as c:
            c.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(operator)))
            c.execute("INSERT INTO cache_key_generation VALUES(true,repeat('a',64))")
    finally:
        with s._connect() as c:
            for role in (runtime,operator):
                c.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(role)))
                c.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


@pytest.mark.parametrize('limit',[1001,-1,0,True,1.5])
def test_periodic_rejects_invalid_batch_before_billing_or_database(limit):
    from notron_service.operations import Operations
    class Billing:
        called=False
        def repair(self,limit):
            self.called=True
            return {'failed':0}
    billing=Billing()
    with pytest.raises(ValueError,match='invalid_limit'):
        Operations(None).periodic(billing,30,limit)
    assert billing.called is False


@pytest.mark.parametrize('limit',['1001','-1'])
def test_periodic_cli_rejects_invalid_batch_before_configuration(limit,monkeypatch):
    from notron_service.config import Settings
    from notron_service.operations import main
    def unexpected_configuration(cls):
        pytest.fail('invalid batch reached service configuration')
    monkeypatch.setattr(Settings,'from_env',classmethod(unexpected_configuration))
    with pytest.raises(SystemExit) as result:main(['periodic','--limit',limit])
    assert result.value.code==2


def test_periodic_largest_supported_batch_completes_billing_and_cleanup(billing):
    from notron_service.operations import Operations
    service,_,_,_=billing
    result=Operations(service.store).periodic(service,30,1000)
    assert result['failed']==0 and result['cache']==0 and result['abuse_windows']==0
