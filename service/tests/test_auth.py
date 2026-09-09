"""Synthetic signed JWTs; no issuer or provider network calls."""
import time
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from test_store import db, store_for
from test_config import env

@pytest.fixture
def tokens():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    def token(**changes):
        claims = dict(iss='https://issuer.test', aud='notron-api', sub='person', sid='login-1',
                      email='person@example.test', email_verified=True, scope='account:read infer',
                      iat=int(time.time()), exp=int(time.time())+300, nbf=int(time.time())-1)
        claims.update(changes)
        return jwt.encode(claims, key, algorithm='RS256', headers={'kid':'one','typ':'at+jwt'})
    return token, key


def verifier(tokens, store):
    from notron_service.auth import Authenticator
    return Authenticator('https://issuer.test', 'notron-api', 'native-client', store,
                         keys=SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=tokens[1].public_key())))


def test_verified_identity_and_session_are_server_owned(db, tokens):
    store = store_for(db); store.migrate()
    auth = verifier(tokens, store)
    first = auth.authenticate(tokens[0](account_id=str(uuid4()), device_id=str(uuid4())))
    assert auth.authenticate(tokens[0]()) == first
    assert auth.authenticate(tokens[0](sid='login-2')).device_id != first.device_id
    assert auth.authenticate(tokens[0](sub='other', sid='login-1')).account_id != first.account_id
    assert store.get_account(first)['contact_email'] == 'person@example.test'

@pytest.mark.parametrize('changes', [dict(aud='wrong'), dict(iss='https://wrong.test'), dict(exp=1),
    dict(sub=''), dict(sub=None), dict(email_verified=False), dict(email_verified='true'),
    dict(nbf=int(time.time())+1000), dict(sid=''), dict(iat=1,exp=int(time.time())+3600)])
def test_invalid_claims_never_create_account(db, tokens, changes):
    from notron_service.auth import AuthenticationError
    import psycopg
    store = store_for(db); store.migrate()
    with pytest.raises(AuthenticationError): verifier(tokens, store).authenticate(tokens[0](**changes))
    with psycopg.connect(db) as conn:
        assert conn.execute('SELECT count(*) FROM accounts').fetchone()[0] == 0


def test_id_token_and_algorithm_substitution_rejected(db, tokens):
    from notron_service.auth import AuthenticationError
    store = store_for(db); store.migrate(); auth = verifier(tokens, store)
    claims = jwt.decode(tokens[0](), options={'verify_signature':False})
    for token in [jwt.encode(claims,tokens[1],algorithm='RS256',headers={'typ':'JWT','kid':'one'}),
                  jwt.encode(claims,'x'*40,algorithm='HS256',headers={'typ':'at+jwt','kid':'one'})]:
        with pytest.raises(AuthenticationError): auth.authenticate(token)


def test_revocation_survives_refresh_and_cross_account_revoke_denied(db,tokens):
    from notron_service.auth import AuthenticationError
    from notron_service.store import AccessDenied
    store=store_for(db); store.migrate(); auth=verifier(tokens,store)
    a=auth.authenticate(tokens[0]()); b=auth.authenticate(tokens[0](sub='other'))
    with pytest.raises(AccessDenied): store.revoke_device(a,b.device_id)
    store.revoke_device(a,a.device_id)
    with pytest.raises(AuthenticationError): auth.authenticate(tokens[0](exp=int(time.time())+400))
    assert auth.authenticate(tokens[0](sub='other')) == b


def test_deletion_entry_point_blocks_all_account_devices(db,tokens):
    from notron_service.auth import AuthenticationError
    store=store_for(db); store.migrate(); auth=verifier(tokens,store)
    a=auth.authenticate(tokens[0]()); auth.authenticate(tokens[0](sid='second'))
    store.request_account_deletion(a)
    for sid in ('login-1','second','new-login'):
        with pytest.raises(AuthenticationError): auth.authenticate(tokens[0](sid=sid))


def test_nonce_verified_id_token_is_bound_to_access_identity(db,tokens):
    from notron_service.auth import AuthenticationError
    store=store_for(db);store.migrate();auth=verifier(tokens,store)
    claims=jwt.decode(tokens[0](),options={'verify_signature':False})
    claims.update(aud='native-client',nonce='random-nonce')
    id_token=jwt.encode(claims,tokens[1],algorithm='RS256',headers={'kid':'one','typ':'JWT'})
    assert auth.verify_login(tokens[0](),id_token)['nonce']=='random-nonce'
    with pytest.raises(AuthenticationError): auth.verify_login(tokens[0](sub='other'),id_token)


def test_authenticator_contract_exists(tokens):
    from notron_service import auth
    assert callable(auth.Authenticator)


def test_http_account_routes_and_wrong_audience_paid_gate(db,tokens,env):
    from fastapi import Depends
    from fastapi.testclient import TestClient
    from notron_service.app import Services, create_app
    from notron_service.auth import require_principal
    from notron_service.config import Settings
    store=store_for(db);store.migrate()
    app=create_app(Settings.from_env(env),Services(store=store,auth=verifier(tokens,store)))
    calls=[]
    @app.post('/v1/infer')
    def infer(principal=Depends(require_principal)):
        calls.append(principal); return {'ok':True}
    client=TestClient(app)
    assert client.post('/v1/infer',headers={'Authorization':'Bearer '+tokens[0](aud='wrong')}).status_code==401
    assert not calls
    headers={'Authorization':'Bearer '+tokens[0]()}
    me=client.get('/v1/me',headers=headers)
    assert me.status_code==200
    device=me.json()['device_id']
    assert client.post('/v1/devices/'+device+'/revoke',headers=headers).status_code==204
    assert client.post('/v1/infer',headers=headers).status_code==401
    assert not calls


def test_unknown_key_triggers_rotation_and_unknown_stays_rejected(tokens):
    from notron_service.auth import DiscoveryKeys, AuthenticationError
    from jwt.algorithms import RSAAlgorithm
    key2=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    docs=[{'keys':[dict(RSAAlgorithm.to_jwk(tokens[1].public_key(),as_dict=True),kid='one',use='sig')]},
          {'keys':[dict(RSAAlgorithm.to_jwk(key2.public_key(),as_dict=True),kid='two',use='sig')]}]
    class LocalKeys(DiscoveryKeys):
        def fetch_data(self):
            data=docs.pop(0) if len(docs)>1 else docs[0]
            self.jwk_set_cache.put(data)
            return data
    keys=LocalKeys('https://issuer.test/keys')
    keys.get_signing_key('one')
    assert keys.get_signing_key('two').key.public_numbers()==key2.public_key().public_numbers()
    with pytest.raises(jwt.PyJWKClientError):keys.get_signing_key('missing')


def test_task1_schema_upgrades_without_losing_identities(db):
    from notron_service.store import PostgresStore
    class OldStore(PostgresStore):
        @staticmethod
        def _migrations(): return PostgresStore._migrations()[:1]
    old=OldStore(db);old.migrate()
    from test_store import seed
    a,b=seed(db)
    store=store_for(db)
    assert not store.readiness()
    store.migrate()
    assert store.get_account(a)['id']==a.account_id
    assert store.readiness()


def test_concurrent_logins_share_account_and_device(db,tokens):
    from concurrent.futures import ThreadPoolExecutor
    store=store_for(db);store.migrate();auth=verifier(tokens,store)
    token=tokens[0]()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(auth.authenticate,[token]*4))
    assert len(set(results))==1


def test_corrupt_previous_schema_is_not_blessed_by_upgrade(db):
    import psycopg
    from notron_service.store import PostgresStore, SchemaMismatch
    class OldStore(PostgresStore):
        @staticmethod
        def _migrations():return PostgresStore._migrations()[:1]
    OldStore(db).migrate()
    with psycopg.connect(db) as conn:conn.execute('ALTER TABLE devices DROP COLUMN revoked_at')
    with pytest.raises(SchemaMismatch):store_for(db).migrate()


def test_discovery_rejects_unapproved_jwks_origin(monkeypatch):
    from notron_service import auth
    monkeypatch.setattr(auth,'_document',lambda _: {'issuer':'https://issuer.test','jwks_uri':'https://evil.test/keys'})
    with pytest.raises(auth.AuthenticationError):auth.discover_keys('https://issuer.test')
