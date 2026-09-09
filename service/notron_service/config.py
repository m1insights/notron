"""Explicit deployment inputs. Errors identify fields, never their values."""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import ipaddress
import os
from urllib.parse import parse_qsl, urlsplit

NEBIUS_URL = 'https://api.tokenfactory.nebius.com/v1/'
TAVILY_URL = 'https://api.tavily.com'
MAX_BODY_BYTES = 1024 * 1024


class ConfigError(ValueError):
    pass


def _invalid(name):
    raise ConfigError(f'Invalid service setting: {name}.') from None


def _url(value, name, *, local=False):
    try:
        parts = urlsplit(value)
        port = parts.port
    except (ValueError, TypeError):
        _invalid(name)
    if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        _invalid(name)
    if any(ord(c) < 33 for c in value) or '\\' in value:
        _invalid(name)
    loopback = parts.hostname == 'localhost'
    try:
        loopback = loopback or ipaddress.ip_address(parts.hostname).is_loopback
    except ValueError:
        pass
    if parts.scheme != 'https' and not (local and loopback and parts.scheme == 'http'):
        _invalid(name)
    if loopback and not local:
        _invalid(name)
    return parts


def _key(value, name):
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError, binascii.Error):
        _invalid(name)
    if len(decoded) != 32:
        _invalid(name)
    return decoded


@dataclass(frozen=True)
class Settings:
    environment: str
    database_url: str = field(repr=False)
    public_url: str
    oidc_issuer: str
    oidc_audience: str
    callback_url: str
    region: str
    monthly_spend_ceiling: Decimal
    encryption_key: bytes = field(repr=False)
    signing_key: bytes = field(repr=False)
    stripe_secret_key: str = field(repr=False)
    stripe_webhook_secret: str = field(repr=False)
    provider_rates: str = field(default='',repr=False)
    units_per_micro_usd: int = 0
    nebius_key: str = field(default='',repr=False)
    tavily_key: str = field(default='',repr=False)
    billing_prices: str = field(default='', repr=False)
    financial_retention_days: int | None = None
    billing_return_urls: str = ''
    oidc_client_id: str = ''
    stripe_mode: str = 'test'
    nebius_url: str = NEBIUS_URL
    tavily_url: str = TAVILY_URL
    max_body_bytes: int = MAX_BODY_BYTES
    debug: bool = False
    prototype_auth: bool = False

    def __post_init__(self):
        if self.environment not in {'production', 'test', 'development'}:
            _invalid('ENVIRONMENT')
        local = self.environment != 'production'
        public = _url(self.public_url, 'PUBLIC_URL', local=local)
        _url(self.oidc_issuer, 'OIDC_ISSUER', local=local)
        callback = _url(self.callback_url, 'CALLBACK_URL', local=local)
        if (callback.scheme, callback.netloc) != (public.scheme, public.netloc):
            _invalid('CALLBACK_URL')
        if self.oidc_client_id and (self.oidc_client_id == self.oidc_audience or any(ord(c)<33 for c in self.oidc_client_id)):
            _invalid('OIDC_CLIENT_ID')
        if not self.oidc_audience.strip() or not self.region.strip():
            _invalid('IDENTITY_OR_REGION')
        if any(ord(c) < 33 for c in self.oidc_audience):
            _invalid('OIDC_AUDIENCE')
        if not isinstance(self.monthly_spend_ceiling, Decimal) or not self.monthly_spend_ceiling.is_finite() or self.monthly_spend_ceiling <= 0:
            _invalid('MONTHLY_SPEND_CEILING')
        if type(self.max_body_bytes) is not int or not 1 <= self.max_body_bytes <= MAX_BODY_BYTES:
            _invalid('MAX_BODY_BYTES')
        if type(self.debug) is not bool or type(self.prototype_auth) is not bool:
            _invalid('MODE_FLAGS')
        if self.debug or self.prototype_auth:
            # Commercial app never mounts prototype authentication, even in test mode.
            _invalid('MODE_FLAGS')
        for key in ('encryption_key', 'signing_key'):
            if not isinstance(getattr(self, key), bytes) or len(getattr(self, key)) != 32:
                _invalid(key.upper())
        if self.encryption_key == self.signing_key:
            _invalid('SIGNING_KEY')
        if self.nebius_url != NEBIUS_URL or self.tavily_url != TAVILY_URL:
            _invalid('PROVIDER_DESTINATION')
        if self.stripe_mode != 'test' or not self.stripe_secret_key.startswith('sk_test_') or not self.stripe_webhook_secret.startswith('whsec_'):
            _invalid('STRIPE_CONFIGURATION')
        self.inference_rates
        if self.financial_retention_days is not None and (type(self.financial_retention_days) is not int or self.financial_retention_days<=0):
            _invalid('FINANCIAL_RETENTION_DAYS')
        if self.billing_policy and self.financial_retention_days is None:
            _invalid('FINANCIAL_RETENTION_DAYS')
        self._validate_database(local)

    @property
    def inference_rates(self):
        if not any((self.provider_rates,self.units_per_micro_usd,self.nebius_key,self.tavily_key)):
            return None
        try:
            import json
            from .inference import RateTable
            if type(self.units_per_micro_usd) is not int or not 1<=self.units_per_micro_usd<=10**9 or not self.nebius_key or not self.tavily_key:
                raise ValueError()
            return RateTable(**json.loads(self.provider_rates))
        except (ValueError,TypeError,AttributeError):
            _invalid('PROVIDER_RATES')

    @property
    def billing_policy(self):
        if not self.billing_prices and not self.billing_return_urls:
            return None
        try:
            import json
            from .billing import BillingPolicy, Price
            prices=json.loads(self.billing_prices)
            urls=json.loads(self.billing_return_urls)
            if not isinstance(prices,dict) or not isinstance(urls,list):
                raise ValueError()
            return BillingPolicy({key:Price(**value) for key,value in prices.items()},tuple(urls),self.stripe_webhook_secret)
        except (ValueError,TypeError,AttributeError):
            _invalid('BILLING_POLICY')

    def _validate_database(self, local):
        try:
            value = urlsplit(self.database_url)
            query = parse_qsl(value.query, keep_blank_values=True, strict_parsing=True)
            pairs = dict(query)
            if value.scheme not in {'postgresql', 'postgres'} or not value.hostname or not value.username or not value.path.strip('/') or value.fragment:
                _invalid('DATABASE_URL')
            if len(query) != len(pairs) or set(pairs) - {'sslmode', 'sslrootcert', 'connect_timeout'}:
                _invalid('DATABASE_URL')
            if not local and (pairs.get('sslmode') != 'verify-full' or not value.password):
                _invalid('DATABASE_URL')
            if any(ord(c) < 33 for c in self.database_url):
                _invalid('DATABASE_URL')
            value.port
        except (ValueError, TypeError):
            _invalid('DATABASE_URL')

    @classmethod
    def from_env(cls, environ=None):
        env = os.environ if environ is None else environ
        def get(name, default=None):
            value = env.get('NOTRON_SERVICE_' + name, default)
            if not isinstance(value, str) or not value.strip():
                _invalid(name)
            return value
        def boolean(name):
            value = get(name, 'false')
            if value not in {'true', 'false'}:
                _invalid(name)
            return value == 'true'
        try:
            ceiling = Decimal(get('MONTHLY_SPEND_CEILING'))
        except InvalidOperation:
            _invalid('MONTHLY_SPEND_CEILING')
        try:
            limit = int(get('MAX_BODY_BYTES', str(MAX_BODY_BYTES)))
        except ValueError:
            _invalid('MAX_BODY_BYTES')
        try:
            conversion=int(env.get('NOTRON_SERVICE_UNITS_PER_MICRO_USD','0'))
        except ValueError:
            _invalid('UNITS_PER_MICRO_USD')
        try:
            retention=int(env['NOTRON_SERVICE_FINANCIAL_RETENTION_DAYS']) if 'NOTRON_SERVICE_FINANCIAL_RETENTION_DAYS' in env else None
        except ValueError:
            _invalid('FINANCIAL_RETENTION_DAYS')
        return cls(
            financial_retention_days=retention,
            provider_rates=env.get('NOTRON_SERVICE_PROVIDER_RATES',''),units_per_micro_usd=conversion,
            nebius_key=env.get('NOTRON_SERVICE_NEBIUS_KEY',''),tavily_key=env.get('NOTRON_SERVICE_TAVILY_KEY',''),
            environment=get('ENVIRONMENT', 'production'), database_url=get('DATABASE_URL'),
            public_url=get('PUBLIC_URL'), oidc_issuer=get('OIDC_ISSUER'), oidc_audience=get('OIDC_AUDIENCE'),
            oidc_client_id=env.get('NOTRON_SERVICE_OIDC_CLIENT_ID',''),
            billing_prices=env.get('NOTRON_SERVICE_BILLING_PRICES',''),
            billing_return_urls=env.get('NOTRON_SERVICE_BILLING_RETURN_URLS',''),
            callback_url=get('CALLBACK_URL'), region=get('REGION'), monthly_spend_ceiling=ceiling,
            encryption_key=_key(get('ENCRYPTION_KEY'), 'ENCRYPTION_KEY'),
            signing_key=_key(get('SIGNING_KEY'), 'SIGNING_KEY'),
            stripe_secret_key=get('STRIPE_SECRET_KEY'), stripe_webhook_secret=get('STRIPE_WEBHOOK_SECRET'),
            stripe_mode=get('STRIPE_MODE', 'test'), nebius_url=get('NEBIUS_URL', NEBIUS_URL),
            tavily_url=get('TAVILY_URL', TAVILY_URL), max_body_bytes=limit,
            debug=boolean('DEBUG'), prototype_auth=boolean('PROTOTYPE_AUTH'))
