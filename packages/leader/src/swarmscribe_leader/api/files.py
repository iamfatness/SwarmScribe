import os
import time
import uuid

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse

from ..db.models import StorageLocation
from ..errors import Forbidden, NotFound, PayloadTooLarge, PreconditionFailed
from ..storage.base import StorageError
from ..storage.links import InvalidLink, LinkClaims
from ..storage.local import LocalBackend

router = APIRouter(prefix="/v1/files")

MAX_UPLOAD_BYTES = 512 * 1024 * 1024


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


@router.get("/{token}")
async def download(token: str, request: Request) -> FileResponse:
    claims = _claims(request, token, "GET")
    backend = await _backend(request, claims)
    info = await backend.stat(claims.key)
    if info is None:
        raise NotFound("file not found")
    if claims.version and info.version != claims.version:
        raise PreconditionFailed("the file changed after this link was issued")
    return FileResponse(backend.path_for(claims.key), media_type="application/octet-stream")


@router.put("/{token}", status_code=201)
async def upload(token: str, request: Request) -> Response:
    claims = _claims(request, token, "PUT")
    backend = await _backend(request, claims)
    # stat() validates the key and that the storage root exists, so an upload can
    # never create a missing root directory.
    await backend.stat(claims.key)
    path = backend.path_for(claims.key)
    if path.is_dir():
        raise StorageError(f"{claims.key!r} is a directory, not an object")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.upload")
    size = 0
    try:
        with temp.open("wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise PayloadTooLarge("upload is larger than 512 MiB")
                out.write(chunk)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return Response(status_code=201)
