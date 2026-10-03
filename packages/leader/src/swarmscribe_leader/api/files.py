import contextlib
import os
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import anyio.to_thread
from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from ..db.models import StorageLocation
from ..errors import Forbidden, NotFound, PayloadTooLarge, PreconditionFailed
from ..storage.base import StorageError
from ..storage.links import InvalidLink, LinkClaims
from ..storage.local import LocalBackend, version_of
from .errors import error_response

router = APIRouter(prefix="/v1/files")

MAX_UPLOAD_BYTES = 512 * 1024 * 1024
CHUNK_BYTES = 256 * 1024


def _claims(request: Request, token: str, method: str) -> LinkClaims:
    try:
        claims = request.app.state.signer.verify(token, now=time.time())
    except InvalidLink as exc:
        raise Forbidden("invalid or expired link") from exc
    if claims.method != method:
        raise Forbidden("this link does not allow that method")
    return claims


async def _backend(request: Request, claims: LinkClaims) -> LocalBackend:
    try:
        location_id = uuid.UUID(claims.location_id)
    except ValueError as exc:
        raise NotFound("no such location") from exc
    async with request.app.state.sessionmaker() as session:
        location = await session.get(StorageLocation, location_id)
    if location is None or location.backend != "local":
        raise NotFound("no such location")
    return request.app.state.backend_factory(location)


def _open_current(path: Path, version: str):
    """Open the file once and check its version on that same handle, so nothing can
    change between the check and the bytes we send."""
    try:
        handle = path.open("rb")
    except OSError as exc:
        if not path.is_file():
            raise NotFound("file not found") from exc
        raise
    try:
        stat_result = os.fstat(handle.fileno())
        if version and version_of(stat_result) != version:
            raise PreconditionFailed("the file changed after this link was issued")
    except BaseException:
        handle.close()
        raise
    return handle, stat_result.st_size


async def _stream(handle) -> AsyncIterator[bytes]:
    try:
        while chunk := await anyio.to_thread.run_sync(handle.read, CHUNK_BYTES):
            yield chunk
    finally:
        handle.close()


@router.get("/{token}")
async def download(token: str, request: Request) -> StreamingResponse:
    claims = _claims(request, token, "GET")
    backend = await _backend(request, claims)
    path = backend.file_path(claims.key)
    handle, size = _open_current(path, claims.version)
    return StreamingResponse(
        _stream(handle),
        media_type="application/octet-stream",
        headers={"Content-Length": str(size)},
    )


@router.put("/{token}", status_code=201)
async def upload(token: str, request: Request) -> Response:
    claims = _claims(request, token, "PUT")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise PayloadTooLarge("upload is larger than 512 MiB")
    backend = await _backend(request, claims)
    path = backend.file_path(claims.key)
    if path.is_dir():
        raise StorageError(f"{claims.key!r} is a directory, not an object")
    temp = path.with_name(f".upload-{uuid.uuid4().hex}")
    size = 0
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temp.open("wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise PayloadTooLarge("upload is larger than 512 MiB")
                out.write(chunk)
        os.replace(temp, path)
    except (NotADirectoryError, FileExistsError) as exc:
        raise StorageError(f"invalid storage key {claims.key!r}") from exc
    except PermissionError:
        return error_response(
            "conflict", "the file is in use; retry shortly", 409, headers={"Retry-After": "5"}
        )
    finally:
        with contextlib.suppress(OSError):
            temp.unlink(missing_ok=True)
    return Response(status_code=201)
