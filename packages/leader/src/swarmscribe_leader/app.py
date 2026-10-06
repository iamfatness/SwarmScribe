import asyncio
import logging
from collections.abc import Awaitable
from contextlib import asynccontextmanager
from datetime import timedelta
from functools import partial

from fastapi import FastAPI

from .api import admin, files, follower, health
from .api import errors as api_errors
from .api.admin_auth import AdminAuth
from .api.body_limit import BodyLimit
from .background import run_exclusive, run_periodically
from .clock import utcnow
from .config import Settings
from .db.migrate import head_revision
from .db.session import make_engine, make_sessionmaker
from .ingest.scanner import scan_due_locations
from .jobs.reaper import reap
from .storage.links import LinkSigner
from .storage.registry import backend_for

logger = logging.getLogger(__name__)

# Stopping, after the last request has been answered (main.REQUEST_DRAIN_SECONDS bounds
# that). Nothing here has to finish for the data to be right: every reaper and scanner step
# is a set of transactions another step repeats, and a step's advisory lock ends with its
# connection. So each wait is a courtesy with a limit, and a database that does not answer
# (frozen, or dropped by the network without a reset) cannot hold a stop past their sum.
SHUTDOWN_GRACE_SECONDS = 5  # for the background step that is running to finish by itself
CANCEL_GRACE_SECONDS = 1  # then for a cancelled step to unwind (it gives its lock back)
DISPOSE_GRACE_SECONDS = 1  # then for the pool's connections to be closed politely


def _retrieve(task: asyncio.Future) -> None:
    if not task.cancelled():
        task.exception()  # marks it retrieved; whoever waited has already moved on


async def _finished_within(seconds: float, work: Awaitable[None]) -> bool:
    """Wait for `work` for at most `seconds`; False when it had not finished. Unlike
    asyncio.wait_for, never waits for the cancellation to be honoured: on a database that
    does not answer, the driver holds a cancelled query or a closing connection for as long
    as that database stays silent."""
    task = asyncio.ensure_future(work)
    done, _ = await asyncio.wait({task}, timeout=seconds)
    if done:
        task.result()
        return True
    task.cancel()
    task.add_done_callback(_retrieve)
    return False


def create_app(
    settings: Settings, *, background: bool = True, admin_auth: AdminAuth | None = None
) -> FastAPI:
    engine = make_engine(settings.database_url.get_secret_value())
    sessionmaker = make_sessionmaker(engine)
    signer = LinkSigner(settings.link_key.get_secret_value().encode("utf-8"))
    backend_factory = partial(backend_for, signer=signer, public_url=settings.public_url)

    async def reaper_step() -> None:
        async def work() -> None:
            await reap(
                sessionmaker,
                now=utcnow(),
                gone_after=timedelta(seconds=settings.follower_gone_after_seconds),
                started_at=app.state.started_at,
                startup_grace=timedelta(seconds=settings.lease_seconds),
            )

        await run_exclusive(engine, "reaper", work)

    async def scanner_step() -> None:
        async def work() -> None:
            await scan_due_locations(
                sessionmaker, backend_factory, now=utcnow(), max_attempts=settings.max_attempts
            )

        await run_exclusive(engine, "scanner", work)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        app.state.started_at = utcnow()
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
                pending: set[asyncio.Task] = set()
                if tasks:
                    _done, pending = await asyncio.wait(tasks, timeout=SHUTDOWN_GRACE_SECONDS)
                for task in pending:
                    task.cancel()
                    task.add_done_callback(_retrieve)
                if pending:
                    _done, pending = await asyncio.wait(pending, timeout=CANCEL_GRACE_SECONDS)
                if pending:
                    logger.warning(
                        "stopping: %d background task(s) did not stop in time and are "
                        "abandoned (is the database answering?)",
                        len(pending),
                    )
            finally:
                if not await _finished_within(DISPOSE_GRACE_SECONDS, engine.dispose()):
                    logger.warning(
                        "stopping: the database connections were not closed in time and "
                        "are abandoned (is the database answering?)"
                    )

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
    app.state.readiness = health.ReadinessProbe(engine)
    app.state.backend_factory = backend_factory
    app.state.admin_auth = admin_auth or AdminAuth.from_settings(settings)
    api_errors.install(app)
    app.add_middleware(BodyLimit)
    app.include_router(health.router)
    app.include_router(files.router)
    app.include_router(follower.router)
    app.include_router(admin.router)
    return app
