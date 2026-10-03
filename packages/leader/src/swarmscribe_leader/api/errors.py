import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from swarmscribe_protocol import ErrorBody

from ..errors import LeaderError
from ..storage.base import StorageError, StorageUnavailable

logger = logging.getLogger(__name__)

_HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}


def error_response(
    code: str, message: str, status: int, headers: dict[str, str] | None = None
) -> JSONResponse:
    body = ErrorBody(code=code, message=message).model_dump()
    return JSONResponse(body, status_code=status, headers=headers)


def install(app: FastAPI) -> None:
    # Nothing here logs the request URL or path: signed-link tokens live in paths.
    @app.exception_handler(LeaderError)
    async def leader_error(_request: Request, exc: LeaderError) -> JSONResponse:
        return error_response(exc.code, exc.message, exc.status)

    @app.exception_handler(StorageError)
    async def storage_error(_request: Request, exc: StorageError) -> JSONResponse:
        logger.warning("storage request refused: %s", exc)
        if isinstance(exc, StorageUnavailable):
            return error_response("invalid_key", "storage location is not available", 400)
        return error_response("invalid_key", "invalid storage key", 400)

    @app.exception_handler(OSError)
    async def os_error(_request: Request, exc: OSError) -> JSONResponse:
        logger.error("storage failure: %s", exc, exc_info=exc)
        return error_response("storage_error", "storage failure", 500)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, f"http_{exc.status_code}")
        return error_response(code, str(exc.detail), exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        summary = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        return error_response("invalid_request", summary[:500], 422)

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled error: %r", exc, exc_info=exc)
        return error_response("internal", "internal error", 500)
