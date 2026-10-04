import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send
from swarmscribe_leader.api.body_limit import BodyLimit
from swarmscribe_leader.auth.oidc import Fetch, TokenVerifier, http_fetch
from swarmscribe_leader.auth.roles import (
    GoogleCloudIdentity,
    GoogleGroupsClient,
    GraphClient,
    MicrosoftGraph,
)
from swarmscribe_leader.db.session import make_engine, make_sessionmaker

from .api import auth as auth_api
from .api import errors as api_errors
from .api import session as session_api
from .api.guard import assert_guarded
from .api.security import SecurityHeaders
from .config import Settings
from .crypto import ConsoleKeys
from .oidc import web_providers

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


def _graph(settings: Settings) -> GraphClient | None:
    if settings.entra_client_id and settings.entra_tenant_id and settings.entra_client_secret:
        return MicrosoftGraph(
            settings.entra_tenant_id, settings.entra_client_id, settings.entra_client_secret
        )
    return None


def _google_groups(settings: Settings) -> GoogleGroupsClient | None:
    key = settings.google_service_account_key()
    return GoogleCloudIdentity(key) if key is not None else None


def create_app(
    settings: Settings,
    *,
    fetch: Fetch | None = None,
    idp_transport: httpx.AsyncBaseTransport | None = None,
    graph: GraphClient | None = None,
    google_groups: GoogleGroupsClient | None = None,
) -> FastAPI:
    """`fetch`, `idp_transport`, `graph` and `google_groups` replace the identity providers
    and directories in tests; real ones are built from the settings otherwise."""
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
    providers = web_providers(settings)
    app.state.web_providers = providers
    app.state.verifier = TokenVerifier(
        [provider.verification for provider in providers.values()], fetch=fetch or http_fetch
    )
    app.state.idp_transport = idp_transport
    app.state.graph = graph if graph is not None else _graph(settings)
    app.state.google_groups = (
        google_groups if google_groups is not None else _google_groups(settings)
    )
    api_errors.install(app)
    app.add_middleware(BodyLimit)
    app.include_router(auth_api.router)  # /auth/*: before a session exists, so no Person guard
    app.include_router(session_api.router)
    assert_guarded(app.routes)  # a route added without the CSRF dependency fails the build
    return app
