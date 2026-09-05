"""Fixed HTTPS provider routes; no generic URL fetch or citation resolver.

Resolve once, reject the entire set if any address is unsafe, then connect a
numeric sockaddr directly. TLS still authenticates the configured service name.
Neither proxies nor redirects can choose a second destination.
"""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar
import http.client
import ipaddress
import os
import re
import socket
import ssl
from urllib.parse import urlsplit

import httpx2
from openai import OpenAIError

from .policy import PolicyError

NEBIUS_URL = 'https://api.tokenfactory.nebius.com/v1/'
SEARCH_URL = 'https://api.tavily.com/search'
_SERVICES = {'nebius': NEBIUS_URL, 'tavily': SEARCH_URL}
_ROUTES = {'nebius': {('POST', '/v1/chat/completions'), ('POST', '/v1/embeddings'),
                      ('GET', '/v1/models')}, 'tavily': {('POST', '/search')}}


class NetworkPolicyError(PolicyError, OpenAIError):
    """Fail closed, including through SDK retries and graph fallback handlers.

    OpenAIError makes the pinned SDK propagate this policy failure unchanged.
    PolicyError remains the application's shared authority/error contract.
    """


class ProviderConnectivityError(PolicyError, OpenAIError):
    """A sanitized, retryable failure before a provider reply is known."""


class ProviderDeadlineError(ProviderConnectivityError):
    """The total budget for one logical provider operation was exhausted."""


class ProviderCooldownError(ProviderConnectivityError):
    """A recent provider outage is still inside its persisted backoff window."""


class ProviderRejectedError(OpenAIError):
    """A fixed provider rejection, such as bad credentials or an unknown model."""

    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f'Provider rejected the request (HTTP {status_code}).')


class ProviderStateError(PolicyError, OpenAIError):
    """Retry metadata is missing its integrity guarantees; calls must pause."""


_DEADLINE: ContextVar[float | None] = ContextVar('provider_deadline', default=None)


@contextmanager
def deadline(value: float):
    token = _DEADLINE.set(value)
    try:
        yield
    finally:
        _DEADLINE.reset(token)


def remaining(fallback: float | None = None) -> float:
    value = _DEADLINE.get()
    if value is None:
        if fallback is None:
            raise ProviderDeadlineError('Provider deadline is unavailable.')
        return fallback
    import time
    left = value - time.monotonic()
    if left <= 0:
        raise ProviderDeadlineError('Provider did not answer before its deadline.')
    return left


def _public_address(value: str) -> bool:
    try:
        if '%' in value:
            return False
        ip = ipaddress.ip_address(value)
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            return False
        if isinstance(ip, ipaddress.IPv6Address):
            # Reject embedded IPv4 and translation/tunnel mechanisms as well as
            # local IPv6; classification must not hide a private inner address.
            if (ip.ipv4_mapped or ip.sixtofour or ip.teredo or ip.is_site_local
                    or ip in ipaddress.ip_network('64:ff9b::/96')
                    or ip in ipaddress.ip_network('64:ff9b:1::/48')):
                return False
        return True
    except (ValueError, TypeError):
        return False


def _https_parts(url: str):
    try:
        if (not isinstance(url, str) or not url or not url.isascii()
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url)
                or '\\' in url):
            return None
        parts = urlsplit(url)
        host = parts.hostname
        if (parts.scheme != 'https' or not host or parts.username is not None
                or parts.password is not None or parts.port == 0):
            return None
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if (not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+', host)
                    or any(label.startswith('-') or label.endswith('-') for label in host.split('.'))
                    or host.endswith(('.localhost', '.local', '.internal'))):
                return None
        else:
            if not _public_address(host):
                return None
        return parts
    except (ValueError, TypeError):
        return None


def public_https_url(url: str, addresses: list[str]) -> bool:
    """Pure validation, never DNS or HTTP. Empty/mixed results fail closed.

    This predicate alone does not authorize fetching. Only the connection below
    binds a validated result to an actual socket, and only for provider routes.
    """
    parts = _https_parts(url)
    if parts is None or not addresses or not all(_public_address(a) for a in addresses):
        return False
    try:
        literal = ipaddress.ip_address(parts.hostname)
    except ValueError:
        return True
    return all(ipaddress.ip_address(a) == literal for a in addresses)


def validate_provider_endpoint(url: str, service: str, *, development: bool = False) -> str:
    """Return a canonical approved base endpoint or a content-free policy error.

    Development supports a dedicated public HTTPS Nebius-compatible test host;
    it never relaxes address, TLS, port, route or redirect restrictions.
    """
    parts = _https_parts(url)
    expected = urlsplit(_SERVICES.get(service, ''))
    if (parts is None or not expected.hostname or parts.port not in (None, 443)
            or '?' in url or '#' in url
            or parts.path.rstrip('/') != expected.path.rstrip('/')
            or (parts.hostname != expected.hostname and not (development and service == 'nebius'))):
        raise NetworkPolicyError('Provider endpoint is not approved.')
    # Development hosts must be DNS names, never alternate IP literal settings.
    try:
        ipaddress.ip_address(parts.hostname)
    except ValueError:
        return f'https://{parts.hostname}{expected.path}'
    raise NetworkPolicyError('Provider endpoint is not approved.')


@dataclass(frozen=True)
class ProviderEndpoint:
    url: str
    service: str
    development: bool

    @property
    def credential_name(self) -> str:
        from . import credentials
        if self.development:
            return credentials.DEV_NEBIUS_KEY
        return credentials.NEBIUS_KEY if self.service == 'nebius' else credentials.SEARCH_KEY


def provider_endpoint(url: str, service: str) -> ProviderEndpoint:
    development = os.environ.get('NOTRON_DEVELOPMENT') == '1'
    canonical = validate_provider_endpoint(url, service, development=development)
    return ProviderEndpoint(canonical, service, canonical != _SERVICES[service])


def _connect_public(host: str, port: int, timeout: float):
    try:
        results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except OSError:
        raise ProviderConnectivityError('Provider address resolution failed.') from None
    remaining(timeout)
    if not public_https_url(f'https://{host}/', [r[4][0] for r in results]):
        raise NetworkPolicyError('Provider resolved to a prohibited destination.')
    for family, kind, proto, _, sockaddr in results:
        sock = socket.socket(family, kind, proto)
        try:
            sock.settimeout(remaining(timeout))
            sock.connect(sockaddr)  # numeric address from the one validated DNS result
            if sock.getpeername()[0] != sockaddr[0]:
                raise NetworkPolicyError('Provider connection destination changed.')
            return sock
        except OSError:
            sock.close()
        except Exception:
            sock.close()
            raise
    raise ProviderConnectivityError('Provider connection unavailable.')


class _ProviderConnection(http.client.HTTPSConnection):
    def connect(self):
        raw = _connect_public(self.host, self.port, remaining(self.timeout))
        try:
            raw.settimeout(remaining(self.timeout))
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
            self.sock.settimeout(remaining(self.write_timeout))
        except ssl.SSLCertVerificationError:
            raw.close()
            raise NetworkPolicyError('Provider certificate validation failed.') from None
        except Exception:
            raw.close()
            raise


class ProviderTransport(httpx2.BaseTransport):
    """The only HTTP adapter for inference, embeddings, listing and search."""
    def __init__(self, endpoint: ProviderEndpoint):
        self.endpoint = endpoint

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        endpoint = self.endpoint
        # Recheck explicit development mode on every attempt, including SDK use.
        if endpoint.development and os.environ.get('NOTRON_DEVELOPMENT') != '1':
            raise NetworkPolicyError('Development endpoint mode is disabled.')
        base = urlsplit(validate_provider_endpoint(endpoint.url, endpoint.service,
                                                   development=endpoint.development))
        url = str(request.url)
        parts = _https_parts(url)
        if (parts is None or parts.hostname != base.hostname or parts.port not in (None, 443)
                or '?' in url or '#' in url
                or (request.method, parts.path) not in _ROUTES[endpoint.service]
                or request.headers.get('host', '').lower() not in (base.hostname, base.hostname + ':443')):
            raise NetworkPolicyError('Provider request destination is not approved.')
        # Buffered, non-streaming requests only. No urllib redirect handlers,
        # environment proxy mounts, hostname re-resolution or cookie replay.
        from . import credentials, retention
        from .securestore import StorageError
        try:
            retention.require_ready()
            secret = credentials.require(endpoint.credential_name).decode('utf-8')
        except (credentials.CredentialUnavailable, StorageError, PolicyError):
            # The SDK otherwise wraps these in APIConnectionError, which graph
            # availability fallbacks may swallow. Keep the shared policy type.
            raise NetworkPolicyError('Secure provider boundary unavailable; processing paused.') from None
        # The SDK imports ambient OPENAI_CUSTOM_HEADERS and retains cookies.
        # None of those may supply credentials or arbitrary headers on the wire.
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        if endpoint.service == 'nebius':
            headers['Authorization'] = f'Bearer {secret}'
        timeouts = request.extensions.get('timeout', {})
        conn = _ProviderConnection(base.hostname, 443, timeout=remaining(timeouts.get('connect', 20)),
                                   context=ssl.create_default_context())
        conn.write_timeout = remaining(timeouts.get('write', 20))
        try:
            conn.request(request.method, parts.path, body=request.read(), headers=headers)
            conn.sock.settimeout(remaining(timeouts.get('read', 20)))
            response = conn.getresponse()
            if 300 <= response.status < 400:
                raise NetworkPolicyError('Provider redirects are not allowed.')
            return httpx2.Response(response.status, headers=response.getheaders(), content=response.read())
        except (OSError, http.client.HTTPException):
            raise ProviderConnectivityError('Provider transport unavailable.') from None
        finally:
            conn.close()


def provider_client(endpoint: ProviderEndpoint) -> httpx2.Client:
    timeout = httpx2.Timeout(60, connect=5) if endpoint.service == 'nebius' else httpx2.Timeout(20)
    return httpx2.Client(transport=ProviderTransport(endpoint), trust_env=False,
                         follow_redirects=False, timeout=timeout)
