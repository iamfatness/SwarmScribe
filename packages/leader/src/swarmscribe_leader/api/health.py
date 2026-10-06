"""Liveness and readiness, for the container's HEALTHCHECK and Kubernetes probes.

/healthz says the process answers; it never touches the database. /readyz says this replica
can serve: the database answers and its schema is this leader's, or a newer one. A newer
schema means a rolling upgrade's migration has already run; this replica keeps serving until
it is replaced (`serve` itself still refuses to start on a database that is ahead). It does
not ask an identity provider: an identity provider's outage must not take a replica away
from the followers. An administrator's call during such an outage is answered 503 by the
admin API itself.

/readyz is anonymous, so its database use is bounded: at most one check runs at a time and
its answer is reused for READY_CACHE_SECONDS. Concurrent callers wait for that one check."""

import asyncio
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ..db.migrate import current_revision, is_known_revision

router = APIRouter()
logger = logging.getLogger(__name__)

READY_TIMEOUT_SECONDS = 3.0
READY_CACHE_SECONDS = 1.0
FAILURE_LOG_SECONDS = 30.0  # a failing check is logged at most this often, per cause

READY = ("ready", 200)
UNREACHABLE = ("database unreachable", 503)
NOT_CURRENT = ("database migrations are not current", 503)


async def _database_revision(engine: AsyncEngine) -> str | None:
    """The schema's revision; None when the database answers and has no schema yet. Raises
    when the database cannot be reached."""
    async with engine.connect() as conn:
        await conn.scalar(text("select 1"))
    return await current_revision(engine)


def _consume(task: asyncio.Future) -> None:
    if not task.cancelled():
        task.exception()  # marks it retrieved: the answer was already given


class ReadinessProbe:
    """One database check at a time; its result is reused for READY_CACHE_SECONDS."""

    def __init__(self, engine: AsyncEngine):
        self._engine = engine
        self._lock = asyncio.Lock()
        self._result: tuple[str, int] | None = None
        self._at = 0.0
        self._stuck: asyncio.Future | None = None
        self._logged: dict[str, float] = {}

    async def check(self, head_revision: str) -> tuple[str, int]:
        async with self._lock:
            now = time.monotonic()
            if self._result is None or now - self._at >= READY_CACHE_SECONDS:
                self._result = await self._run(head_revision)
                self._at = time.monotonic()
            return self._result

    def _log(self, cause: str, message: str) -> None:
        """One line per cause every FAILURE_LOG_SECONDS: a probe asks every few seconds."""
        now = time.monotonic()
        if now - self._logged.get(cause, float("-inf")) >= FAILURE_LOG_SECONDS:
            self._logged[cause] = now
            logger.warning(message)

    async def _run(self, head_revision: str) -> tuple[str, int]:
        # The query runs as its own task and is never awaited past the timeout: cancelling a
        # query on a frozen database makes the driver wait for that database (it can take
        # minutes), and the probe must answer 503 in READY_TIMEOUT_SECONDS regardless. At most
        # one such stuck task exists; while it does, the answer is "unreachable" at once.
        if self._stuck is not None and not self._stuck.done():
            return UNREACHABLE
        self._stuck = None
        task = asyncio.ensure_future(_database_revision(self._engine))
        try:
            done, _ = await asyncio.wait({task}, timeout=READY_TIMEOUT_SECONDS)
            if not done:
                task.cancel()
                task.add_done_callback(_consume)
                self._stuck = task
                raise TimeoutError
            revision = task.result()
        except Exception as exc:  # refused, unreachable, bad credentials, query failed, no answer
            # The response stays fixed; the log says which (the exception's type only: its
            # text can hold the database's address).
            self._log(
                "cannot-query",
                f"readiness check cannot query the database: {type(exc).__name__}",
            )
            return UNREACHABLE
        if revision == head_revision:
            return READY
        if revision is None or is_known_revision(revision):
            self._log(
                "not-current",
                f"readiness check: migrations are not current (database is at {revision})",
            )
            return NOT_CURRENT
        return READY  # the database is ahead: a newer leader has migrated it


def _answer(status: str, code: int = 200) -> JSONResponse:
    return JSONResponse({"status": status}, status_code=code, headers={"Cache-Control": "no-store"})


@router.get("/healthz")
async def healthz() -> JSONResponse:
    return _answer("ok")


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    status, code = await request.app.state.readiness.check(request.app.state.head_revision)
    return _answer(status, code)
