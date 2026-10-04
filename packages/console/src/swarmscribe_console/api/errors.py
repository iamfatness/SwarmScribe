"""Error answers: always `{code, message}` (the leader's ErrorBody), never request values.

A state-changing request refused after the person was identified is audited as
`request.refused` with the route template and the error code, unless the route audited the
refusal itself (request.state.audited)."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException
from swarmscribe_protocol import ErrorBody

from .. import audit
from ..errors import ConsoleError, Unauthenticated
from ..sessions import clear_session_cookie
from .deps import SAFE_METHODS

logger = logging.getLogger(__name__)

_HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}
OUTAGE_RETRY_AFTER = 10


def error_response(
    code: str, message: str, status: int, headers: dict[str, str] | None = None
) -> JSONResponse:
    body = ErrorBody(code=code, message=message).model_dump()
    return JSONResponse(body, status_code=status, headers=headers)


async def audit_refused(request: Request, code: str) -> None:
    person = getattr(request.state, "person", None)
    if (
        person is None
        or request.method in SAFE_METHODS
        or getattr(request.state, "audited", False)
    ):
        return
    route = request.scope.get("route")
    await audit.record_apart(
        request.app.state.sessionmaker,
        actor=person.actor,
        action="request.refused",
        target=f"{request.method} {getattr(route, 'path', '?')}",
        outcome=code,
    )


def install(app: FastAPI) -> None:
    # Nothing here logs the request URL: the sign-in callback's carries an authorization code.
    @app.exception_handler(ConsoleError)
    async def console_error(request: Request, exc: ConsoleError) -> JSONResponse:
        await audit_refused(request, exc.code)
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        response = error_response(exc.code, exc.message, exc.status, headers)
        if isinstance(exc, Unauthenticated) and exc.clear_cookie:
            clear_session_cookie(response)
        return response

    @app.exception_handler(DBAPIError)
    async def outage(_request: Request, exc: DBAPIError) -> JSONResponse:
        # Class names only: a DBAPIError's text carries the SQL and its parameters.
        logger.error(
            "database unavailable: %s (%s)", type(exc).__name__, type(exc.orig).__name__
        )
        return error_response(
            "unavailable",
            "service temporarily unavailable",
            503,
            {"Retry-After": str(OUTAGE_RETRY_AFTER)},
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, f"http_{exc.status_code}")
        return error_response(code, str(exc.detail), exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        await audit_refused(request, "invalid_request")
        summary = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        return error_response("invalid_request", summary[:500], 422)

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, _exc: Exception) -> JSONResponse:
        # Logged once, by the app's ContainErrors wrapper (type and route only).
        return error_response("internal", "internal error", 500)
