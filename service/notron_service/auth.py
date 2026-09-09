"""Managed OIDC access-token verification; no password or client-selected issuer.

Deployment profile: RS256 RFC 9068 `at+jwt`, <=15 minute access tokens,
verified email, stable issuer `sid` across refresh, native client ID distinct
from API audience. An issuer without this profile must stay disabled.
"""
from __future__ import annotations

import json
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

import jwt
from jwt import PyJWKClient

from .principals import Principal
from .store import AccessDenied


class AuthenticationError(PermissionError):
    pass


def _document(url):
    # Identity URLs are deployment configuration, never request inputs. Redirects
    # are refused to avoid moving discovery/JWKS to an unreviewed destination.
    from urllib.request import HTTPRedirectHandler, build_opener
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    with build_opener(NoRedirect).open(Request(url, headers={'Accept':'application/json'}), timeout=5) as response:
        raw = response.read(262145)
        if len(raw) > 262144:
            raise AuthenticationError('identity unavailable')
        return json.loads(raw)


class DiscoveryKeys(PyJWKClient):
    def fetch_data(self):
        data = _document(self.uri)
        if self.jwk_set_cache is not None:
            self.jwk_set_cache.put(data)
        return data


def discover_keys(issuer):
    doc = _document(issuer.rstrip('/') + '/.well-known/openid-configuration')
    uri = doc.get('jwks_uri', '')
    parts = urlsplit(uri)
    origin = urlsplit(issuer)
    if (doc.get('issuer') != issuer or parts.scheme != 'https' or
            parts.netloc != origin.netloc or parts.username or parts.password or parts.fragment or parts.query):
        raise AuthenticationError('identity unavailable')
    return DiscoveryKeys(uri, lifespan=300, timeout=5)


class Authenticator:
    def __init__(self, issuer, audience, client_id, store, *, keys=None):
        if not client_id or client_id == audience:
            raise ValueError('distinct native OIDC client ID required')
        self.issuer, self.audience, self.client_id, self.store = issuer, audience, client_id, store
        self.keys = keys  # Discovery is lazy: outages do not prevent health checks.

    def _decode(self, bearer, *, identity=False):
        try:
            if not isinstance(bearer,str) or len(bearer)>16384:
                raise AuthenticationError()
            header = jwt.get_unverified_header(bearer)
            if header.get('alg') != 'RS256' or header.get('typ') != ('JWT' if identity else 'at+jwt') or not header.get('kid'):
                raise AuthenticationError()
            if header.get('crit') or header.get('jku') or header.get('x5u'):
                raise AuthenticationError()
            if self.keys is None:
                self.keys = discover_keys(self.issuer)
            key = self.keys.get_signing_key_from_jwt(bearer).key
            claims = jwt.decode(bearer,key,algorithms=['RS256'],audience=self.client_id if identity else self.audience,
                                issuer=self.issuer,leeway=30,
                                options={'require':['iss','aud','sub','exp','iat','nbf'], 'strict_aud':True})
            if not isinstance(claims['sub'],str) or not claims['sub'].strip():
                raise AuthenticationError()
            if any(type(claims[name]) is not int for name in ('exp','iat','nbf')):
                raise AuthenticationError()
            if not 0 < claims['exp']-claims['iat'] <= 900:
                raise AuthenticationError()
            if claims.get('azp',self.client_id) != self.client_id:
                raise AuthenticationError()
            if identity:
                if not isinstance(claims.get('nonce'),str) or not claims['nonce']:
                    raise AuthenticationError()
            else:
                if (not isinstance(claims.get('sid'),str) or not 1 <= len(claims['sid']) <= 512
                        or claims.get('email_verified') is not True
                        or not isinstance(claims.get('email'),str) or not 1 <= len(claims['email']) <= 320):
                    raise AuthenticationError()
            return claims
        except Exception:
            raise AuthenticationError('invalid authentication') from None

    def authenticate(self, bearer: str) -> Principal:
        claims = self._decode(bearer)
        scopes = claims.get('scope','')
        if not isinstance(scopes,str):
            raise AuthenticationError('invalid authentication')
        try:
            return self.store.resolve_identity(self.issuer,claims['sub'],claims['sid'],claims['email'],
                                               frozenset(scopes.split()))
        except AccessDenied:
            raise AuthenticationError('invalid authentication') from None

    def verify_login(self, bearer, id_token):
        access = self._decode(bearer)
        identity = self._decode(id_token,identity=True)
        if identity['sub'] != access['sub']:
            raise AuthenticationError('invalid authentication')
        return {'nonce':identity['nonce']}

from fastapi import Request, HTTPException


def bearer_from_request(request: Request):
    values=request.headers.getlist('authorization')
    if len(values)!=1 or not values[0].startswith('Bearer ') or not values[0][7:] or ' ' in values[0][7:]:
        raise HTTPException(401,detail='authentication_required')
    return values[0][7:]


def require_principal(request: Request) -> Principal:
    auth=request.app.state.services.auth
    if auth is None:
        raise HTTPException(503,detail='identity_unavailable')
    try:
        return auth.authenticate(bearer_from_request(request))
    except AuthenticationError:
        raise HTTPException(401,detail='authentication_required') from None
