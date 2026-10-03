import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from functools import partial

from fastapi import FastAPI

from .api import errors as api_errors
from .api import files, follower, health
from .background import run_exclusive, run_periodically
from .clock import utcnow
from .config import Settings
from .db.migrate import head_revision
from .db.session import make_engine, make_sessionmaker
from .ingest.scanner import scan_due_locations
from .jobs.reaper import reap
from .storage.links import LinkSigner
from .storage.registry import backend_for

SHUTDOWN_GRACE_SECONDS = 10


def create_app(settings: Settings, *, background: bool = True) -> FastAPI:
    engine = make_engine(settings.database_url)
    sessionmaker = make_sessionmaker(engine)
    signer = LinkSigner(settings.link_key.encode("utf-8"))
    backend_factory = partial(backend_for, signer=signer, public_url=settings.public_url)

    async def reaper_step() -> None:
        async def work() -> None:
            async with sessionmaker() as session:
                await reap(
                    session,
                    now=utcnow(),
                    gone_after=timedelta(seconds=settings.follower_gone_after_seconds),
                )
                await session.commit()

        await run_exclusive(engine, "reaper", work)

    async def scanner_step() -> None:
        async def work() -> None:
            await scan_due_locations(
                sessionmaker, backend_factory, now=utcnow(), max_attempts=settings.max_attempts
            )

        await run_exclusive(engine, "scanner", work)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        stop = asyncio.Event()
        tasks = []
        if background:
            tasks = [
                asyncio.create_task(
                    run_periodically(stop, settings.reaper_interval_seconds, reaper_step, "reaper")
                ),
                asyncio.create_task(
                    run_periodically(
                        stop, settings.scanner_interval_seconds, scanner_step, "scanner"
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
                await engine.dispose()

    app = FastAPI(
        title="SwarmScribe leader",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker
    app.state.signer = signer
    app.state.head_revision = head_revision()
    app.state.backend_factory = backend_factory
    api_errors.install(app)
    app.include_router(health.router)
    app.include_router(files.router)
    app.include_router(follower.router)
    return app
