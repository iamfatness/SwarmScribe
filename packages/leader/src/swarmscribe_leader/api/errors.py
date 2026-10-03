import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException
from swarmscribe_protocol import ErrorBody

from ..errors import LeaderError, Unauthorized
from ..storage.base import StorageError, StorageUnavailable
from .admin_auth import audit_refused_change

logger = logging.getLogger(__name__)

_HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}
STORAGE_RETRY_AFTER = 30
OUTAGE_RETRY_AFTER = 10


def _describe_outage(exc: Exception) -> str:
    """Exception classes and OS error text only: a DBAPIError's string carries the SQL and
    its parameters, and an OSError's string can carry a server path."""
    if isinstance(exc, DBAPIError):
        return f"{type(exc).__name__} ({type(exc.orig).__name__})"
    if isinstance(exc, OSError):
        return f"{type(exc).__name__} (errno {exc.errno}: {exc.strerror})"
    return type(exc).__name__


def error_response(
    code: str, message: str, status: int, headers: dict[str, str] | None = None
) -> JSONResponse:
    body = ErrorBody(code=code, message=message).model_dump()
    return JSONResponse(body, status_code=status, headers=headers)


def install(app: FastAPI) -> None:
    # Nothing here logs the request URL or path: signed-link tokens live in paths.
    @app.exception_handler(LeaderError)
    async def leader_error(request: Request, exc: LeaderError) -> JSONResponse:
        if exc.status in (400, 404, 409, 422):
            await audit_refused_change(request, exc.code)
        headers: dict[str, str] = {}
        if exc.retry_after:
            headers["Retry-After"] = str(exc.retry_after)
        if isinstance(exc, Unauthorized):  # RFC 6750: say how to authenticate
            error = exc.bearer_error
            headers["WWW-Authenticate"] = f'Bearer error="{error}"' if error else "Bearer"
        return error_response(exc.code, exc.message, exc.status, headers=headers or None)

    @app.exception_handler(StorageError)
    async def storage_error(_request: Request, exc: StorageError) -> JSONResponse:
        if isinstance(exc, StorageUnavailable):
            logger.warning("storage location unavailable: %s", exc)
            return error_response(
                "unavailable",
                "storage location is not available",
                503,
                headers={"Retry-After": str(STORAGE_RETRY_AFTER)},
            )
        logger.warning("storage request refused: %s", exc)
        return error_response("invalid_key", "invalid storage key", 400)

    async def outage(_request: Request, exc: Exception) -> JSONResponse:
        # A database outage, or an OSError (connection refused, timeout) that no storage
        # boundary translated. Transient: the caller should retry, not give up.
        logger.error("service unavailable: %s", _describe_outage(exc))
        return error_response(
            "unavailable",
            "service temporarily unavailable",
            503,
            headers={"Retry-After": str(OUTAGE_RETRY_AFTER)},
        )

    app.add_exception_handler(DBAPIError, outage)
    app.add_exception_handler(OSError, outage)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, f"http_{exc.status_code}")
        return error_response(code, str(exc.detail), exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        await audit_refused_change(request, "invalid_request")
        summary = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        return error_response("invalid_request", summary[:500], 422)

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled error: %r", exc, exc_info=exc)
        return error_response("internal", "internal error", 500)
