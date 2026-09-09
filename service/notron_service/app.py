"""Small service foundation. Paid routes are added only with their auth controls."""
from dataclasses import dataclass
from typing import Protocol

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse

from .config import Settings


class ReadyStore(Protocol):
    def readiness(self) -> bool: ...


@dataclass(frozen=True)
class Services:
    store: ReadyStore


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


def create_app(settings: Settings, services: Services) -> FastAPI:
    app = FastAPI(debug=False, docs_url=None, redoc_url=None, openapi_url=None)
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

    return app


def create_default_app():
    from .store import PostgresStore
    settings = Settings.from_env()
    return create_app(settings, Services(store=PostgresStore(settings.database_url)))
