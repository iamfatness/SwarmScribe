"""Liveness and readiness, for the container's HEALTHCHECK and Kubernetes probes.

/healthz says the process answers; it never touches the database. /readyz says this replica
can serve: the database answers and its schema is this console's, or a newer one. A newer
schema means a rolling upgrade's migration has already run; this replica keeps serving until
it is replaced (`serve` itself still refuses to start on a database that is ahead). Neither
asks an identity provider or a leader: their outages must not take the console out of service.
Both are outside /api and /auth, need no session, and say nothing about the deployment beyond
a fixed status word.

/readyz is anonymous, so its database use is bounded: at most one check runs at a time and its
answer is reused for READY_CACHE_SECONDS. Concurrent callers wait for that one check."""

import asyncio
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncEngine

from ..db.migrate import current_revision, is_known_revision
from ..logsafe import log_limited

router = APIRouter()
logger = logging.getLogger(__name__)

READY_TIMEOUT_SECONDS = 3.0
READY_CACHE_SECONDS = 1.0
FAILURE_LOG_SECONDS = 30.0  # a failing check is logged at most this often
_NO_STORE = {"Cache-Control": "no-store"}

READY = ("ready", 200)
UNREACHABLE = ("database unreachable", 503)
NOT_CURRENT = ("database migrations are not current", 503)


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

    async def check(self, head_revision: str) -> tuple[str, int]:
        async with self._lock:
            now = time.monotonic()
            if self._result is None or now - self._at >= READY_CACHE_SECONDS:
                self._result = await self._run(head_revision)
                self._at = time.monotonic()
            return self._result

    async def _run(self, head_revision: str) -> tuple[str, int]:
        # The query runs as its own task and is never awaited past the timeout: cancelling a
        # query on a frozen database makes the driver wait for that database (it can take
        # minutes), and the probe must answer 503 in READY_TIMEOUT_SECONDS regardless. At most
        # one such stuck task exists; while it does, the answer is "unreachable" at once.
        if self._stuck is not None and not self._stuck.done():
            return UNREACHABLE
        self._stuck = None
        task = asyncio.ensure_future(current_revision(self._engine))
        try:
            done, _ = await asyncio.wait({task}, timeout=READY_TIMEOUT_SECONDS)
            if not done:
                task.cancel()
                task.add_done_callback(_consume)
                self._stuck = task
                raise TimeoutError
            revision = task.result()
        except Exception as exc:  # refused, unreachable, bad credentials, query failed, no answer
            # The response stays fixed; the log says which (the exception's type only).
            log_limited(
                logger, "readiness check cannot query the database", exc,
                key="readyz-cannot-query", every=FAILURE_LOG_SECONDS, frames=False,
            )
            return UNREACHABLE
        if revision == head_revision:
            return READY
        if revision is None or is_known_revision(revision):
            log_limited(
                logger, f"readiness check: migrations are not current (database is at {revision})",
                None, key="readyz-not-current", every=FAILURE_LOG_SECONDS,
            )
            return NOT_CURRENT
        return READY  # the database is ahead: a newer console has migrated it


def _answer(status: str, code: int = 200) -> JSONResponse:
    return JSONResponse({"status": status}, status_code=code, headers=_NO_STORE)


@router.api_route("/healthz", methods=["GET", "HEAD"])
async def healthz() -> JSONResponse:
    return _answer("ok")


@router.api_route("/readyz", methods=["GET", "HEAD"])
async def readyz(request: Request) -> JSONResponse:
    status, code = await request.app.state.readiness.check(request.app.state.head_revision)
    return _answer(status, code)


# A trailing slash redirects to the canonical path (GET and HEAD only, so a probe that
# follows redirects still gets the real answer); the web app's page is never returned.
@router.api_route("/healthz/", methods=["GET", "HEAD"], include_in_schema=False)
async def healthz_slash() -> RedirectResponse:
    return RedirectResponse("/healthz", status_code=308, headers=_NO_STORE)


@router.api_route("/readyz/", methods=["GET", "HEAD"], include_in_schema=False)
async def readyz_slash() -> RedirectResponse:
    return RedirectResponse("/readyz", status_code=308, headers=_NO_STORE)
