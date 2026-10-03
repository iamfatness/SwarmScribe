from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from swarmscribe_protocol import ErrorBody

from ..errors import LeaderError
from ..storage.base import StorageError


def _body(code: str, message: str) -> dict:
    return ErrorBody(code=code, message=message).model_dump()


def install(app: FastAPI) -> None:
    @app.exception_handler(LeaderError)
    async def leader_error(_request: Request, exc: LeaderError) -> JSONResponse:
        return JSONResponse(_body(exc.code, exc.message), status_code=exc.status)

    @app.exception_handler(StorageError)
    async def storage_error(_request: Request, exc: StorageError) -> JSONResponse:
        return JSONResponse(_body("invalid_key", str(exc)), status_code=400)
