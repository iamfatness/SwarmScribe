import contextlib
import os
import time
import uuid
from collections.abc import AsyncIterator, Callable
from functools import partial
from pathlib import Path
from typing import Any

import anyio.to_thread
from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from ..db.models import Job, StorageLocation
from ..errors import Forbidden, NotFound, PayloadTooLarge, PreconditionFailed, StaleLease
from ..storage.base import StorageError, StorageUnavailable
from ..storage.links import InvalidLink, LinkClaims
from ..storage.local import LocalBackend, version_of
from .errors import error_response

router = APIRouter(prefix="/v1/files")

MAX_UPLOAD_BYTES = 512 * 1024 * 1024
CHUNK_BYTES = 256 * 1024


async def _in_thread(function: Callable[..., Any], *args: Any) -> Any:
    """Filesystem calls can block for seconds on a slow or network disk: never on the loop."""
    return await anyio.to_thread.run_sync(function, *args)


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
        if not _is_file(path):
            raise NotFound("file not found") from exc
        raise StorageUnavailable("the file cannot be opened") from exc
    try:
        stat_result = os.fstat(handle.fileno())
        if version and version_of(stat_result) != version:
            raise PreconditionFailed("the file changed after this link was issued")
    except OSError as exc:
        handle.close()
        raise StorageUnavailable("the file cannot be read") from exc
    except BaseException:
        handle.close()
        raise
    return handle, stat_result.st_size


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return True  # it may well be there; we cannot tell, so do not answer 404


async def _stream(handle) -> AsyncIterator[bytes]:
    try:
        while chunk := await _in_thread(handle.read, CHUNK_BYTES):
            yield chunk
    finally:
        handle.close()


@router.get("/{token}")
async def download(token: str, request: Request) -> StreamingResponse:
    claims = _claims(request, token, "GET")
    backend = await _backend(request, claims)
    path = await _in_thread(backend.file_path, claims.key)
    handle, size = await _in_thread(_open_current, path, claims.version)
    return StreamingResponse(
        _stream(handle),
        media_type="application/octet-stream",
        headers={"Content-Length": str(size)},
    )


def _lease_ids(claims: LinkClaims) -> tuple[uuid.UUID, uuid.UUID]:
    try:
        return uuid.UUID(claims.job_id), uuid.UUID(claims.lease_id)
    except ValueError as exc:
        raise StaleLease("this upload link's lease is no longer current") from exc


def _lease_is_current(job: Job | None, lease_id: uuid.UUID) -> bool:
    return job is not None and job.state == "leased" and job.lease_id == lease_id


async def _require_current_lease(request: Request, claims: LinkClaims) -> None:
    """The upload belongs to a lease; once that lease has ended (expired and re-leased,
    cancelled, completed), its links must not write anything. A cheap check, made before
    the body is read; `_replace_if_lease_current` makes the one that counts."""
    job_id, lease_id = _lease_ids(claims)
    async with request.app.state.sessionmaker() as session:
        job = await session.get(Job, job_id)
    if not _lease_is_current(job, lease_id):
        raise StaleLease("this upload link's lease is no longer current")


async def _replace_if_lease_current(
    request: Request, claims: LinkClaims, temp: Path, path: Path
) -> None:
    """Check the lease and replace the target while holding a shared lock on the job row.
    Submit takes the row FOR UPDATE, so it waits for this replace, or this check sees the
    job completed: an output can never change after submit has verified it."""
    job_id, lease_id = _lease_ids(claims)
    async with request.app.state.sessionmaker() as session, session.begin():
        job = await session.get(
            Job, job_id, with_for_update={"read": True}, populate_existing=True
        )
        if not _lease_is_current(job, lease_id):
            raise StaleLease("this upload link's lease is no longer current")
        await _in_thread(os.replace, temp, path)


@router.put("/{token}", status_code=201)
async def upload(token: str, request: Request) -> Response:
    claims = _claims(request, token, "PUT")
    if not claims.job_id or not claims.lease_id:
        raise Forbidden("this upload link is not bound to a lease")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise PayloadTooLarge("upload is larger than 512 MiB")
    await _require_current_lease(request, claims)
    backend = await _backend(request, claims)
    path = await _in_thread(backend.file_path, claims.key)
    if await _in_thread(path.is_dir):
        raise StorageError(f"{claims.key!r} is a directory, not an object")
    temp = path.with_name(f".upload-{uuid.uuid4().hex}")
    size = 0
    try:
        await _in_thread(partial(path.parent.mkdir, parents=True, exist_ok=True))
        out = await _in_thread(temp.open, "wb")
        try:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise PayloadTooLarge("upload is larger than 512 MiB")
                await _in_thread(out.write, chunk)
        finally:
            await _in_thread(out.close)
        # Right before the target changes: the lease may have ended meanwhile.
        await _replace_if_lease_current(request, claims, temp, path)
    except (NotADirectoryError, FileExistsError) as exc:
        raise StorageError(f"invalid storage key {claims.key!r}") from exc
    except PermissionError:
        return error_response(
            "conflict", "the file is in use; retry shortly", 409, headers={"Retry-After": "5"}
        )
    except OSError as exc:
        raise StorageUnavailable("the upload could not be stored") from exc
    finally:
        with contextlib.suppress(OSError):
            await _in_thread(partial(temp.unlink, missing_ok=True))
    return Response(status_code=201)
