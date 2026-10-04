from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.types import ASGIApp
from swarmscribe_leader.api.body_limit import BodyLimit
from swarmscribe_leader.db.session import make_engine, make_sessionmaker

from .api import errors as api_errors
from .api import session as session_api
from .api.security import SecurityHeaders
from .config import Settings
from .crypto import ConsoleKeys


class _ConsoleApp(FastAPI):
    def build_middleware_stack(self) -> ASGIApp:
        # Outermost of all, outside Starlette's ServerErrorMiddleware too, so that the 500
        # answer for an unhandled exception and BodyLimit's 413 carry the security headers.
        return SecurityHeaders(super().build_middleware_stack())


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
    return app
