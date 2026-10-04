import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import timedelta

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
from swarmscribe_leader.background import run_periodically
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.session import make_engine, make_sessionmaker

from .api import admin as admin_api
from .api import auth as auth_api
from .api import errors as api_errors
from .api import session as session_api
from .api.guard import assert_guarded
from .api.security import SecurityHeaders
from .background import run_exclusive
from .config import Settings
from .crypto import ConsoleKeys
from .leader_client import LeaderClient
from .logsafe import contained, log_contained
from .oidc import web_providers
from .poller import PRUNE_LOCK, PollerConfig, poll_due_leaders, prune

logger = logging.getLogger(__name__)

SHUTDOWN_GRACE_SECONDS = 10
# Connections web requests may use at once, on top of the poller's (see _pool_size).
REQUEST_HEADROOM = 10


class ContainErrors:
    """Logs an unhandled exception once and stops it there: its type, the route and the
    traceback's frames (file, line, function, source line), never the exception's text, which
    can carry SQL parameters or request values. Starlette's error middleware has already sent
    the 500 answer and re-raises, which would otherwise log it a second time in the server.
    Only `http` requests are wrapped: a lifespan failure must reach the server."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        except Exception as exc:
            route = getattr(scope.get("route"), "path", "?")
            log_contained(logger, "unhandled error", exc, where=route)


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


def _pool_size(settings: Settings) -> int:
    # A poll holds its leader's advisory-lock connection for the whole call and takes a second
    # one to read and record, so a round at full concurrency needs twice the concurrency, plus
    # two for the prune (its lock connection and its own session). Requests borrow from the
    # overflow (REQUEST_HEADROOM). Documented in the package README.
    return 2 * settings.poll_concurrency + 2


def create_app(
    settings: Settings,
    *,
    background: bool = True,
    fetch: Fetch | None = None,
    idp_transport: httpx.AsyncBaseTransport | None = None,
    graph: GraphClient | None = None,
    google_groups: GoogleGroupsClient | None = None,
    leader_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """`fetch`, `idp_transport`, `graph` and `google_groups` replace the identity providers
    and directories in tests; real ones are built from the settings otherwise. `leader_transport`
    replaces the network that leader calls go over. `background=False` starts no poller or
    prune loop (tests drive them by hand)."""
    # hide_parameters: a failed statement, or sqlalchemy.engine logging turned on, never
    # prints the bound values (the sign-in nonce and PKCE verifier among them).
    engine = make_engine(
        settings.database_url.get_secret_value(),
        hide_parameters=True,
        pool_size=_pool_size(settings),
        max_overflow=REQUEST_HEADROOM,
    )
    sessionmaker = make_sessionmaker(engine)
    keys = ConsoleKeys(settings.key_bytes())
    leader_client = LeaderClient(transport=leader_transport)
    poller_config = PollerConfig.from_settings(settings)
    session_idle = timedelta(seconds=settings.session_idle_seconds)

    async def poll_step() -> None:
        await poll_due_leaders(
            engine, sessionmaker, leader_client, keys, now=utcnow(), config=poller_config
        )

    async def prune_step() -> None:
        async def work() -> None:
            await prune(
                sessionmaker,
                now=utcnow(),
                history=poller_config.history,
                session_idle=session_idle,
            )

        await run_exclusive(engine, PRUNE_LOCK, work)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        stop = asyncio.Event()
        tasks: list[asyncio.Task] = []
        if background:
            # contained(): a failing step logs its type and frames, never its text.
            tasks = [
                asyncio.create_task(
                    run_periodically(
                        stop,
                        settings.poll_tick_seconds,
                        contained(logger, "poller", poll_step),
                        "poller",
                    )
                ),
                asyncio.create_task(
                    run_periodically(
                        stop,
                        settings.prune_interval_seconds,
                        contained(logger, "prune", prune_step),
                        "prune",
                    )
                ),
            ]
        try:
            yield
        finally:
            stop.set()
            try:
                if tasks:
                    _done, pending = await asyncio.wait(tasks, timeout=SHUTDOWN_GRACE_SECONDS)
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
            finally:
                await leader_client.aclose()
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
    app.state.keys = keys
    app.state.leader_client = leader_client
    app.state.poller_config = poller_config
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
    app.include_router(admin_api.router)
    assert_guarded(app.routes)  # a route added without the CSRF dependency fails the build
    return app
