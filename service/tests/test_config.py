import base64
import secrets

import pytest
from fastapi.testclient import TestClient

from notron_service.config import ConfigError, Settings
from notron_service.app import Services, create_app


@pytest.fixture
def env():
    return {
        'NOTRON_SERVICE_ENVIRONMENT': 'production',
        'NOTRON_SERVICE_DATABASE_URL': 'postgresql://app:secret@db.internal/notron?sslmode=verify-full',
        'NOTRON_SERVICE_PUBLIC_URL': 'https://service.example.test',
        'NOTRON_SERVICE_OIDC_ISSUER': 'https://identity.example.test',
        'NOTRON_SERVICE_OIDC_AUDIENCE': 'notron-api',
        'NOTRON_SERVICE_CALLBACK_URL': 'https://service.example.test/auth/callback',
        'NOTRON_SERVICE_REGION': 'owner-approved-region',
        'NOTRON_SERVICE_MONTHLY_SPEND_CEILING': '50.00',
        'NOTRON_SERVICE_ENCRYPTION_KEY': base64.b64encode(secrets.token_bytes(32)).decode(),
        'NOTRON_SERVICE_SIGNING_KEY': base64.b64encode(secrets.token_bytes(32)).decode(),
        'NOTRON_SERVICE_STRIPE_SECRET_KEY': 'sk_test_synthetic_not_a_real_key',
        'NOTRON_SERVICE_STRIPE_WEBHOOK_SECRET': 'whsec_synthetic_not_a_real_secret',
    }


@pytest.mark.parametrize('field', ['DATABASE_URL', 'PUBLIC_URL', 'OIDC_ISSUER', 'OIDC_AUDIENCE',
                                  'CALLBACK_URL', 'REGION', 'MONTHLY_SPEND_CEILING',
                                  'ENCRYPTION_KEY', 'SIGNING_KEY', 'STRIPE_SECRET_KEY', 'STRIPE_WEBHOOK_SECRET'])
def test_production_rejects_missing_deployment_input(env, field):
    del env['NOTRON_SERVICE_' + field]
    with pytest.raises(ConfigError):
        Settings.from_env(env)


@pytest.mark.parametrize('field,value', [
    ('OIDC_ISSUER', 'http://identity.example.test'),
    ('PUBLIC_URL', 'http://service.example.test'),
    ('CALLBACK_URL', 'https://unapproved.example.test/callback'),
    ('OIDC_ISSUER', 'https://user:secret@identity.example.test'),
    ('OIDC_ISSUER', 'https://identity.example.test?redirect=https://evil.test'),
    ('NEBIUS_URL', 'https://evil.test/v1'),
    ('TAVILY_URL', 'http://api.tavily.com'),
    ('DEBUG', 'true'), ('DEBUG', 'sometimes'), ('PROTOTYPE_AUTH', 'true'),
    ('ENCRYPTION_KEY', 'not-base64-secret'), ('SIGNING_KEY', 'abcd'),
    ('DATABASE_URL', 'postgresql://app:secret@db.internal/notron?sslmode=disable'),
    ('DATABASE_URL', 'postgresql://app:secret@db.internal/notron?sslmode=verify-full&sslmode=disable'),
    ('MONTHLY_SPEND_CEILING', '-1'), ('MONTHLY_SPEND_CEILING', 'NaN'),
    ('MAX_BODY_BYTES', '0'), ('MAX_BODY_BYTES', '999999999'),
    ('STRIPE_SECRET_KEY', 'sk_live_not_approved'), ('STRIPE_MODE', 'live'),
])
def test_unsafe_production_configuration_is_rejected_without_echoing_values(env, field, value):
    env['NOTRON_SERVICE_' + field] = value
    with pytest.raises(ConfigError) as error:
        Settings.from_env(env)
    assert value not in str(error.value)


def test_keys_are_not_interchangeable_or_exposed_in_repr(env):
    settings = Settings.from_env(env)
    for field in ('DATABASE_URL','ENCRYPTION_KEY','SIGNING_KEY','STRIPE_SECRET_KEY','STRIPE_WEBHOOK_SECRET'):
        assert env['NOTRON_SERVICE_' + field] not in repr(settings)
    env['NOTRON_SERVICE_SIGNING_KEY'] = env['NOTRON_SERVICE_ENCRYPTION_KEY']
    with pytest.raises(ConfigError):
        Settings.from_env(env)


def test_loopback_fake_issuer_requires_explicit_test_environment(env):
    env['NOTRON_SERVICE_ENVIRONMENT'] = 'test'
    env['NOTRON_SERVICE_OIDC_ISSUER'] = 'http://127.0.0.1:9876'
    env['NOTRON_SERVICE_DATABASE_URL'] = 'postgresql://app@localhost/test'
    assert Settings.from_env(env).oidc_issuer == 'http://127.0.0.1:9876'
    env['NOTRON_SERVICE_OIDC_ISSUER'] = 'http://remote.example.test'
    with pytest.raises(ConfigError):
        Settings.from_env(env)


class Store:
    def __init__(self, ready):
        self.ready = ready
    def readiness(self):
        if isinstance(self.ready, Exception):
            raise self.ready
        return self.ready


@pytest.mark.parametrize('ready,status', [(True,200),(False,503),(RuntimeError('secret database password'),503)])
def test_readiness_is_injected_and_never_reveals_config(env, ready, status):
    client = TestClient(create_app(Settings.from_env(env), Services(store=Store(ready))))
    response = client.get('/health/ready')
    assert response.status_code == status
    assert response.json() == {'status': 'ready' if status == 200 else 'not_ready'}
    assert client.get('/health/live').json() == {'status': 'alive'}
    for path in ('/docs','/redoc','/openapi.json','/v1/infer'):
        assert client.get(path).status_code == 404
    assert client.get('/v1/me').status_code == 503


def test_body_size_limit_rejects_even_without_content_length(env):
    env['NOTRON_SERVICE_MAX_BODY_BYTES'] = '1024'
    app = create_app(Settings.from_env(env), Services(store=Store(True)))
    called = []
    @app.post('/echo')
    def echo(value: dict):
        called.append(True)
        return {'ok':True}
    client = TestClient(app)
    response = client.post('/echo', content=(chunk for chunk in [b'"', b'a'*1024, b'"']), headers={'content-type':'application/json'})
    assert response.status_code == 413 and called == []
    assert response.json() == {'code':'request_too_large'}


def test_validation_errors_do_not_echo_note_text(env, caplog):
    app = create_app(Settings.from_env(env), Services(store=Store(True)))
    @app.post('/validate')
    def validate(value: dict[str,int]):
        return value
    secret = 'personal note body never log me'
    response = TestClient(app).post('/validate', json={'count':secret})
    assert response.status_code == 422
    assert secret not in response.text + caplog.text


def test_unexpected_exception_does_not_escape_to_server_logs(env, caplog):
    app = create_app(Settings.from_env(env), Services(store=Store(True)))
    @app.get('/broken')
    def broken():
        raise RuntimeError('private note plus provider secret')
    response = TestClient(app).get('/broken')
    assert response.status_code == 503
    assert response.json() == {'code':'service_unavailable'}
    assert 'private note' not in response.text + caplog.text
