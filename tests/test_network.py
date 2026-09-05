"""Task 4 boundaries: synthetic DNS, sockets, TLS and provider replies only."""
import io
import json
import socket
import ssl

import pytest

from notron import credentials, network
from notron.outbound import Passage
from notron.policy import PolicyError



@pytest.mark.parametrize('address', [
    '127.0.0.1', '127.12.1.2', '0.0.0.0', '10.0.0.2', '172.16.0.1',
    '192.168.1.1', '169.254.169.254', '169.254.170.2', '100.100.100.200',
    '100.64.0.1', '224.0.0.1', '240.0.0.1', '192.0.2.1', '198.18.0.1',
    '::', '::1', 'fc00::1', 'fd00::1', 'fe80::1', 'ff02::1',
    '::ffff:127.0.0.1', '::ffff:8.8.8.8', '64:ff9b::a00:1',
    '2002:0a00:0001::', '2001::1', 'fe80::1%en0', 'not-an-address',
])
def test_prohibited_addresses_and_mixed_dns_fail_closed(address):
    assert not network.public_https_url('https://example.org/x', [address])
    assert not network.public_https_url('https://example.org/x', ['93.184.216.34', address])


@pytest.mark.parametrize('url', [
    'http://example.org/x', 'file:///etc/passwd', 'https://user:pass@example.org/',
    'https://@example.org/', 'https://127.0.0.1/', 'https://[::ffff:8.8.8.8]/',
    'https://localhost/', 'https://metadata.google.internal/', 'https://example.org:bad/',
    'https://example.org\\@evil.test/', ' https://example.org/',
    'https://example.org/\nprivate', 'https://[bad/', 'https:///x',
])
def test_invalid_url_cannot_be_laundered_by_public_dns(url):
    assert not network.public_https_url(url, ['93.184.216.34'])


def test_public_url_requires_nonempty_all_public_results():
    assert not network.public_https_url('https://example.org/', [])
    assert network.public_https_url('https://example.org/x', ['93.184.216.34', '2606:4700:4700::1111'])
    assert network.public_https_url('https://8.8.8.8/', ['8.8.8.8'])


@pytest.mark.parametrize('url', [
    'http://api.tokenfactory.nebius.com/v1/',
    'https://api.tokenfactory.nebius.com.evil.test/v1/',
    'https://evil.test/api.tokenfactory.nebius.com/v1/',
    'https://user:synthetic@api.tokenfactory.nebius.com/v1/',
    'https://api.tokenfactory.nebius.com:444/v1/',
    'https://api.tokenfactory.nebius.com/v1/?destination=evil',
    'https://api.tokenfactory.nebius.com/other/',
    'https://api.tokenfactory.nebius.com/v1/#fragment',
    'https://api.tavily.com/v1/',
])
def test_provider_endpoint_rejects_unapproved_destination(url):
    with pytest.raises(PolicyError):
        network.validate_provider_endpoint(url, 'nebius')


@pytest.fixture
def wire(monkeypatch):
    """Keep real SDK, HTTP client, HTTP parsing and network policy above fake sockets."""
    from types import SimpleNamespace
    out = SimpleNamespace(dns=[], connects=[], tls=[], requests=[], timeouts=[], status=200,
                          location='https://attacker.example/steal', addresses=['93.184.216.34'])
    def resolve(host, port, *args, **kwargs):
        out.dns.append((host, port))
        return [(socket.AF_INET6 if ':' in address else socket.AF_INET, socket.SOCK_STREAM,
                 socket.IPPROTO_TCP, '', (address, port, 0, 0) if ':' in address else (address, port))
                for address in out.addresses]
    class Socket:
        def __init__(self, *a, **k): self.data = b''; self.peer = None
        def settimeout(self, value): out.timeouts.append(value)
        def setsockopt(self, *args): pass
        def connect(self, address): self.peer = address; out.connects.append(address)
        def getpeername(self): return self.peer
        def sendall(self, data): self.data += bytes(data)
        def close(self): pass
        def makefile(self, *args, **kw):
            out.requests.append(self.data)
            if b'/embeddings ' in self.data:
                body = {'data': [{'index': 0, 'embedding': [1., 0.]}]}
            elif b'/models ' in self.data:
                body = {'data': [{'id': 'synthetic-model'}]}
            elif b'/search ' in self.data:
                body = {'answer': 'synthetic result', 'results': []}
            else:
                body = {'choices': [{'message': {'content': 'synthetic answer'}}]}
            data = json.dumps(body).encode()
            return io.BytesIO(f'HTTP/1.1 {out.status} Test\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\nLocation: {out.location}\r\n\r\n'.encode() + data)
    def wrap(context, sock, *, server_hostname, **kwargs):
        out.tls.append((server_hostname, context.check_hostname, context.verify_mode))
        return sock
    monkeypatch.setattr(socket, 'getaddrinfo', resolve)
    monkeypatch.setattr(socket, 'socket', Socket)
    monkeypatch.setattr(ssl.SSLContext, 'wrap_socket', wrap)
    return out


def invoke(boundary):
    from notron.brain import Brain
    from notron import research
    passages = [Passage('synthetic question', 'user_request')]
    if boundary == 'search':
        credentials._provider.put(credentials.SEARCH_KEY, b'synthetic-search-key')
        return research.search(passages)
    brain = Brain.from_credentials()
    try:
        if boundary == 'inference':
            return brain.ask(system='static', user=passages, purpose='write')
        if boundary == 'embedding':
            return brain.embed(passages)
        return brain.available_models()
    finally:
        brain._client.close()


@pytest.mark.parametrize('boundary', ['inference', 'embedding', 'models', 'search'])
def test_every_real_transport_connects_only_to_validated_ip_with_original_tls_name(wire, boundary):
    result = invoke(boundary)
    assert result
    host = 'api.tavily.com' if boundary == 'search' else 'api.tokenfactory.nebius.com'
    assert wire.dns == [(host, 443)]
    assert wire.connects == [('93.184.216.34', 443)]
    assert wire.tls == [(host, True, ssl.CERT_REQUIRED)]
    assert len(wire.requests) == 1
    assert b'synthetic-' in wire.requests[0]


@pytest.mark.parametrize('boundary', ['inference', 'embedding', 'models', 'search'])
@pytest.mark.parametrize('addresses', [['127.0.0.1'], ['93.184.216.34', '10.0.0.2'], ['::ffff:8.8.8.8'], []])
def test_every_boundary_rejects_prohibited_dns_before_credentials_leave(wire, boundary, addresses):
    wire.addresses = addresses
    with pytest.raises(PolicyError):
        invoke(boundary)
    assert wire.connects == [] and wire.requests == []


@pytest.mark.parametrize('boundary', ['inference', 'embedding', 'models', 'search'])
@pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
def test_redirects_never_forward_authorization_or_search_body(wire, boundary, status):
    wire.status = status
    with pytest.raises(PolicyError):
        invoke(boundary)
    assert len(wire.requests) == len(wire.connects) == 1
    assert all(host != 'attacker.example' for host, _ in wire.dns)


def test_production_overrides_fail_before_transport(monkeypatch, wire):
    from notron.brain import Brain
    monkeypatch.setenv('NEBIUS_BASE_URL', 'https://development.example/v1/')
    with pytest.raises(PolicyError):
        Brain.from_credentials()
    assert not wire.requests and not wire.dns


def test_development_override_never_falls_back_to_production_credential(monkeypatch, wire):
    from notron.brain import Brain
    monkeypatch.setenv('NOTRON_DEVELOPMENT', '1')
    monkeypatch.setenv('NEBIUS_BASE_URL', 'https://development.example/v1/')
    with pytest.raises(credentials.CredentialUnavailable):
        Brain.from_credentials()
    assert not wire.requests and not wire.dns


def test_development_credential_is_isolated_and_rechecked(monkeypatch, wire):
    from notron.brain import Brain
    monkeypatch.setenv('NOTRON_DEVELOPMENT', '1')
    monkeypatch.setenv('NEBIUS_BASE_URL', 'https://development.example/v1/')
    credentials._provider.put(credentials.DEV_NEBIUS_KEY, b'synthetic-development-only')
    brain = Brain.from_credentials()
    try:
        assert brain.available_models() == ['synthetic-model']
        assert b'synthetic-development-only' in wire.requests[0]
        assert b'synthetic-inference-key' not in wire.requests[0]
        credentials._provider.delete(credentials.DEV_NEBIUS_KEY)
        with pytest.raises(credentials.CredentialUnavailable): brain.available_models()
        assert len(wire.requests) == 1
    finally:
        brain._client.close()


def test_sdk_base_mutation_cannot_redirect_production_key(wire):
    from notron.brain import Brain
    brain = Brain.from_credentials()
    try:
        brain._client.base_url = 'https://attacker.example/v1/'
        with pytest.raises(PolicyError): brain.available_models()
        assert not wire.requests and not wire.dns
    finally:
        brain._client.close()


def test_search_endpoint_mutation_is_blocked(monkeypatch, wire):
    from notron import research
    monkeypatch.setattr(research, 'ENDPOINT', 'https://attacker.example/search')
    with pytest.raises(PolicyError): invoke('search')
    assert not wire.requests and not wire.dns


@pytest.mark.parametrize('boundary', ['inference', 'embedding', 'models', 'search'])
def test_proxy_environment_cannot_receive_provider_credentials(monkeypatch, wire, boundary):
    monkeypatch.setenv('HTTPS_PROXY', 'http://synthetic:proxy-secret@attacker.example:8888')
    monkeypatch.setenv('ALL_PROXY', 'http://attacker.example:8888')
    invoke(boundary)
    assert all(host != 'attacker.example' for host, _ in wire.dns)
    assert len(wire.connects) == 1
    assert b'proxy-secret' not in wire.requests[0]


@pytest.mark.parametrize('boundary', ['inference', 'embedding', 'models', 'search'])
def test_public_ipv6_connection_is_pinned(wire, boundary):
    wire.addresses = ['2606:4700:4700::1111']
    invoke(boundary)
    assert wire.connects == [('2606:4700:4700::1111', 443, 0, 0)]


@pytest.mark.parametrize('location', [
    '/v1/models', 'https://api.tokenfactory.nebius.com/v1/models',
    'http://api.tokenfactory.nebius.com/v1/models', 'https://127.0.0.1/steal',
])
def test_even_same_host_relative_or_downgrade_redirects_are_not_followed(wire, location):
    wire.status = 307
    wire.location = location
    with pytest.raises(PolicyError): invoke('models')
    assert len(wire.requests) == len(wire.dns) == 1


@pytest.mark.parametrize('url', ['http://development.example/v1/', 'https://127.0.0.1/v1/',
                                'https://[::1]/v1/', 'https://u:p@development.example/v1/'])
def test_development_does_not_relax_url_safety(monkeypatch, wire, url):
    from notron.brain import Brain
    monkeypatch.setenv('NOTRON_DEVELOPMENT', '1')
    monkeypatch.setenv('NEBIUS_BASE_URL', url)
    with pytest.raises(PolicyError): Brain.from_credentials()
    assert not wire.dns and not wire.requests


def test_development_mode_revocation_blocks_existing_client(monkeypatch, wire):
    from notron.brain import Brain
    monkeypatch.setenv('NOTRON_DEVELOPMENT', '1')
    monkeypatch.setenv('NEBIUS_BASE_URL', 'https://development.example/v1/')
    credentials._provider.put(credentials.DEV_NEBIUS_KEY, b'synthetic-dev')
    brain = Brain.from_credentials()
    try:
        monkeypatch.delenv('NOTRON_DEVELOPMENT')
        with pytest.raises(PolicyError): brain.available_models()
        assert not wire.requests and not wire.dns
    finally: brain._client.close()


def test_constructor_key_is_not_a_development_credential_fallback(monkeypatch, wire):
    from notron.brain import Brain
    monkeypatch.setenv('NOTRON_DEVELOPMENT', '1')
    with pytest.raises(credentials.CredentialUnavailable):
        Brain(api_key='synthetic-production-key', base_url='https://development.example/v1/')
    assert not wire.requests


def test_actual_peer_must_match_validated_address(monkeypatch, wire):
    original = socket.socket
    class WrongPeer(original):
        def getpeername(self): return ('127.0.0.1', 443)
    monkeypatch.setattr(socket, 'socket', WrongPeer)
    with pytest.raises(PolicyError): invoke('models')
    assert not wire.requests and not wire.tls


def test_tls_failure_sends_no_credentials(monkeypatch, wire):
    def reject(*args, **kwargs): raise ssl.SSLCertVerificationError('synthetic certificate mismatch')
    monkeypatch.setattr(ssl.SSLContext, 'wrap_socket', reject)
    with pytest.raises(PolicyError): invoke('models')
    assert not wire.requests


def test_dns_rebinding_cannot_cause_a_second_hostname_lookup(monkeypatch, wire):
    original = socket.getaddrinfo
    def rebind(*args, **kwargs):
        if wire.dns:
            wire.addresses = ['127.0.0.1']
        return original(*args, **kwargs)
    monkeypatch.setattr(socket, 'getaddrinfo', rebind)
    invoke('models')
    assert len(wire.dns) == 1
    assert wire.connects == [('93.184.216.34', 443)]


def test_dns_failure_is_sanitized_and_never_falls_back(monkeypatch, wire):
    def failure(*args, **kwargs): raise socket.gaierror('synthetic-sensitive-host')
    monkeypatch.setattr(socket, 'getaddrinfo', failure)
    with pytest.raises(PolicyError) as error: invoke('models')
    assert 'synthetic-sensitive-host' not in str(error.value)
    assert not wire.connects


def test_transport_cannot_be_used_as_generic_fetch_or_host_override(wire):
    endpoint = network.provider_endpoint(network.NEBIUS_URL, 'nebius')
    with network.provider_client(endpoint) as client:
        for path, headers in [('/admin', {}), ('/v1/models', {'Host': 'attacker.example'})]:
            with pytest.raises(PolicyError):
                client.get('https://api.tokenfactory.nebius.com' + path, headers=headers)
    assert not wire.requests and not wire.dns


def test_sdk_ambient_headers_cannot_forward_production_secrets_to_development(monkeypatch, wire):
    from notron.brain import Brain
    monkeypatch.setenv('NOTRON_DEVELOPMENT', '1')
    monkeypatch.setenv('NEBIUS_BASE_URL', 'https://development.example/v1/')
    monkeypatch.setenv('OPENAI_CUSTOM_HEADERS', 'Authorization: Bearer synthetic-production-other\nProxy-Authorization: Basic synthetic-proxy\nX-Secret: synthetic-ambient-secret')
    monkeypatch.setenv('OPENAI_ORG_ID', 'synthetic-organization')
    monkeypatch.setenv('OPENAI_PROJECT_ID', 'synthetic-project')
    credentials._provider.put(credentials.DEV_NEBIUS_KEY, b'synthetic-development-only')
    brain = Brain.from_credentials()
    try: brain.available_models()
    finally: brain._client.close()
    request = wire.requests[0]
    assert b'synthetic-development-only' in request
    for secret in (b'synthetic-production-other', b'synthetic-proxy', b'synthetic-ambient-secret',
                   b'synthetic-organization', b'synthetic-project', b'synthetic-inference-key'):
        assert secret not in request


def test_inference_keeps_sdk_reasoning_timeout_budget(wire):
    invoke('inference')
    assert 600 in wire.timeouts


@pytest.mark.parametrize('failure', ['credential', 'storage', 'policy'])
def test_late_secure_boundary_failure_remains_fail_closed_through_sdk(monkeypatch, wire, failure):
    from notron.brain import Brain
    from notron import retention
    from notron.securestore import StorageError
    brain = Brain.from_credentials()
    calls = []
    if failure == 'credential':
        original = credentials.require
        def require(name):
            if name == credentials.NEBIUS_KEY:
                calls.append(name)
                if len(calls) == 2: raise credentials.CredentialUnavailable('synthetic-private-detail')
            return original(name)
        monkeypatch.setattr(credentials, 'require', require)
    else:
        original = retention.require_ready
        def ready():
            calls.append('ready')
            if len(calls) == 2:
                raise (StorageError if failure == 'storage' else PolicyError)('synthetic-private-detail')
            return original()
        monkeypatch.setattr(retention, 'require_ready', ready)
    try:
        with pytest.raises((PolicyError, StorageError, credentials.CredentialUnavailable)) as error:
            brain.available_models()
        assert 'synthetic-private-detail' not in str(error.value)
        assert not wire.requests and not wire.connects
    finally: brain._client.close()
