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
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncEngine

from ..db.migrate import current_revision, is_known_revision

router = APIRouter()

READY_TIMEOUT_SECONDS = 3.0
READY_CACHE_SECONDS = 1.0
_NO_STORE = {"Cache-Control": "no-store"}

READY = ("ready", 200)
UNREACHABLE = ("database unreachable", 503)
NOT_CURRENT = ("database migrations are not current", 503)


class ReadinessProbe:
    """One database check at a time; its result is reused for READY_CACHE_SECONDS."""

    def __init__(self, engine: AsyncEngine):
        self._engine = engine
        self._lock = asyncio.Lock()
        self._result: tuple[str, int] | None = None
        self._at = 0.0

    async def check(self, head_revision: str) -> tuple[str, int]:
        async with self._lock:
            now = time.monotonic()
            if self._result is None or now - self._at >= READY_CACHE_SECONDS:
                self._result = await self._run(head_revision)
                self._at = time.monotonic()
            return self._result

    async def _run(self, head_revision: str) -> tuple[str, int]:
        try:
            revision = await asyncio.wait_for(
                current_revision(self._engine), READY_TIMEOUT_SECONDS
            )
        except Exception:  # refused, unreachable, bad credentials, or no answer in time
            return UNREACHABLE
        if revision == head_revision:
            return READY
        if revision is None or is_known_revision(revision):
            return NOT_CURRENT
        return READY  # the database is ahead: a newer console has migrated it


def _answer(status: str, code: int = 200) -> JSONResponse:
    return JSONResponse({"status": status}, status_code=code, headers=_NO_STORE)


@router.get("/healthz")
async def healthz() -> JSONResponse:
    return _answer("ok")


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    status, code = await request.app.state.readiness.check(request.app.state.head_revision)
    return _answer(status, code)
