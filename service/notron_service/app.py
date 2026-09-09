"""Small service foundation. Paid routes are added only with their auth controls."""
from dataclasses import dataclass
from typing import Protocol

from fastapi import FastAPI, Depends, Request, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID

from .auth import require_principal, bearer_from_request, AuthenticationError
from .store import AccessDenied
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse

from .config import Settings


class ReadyStore(Protocol):
    def readiness(self) -> bool: ...


@dataclass(frozen=True)
class Services:
    store: ReadyStore
    auth: object | None = None


class BoundedBody:
    """Bound actual bytes before dispatch, including chunked/no-length requests."""
    def __init__(self, app, limit):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = scope.get('headers', [])
        lengths = [v for k, v in headers if k.lower() == b'content-length']
        if lengths:
            try:
                size = int(lengths[0])
                valid = len(lengths) == 1 and size >= 0
            except ValueError:
                valid, size = False, 0
            if not valid:
                return await JSONResponse({'code':'invalid_request'}, status_code=400)(scope, receive, send)
            if size > self.limit:
                return await JSONResponse({'code':'request_too_large'}, status_code=413)(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            if message['type'] != 'http.request':
                continue
            chunk = message.get('body', b'')
            if len(body) + len(chunk) > self.limit:
                return await JSONResponse({'code':'request_too_large'}, status_code=413)(scope, receive, send)
            body.extend(chunk)
            if not message.get('more_body', False):
                break
        delivered = False
        async def replay():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {'type':'http.request', 'body':bytes(body), 'more_body':False}
        return await self.app(scope, replay, send)


class SafeErrors:
    """Do not propagate content-bearing exceptions to the ASGI server's logger."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        started = False
        async def observed_send(message):
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
            await send(message)
        try:
            return await self.app(scope, receive, observed_send)
        except Exception:
            if not started:
                await JSONResponse({'code':'service_unavailable'}, status_code=503)(scope, receive, send)
            # A partial response cannot be replaced safely. The server closes it
            # as incomplete, without seeing the exception's content or traceback.
            return


class LoginVerification(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id_token: str = Field(min_length=1,max_length=16384,repr=False)


def create_app(settings: Settings, services: Services) -> FastAPI:
    app = FastAPI(debug=False, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.services = services
    app.add_middleware(BoundedBody, limit=settings.max_body_bytes)
    app.add_middleware(SafeErrors)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({'code':'invalid_request'}, status_code=422)

    @app.get('/health/live')
    def live():
        return {'status':'alive'}

    @app.get('/health/ready')
    def ready():
        try:
            available = services.store.readiness() is True
        except Exception:
            available = False
        return JSONResponse({'status':'ready' if available else 'not_ready'}, status_code=200 if available else 503)

    @app.exception_handler(AccessDenied)
    async def access_denied(request, exc):
        return JSONResponse({'code':'access_denied'}, status_code=403)

    @app.get('/v1/me')
    def me(principal=Depends(require_principal)):
        account=services.store.get_account(principal)
        return {'account_id':str(principal.account_id),'device_id':str(principal.device_id),
                'status':account['status']}

    @app.get('/v1/devices')
    def devices(principal=Depends(require_principal)):
        return services.store.list_devices(principal)

    @app.post('/v1/devices/{device_id}/revoke',status_code=204)
    def revoke(device_id: UUID,principal=Depends(require_principal)):
        services.store.revoke_device(principal,device_id)

    @app.post('/v1/me/deletion',status_code=202)
    def deletion(principal=Depends(require_principal)):
        services.store.request_account_deletion(principal)
        return {'status':'deletion_requested'}

    @app.post('/v1/oidc/verify')
    def verify(body: LoginVerification, request: Request):
        if services.auth is None:
            raise HTTPException(503,detail='identity_unavailable')
        try:
            return services.auth.verify_login(bearer_from_request(request),body.id_token)
        except AuthenticationError:
            raise HTTPException(401,detail='authentication_required') from None

    return app


def create_default_app():
    from .store import PostgresStore
    settings = Settings.from_env()
    from .auth import Authenticator
    store = PostgresStore(settings.database_url)
    auth = Authenticator(settings.oidc_issuer,settings.oidc_audience,settings.oidc_client_id,store) if settings.oidc_client_id else None
    return create_app(settings, Services(store=store,auth=auth))
