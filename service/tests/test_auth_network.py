"""Identity configuration and header failures must never trust request URLs."""
import pytest
from fastapi.testclient import TestClient
from notron_service.app import Services, create_app
from notron_service.config import Settings
from test_config import env, Store


def test_missing_identity_config_keeps_account_routes_closed(env):
    client=TestClient(create_app(Settings.from_env(env),Services(store=Store(True))))
    assert client.get('/v1/me').status_code==503
    assert client.post('/v1/oidc/verify',json={'id_token':'synthetic'}).status_code==503


def test_nonce_endpoint_rejects_client_issuer_and_account_fields(env):
    client=TestClient(create_app(Settings.from_env(env),Services(store=Store(True))))
    response=client.post('/v1/oidc/verify',json={'id_token':'synthetic','issuer':'https://evil.test','account_id':'arbitrary'})
    assert response.status_code==422
    assert 'synthetic' not in response.text
