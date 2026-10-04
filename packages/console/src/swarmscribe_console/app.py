import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send
from swarmscribe_leader.api.body_limit import BodyLimit
from swarmscribe_leader.db.session import make_engine, make_sessionmaker

from .api import errors as api_errors
from .api import session as session_api
from .api.guard import assert_guarded
from .api.security import SecurityHeaders
from .config import Settings
from .crypto import ConsoleKeys

logger = logging.getLogger(__name__)


class ContainErrors:
    """Logs an unhandled exception once, by type and route only (never its text, which can
    carry SQL parameters), and stops it there. Starlette's error middleware has already sent
    the 500 answer and re-raises, which would otherwise log it a second time in the server."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await self.app(scope, receive, send)
        except Exception as exc:
            route = getattr(scope.get("route"), "path", "?")
            logger.error("unhandled error: %s at %s", type(exc).__name__, route)


class _ConsoleApp(FastAPI):
    def build_middleware_stack(self) -> ASGIApp:
        # Outermost of all, outside Starlette's ServerErrorMiddleware too, so that the 500
        # answer for an unhandled exception and BodyLimit's 413 carry the security headers.
        return SecurityHeaders(ContainErrors(super().build_middleware_stack()))


def create_app(settings: Settings) -> FastAPI:
    engine = make_engine(settings.database_url.get_secret_value())
    sessionmaker = make_sessionmaker(engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            await engine.dispose()

    app = _ConsoleApp(
        title="SwarmScribe console",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker
    app.state.keys = ConsoleKeys(settings.key_bytes())
    api_errors.install(app)
    app.add_middleware(BodyLimit)
    app.include_router(session_api.router)
    assert_guarded(app.routes)  # a route added without the CSRF dependency fails the build
    return app
