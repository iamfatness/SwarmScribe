"""Error answers: always `{code, message}` (the leader's ErrorBody), never request values or
the names of fields the client sent.

A state-changing request refused after the person was identified is audited as
`request.refused` with the route template and the error code, unless the route audited the
refusal itself (request.state.audited)."""

import logging
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException
from swarmscribe_protocol import ErrorBody

from .. import audit
from ..errors import ConsoleError, Unauthenticated
from ..sessions import clear_session_cookie
from .deps import SAFE_METHODS, checked, signed_in

logger = logging.getLogger(__name__)

_HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}
OUTAGE_RETRY_AFTER = 10

# Validation answers are fixed text by error type. Pydantic's own messages can quote the
# input (a bad UUID names the offending character) and, for an unknown field, `loc` is the
# key the client sent, so neither is ever used.
_INVALID_TEXT = {
    "missing": "is required",
    "string_type": "must be text",
    "string_too_long": "is too long",
    "string_too_short": "is too short",
    "literal_error": "is not one of the allowed values",
    "uuid_parsing": "is not a valid id",
    "bool_parsing": "must be true or false",
    "bool_type": "must be true or false",
    "dict_type": "must be an object",
    "model_attributes_type": "must be an object",
}
INVALID_JSON_TEXT = "the request body is not valid JSON"
UNKNOWN_FIELD_TEXT = "the request has a field the console does not accept"
# A field path may name only the console's own fields: lowercase identifiers and list
# positions. Anything else (a key that came from the client) is dropped.
_OWN_FIELD = re.compile(r"[a-z][a-z0-9_]*")


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


def _field_path(loc: tuple) -> str:
    parts = [
        str(part)
        for part in loc
        if part not in ("body", "query", "path")
        and (isinstance(part, int) or _OWN_FIELD.fullmatch(str(part)))
    ]
    return ".".join(parts)


def invalid_summary(errors: list) -> str:
    """Fixed text for each error; a field path appears only when it is one of the console's
    own field names."""
    found = []
    for error in errors:
        kind = error.get("type", "")
        if kind == "extra_forbidden":
            found.append(UNKNOWN_FIELD_TEXT)
            continue
        text = _INVALID_TEXT.get(kind, "is not valid")
        path = _field_path(tuple(error.get("loc", ())))
        found.append(f"{path} {text}" if path else f"the request {text}")
    return "; ".join(dict.fromkeys(found))[:500]


async def _console_response(request: Request, exc: ConsoleError) -> JSONResponse:
    await audit_refused(request, exc.code)
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    response = error_response(exc.code, exc.message, exc.status, headers)
    if isinstance(exc, Unauthenticated) and exc.clear_cookie:
        clear_session_cookie(response)
    return response


def install(app: FastAPI) -> None:
    # Nothing here logs the request URL: the sign-in callback's carries an authorization code.
    @app.exception_handler(ConsoleError)
    async def console_error(request: Request, exc: ConsoleError) -> JSONResponse:
        return await _console_response(request, exc)

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
        errors = exc.errors()
        if any(error.get("type") == "json_invalid" for error in errors):
            # FastAPI parses the body before it runs the route's dependencies, so a malformed
            # body would be refused with 422 ahead of the session and CSRF checks. Run them
            # first: a caller who may not make this change is told so (401 or 403), and a
            # refusal for a signed-in person is audited.
            route = request.scope.get("route")
            path = str(getattr(route, "path", ""))
            if request.method not in SAFE_METHODS and path.startswith("/api"):
                try:
                    await checked(request, await signed_in(request))
                except ConsoleError as refused:
                    return await _console_response(request, refused)
            await audit_refused(request, "invalid_request")
            return error_response("invalid_request", INVALID_JSON_TEXT, 422)
        await audit_refused(request, "invalid_request")
        return error_response("invalid_request", invalid_summary(errors), 422)

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, _exc: Exception) -> JSONResponse:
        # Logged once, by the app's ContainErrors wrapper (type and route only).
        return error_response("internal", "internal error", 500)
