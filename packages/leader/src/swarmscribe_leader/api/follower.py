import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from swarmscribe_protocol import (
    ClaimResponse,
    FailRequest,
    HeartbeatRequest,
    HeartbeatResponse,
    JobLinks,
    LinksRequest,
    OutputChecksums,
    RegisterRequest,
    RegisterResponse,
    ReleaseRequest,
    SubmitRequest,
    SubmitResponse,
)

from .. import audit
from ..auth.followers import authenticate, register
from ..clock import utcnow
from ..db.models import Follower, Job, Recording
from ..errors import LeaderError, Unauthorized
from ..jobs import store
from ..jobs.claims import (
    build_claim,
    build_links,
    device_of,
    outputs_unchanged,
    outputs_verified,
    profile_for,
)
from ..storage.base import StorageError
from .deps import db_session, settings_of

logger = logging.getLogger(__name__)
MAX_CLAIM_ATTEMPTS = 5
UNBUILDABLE_BACKOFF_SECONDS = 60

router = APIRouter(prefix="/v1")


async def current_follower(
    request: Request, session: Annotated[AsyncSession, Depends(db_session)]
) -> Follower:
    scheme, _, credential = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not credential.strip():
        raise Unauthorized("missing follower credential")
    return await authenticate(session, credential.strip(), now=utcnow())


def _no_work(request: Request) -> Response:
    return Response(
        status_code=204, headers={"Retry-After": str(settings_of(request).claim_retry_after)}
    )


@router.post("/followers/register", response_model=RegisterResponse)
async def register_follower(
    body: RegisterRequest, request: Request, session: Annotated[AsyncSession, Depends(db_session)]
) -> RegisterResponse:
    settings = settings_of(request)
    follower, credential = await register(session, body, now=utcnow())
    await session.commit()
    return RegisterResponse(
        follower_id=str(follower.id),
        credential=credential,
        heartbeat_interval=settings.heartbeat_seconds,
        lease_seconds=settings.lease_seconds,
    )


@router.post("/jobs/claim", response_model=ClaimResponse)
async def claim_job(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    follower: Annotated[Follower, Depends(current_follower)],
) -> ClaimResponse | Response:
    settings = settings_of(request)
    if follower.state == "draining":
        await session.commit()
        return _no_work(request)
    device = device_of(follower)
    profile = await profile_for(session, device)
    if profile is None:
        logger.warning("no settings profile for device %r; follower gets no work", device)
        await session.commit()
        return _no_work(request)
    skipped: list[uuid.UUID] = []
    while len(skipped) < MAX_CLAIM_ATTEMPTS:
        # A savepoint, so an unbuildable job rolls back alone and the follower's
        # authentication (last_seen_at, gone -> active) is kept.
        savepoint = await session.begin_nested()
        job = await store.claim(
            session, follower, now=utcnow(), lease_seconds=settings.lease_seconds, exclude=skipped
        )
        if job is None:
            await savepoint.rollback()
            break
        job_id = job.id
        try:
            claim = await build_claim(
                session,
                job,
                follower,
                settings=settings,
                backend_factory=request.app.state.backend_factory,
                profile=profile,
            )
        except Exception as exc:
            await savepoint.rollback()
            # Push it back so the next claims reach the healthy jobs queued behind it.
            await store.push_back(session, job_id, delay_seconds=UNBUILDABLE_BACKOFF_SECONDS)
            await _log_unbuildable(session, job_id, exc)
            skipped.append(job_id)
            continue
        await savepoint.commit()
        await session.commit()
        return claim
    await session.commit()
    return _no_work(request)


async def _log_unbuildable(session: AsyncSession, job_id: uuid.UUID, exc: Exception) -> None:
    # Ids only: links and storage keys must never reach the log.
    job = await session.get(Job, job_id)
    recording = await session.get(Recording, job.recording_id) if job else None
    detail = exc.message if isinstance(exc, LeaderError) else type(exc).__name__
    expected = isinstance(exc, LeaderError | StorageError)
    logger.warning(
        "job %s skipped, claim could not be built (location %s): %s",
        job_id,
        recording.location_id if recording else None,
        detail,
        exc_info=None if expected else True,
    )


@router.post("/jobs/{job_id}/heartbeat", response_model=HeartbeatResponse)
async def heartbeat(
    job_id: uuid.UUID,
    body: HeartbeatRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    follower: Annotated[Follower, Depends(current_follower)],
) -> HeartbeatResponse:
    directive = await store.heartbeat(
        session,
        job_id,
        body.lease_id,
        follower,
        now=utcnow(),
        lease_seconds=settings_of(request).lease_seconds,
    )
    await session.commit()
    return HeartbeatResponse(directive=directive)


@router.post("/jobs/{job_id}/links", response_model=JobLinks)
async def fresh_links(
    job_id: uuid.UUID,
    body: LinksRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    follower: Annotated[Follower, Depends(current_follower)],
) -> JobLinks:
    """New links for the job, for the follower holding its lease: a job that outlasts its
    links (two hours for uploads) asks here. The links carry the same lease, so they stop
    working when it ends, like the ones the claim gave."""
    settings = settings_of(request)
    job = await store.refresh_links(
        session,
        job_id,
        body.lease_id,
        follower,
        min_interval_seconds=settings.links_refresh_min_seconds,
    )
    links = await build_links(
        session, job, settings=settings, backend_factory=request.app.state.backend_factory
    )
    await session.commit()
    return links


@router.post("/jobs/{job_id}/submit", response_model=SubmitResponse)
async def submit(
    job_id: uuid.UUID,
    body: SubmitRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    follower: Annotated[Follower, Depends(current_follower)],
) -> SubmitResponse:
    # Authentication locked the follower's row. Commit it now, so hashing the outputs never
    # holds that lock (the follower's heartbeats for its other jobs would wait on it).
    await session.commit()
    backend_factory = request.app.state.backend_factory

    async def verified(job: Job, checksums: OutputChecksums) -> store.OutputsCheck:
        return await outputs_verified(session, job, checksums, backend_factory=backend_factory)

    async def unchanged(job: Job, check: store.OutputsCheck) -> bool:
        return await outputs_unchanged(session, job, check, backend_factory=backend_factory)

    await store.submit(
        session,
        job_id,
        body,
        follower,
        now=utcnow(),
        outputs_verified=verified,
        outputs_unchanged=unchanged,
    )
    await session.commit()
    return SubmitResponse(accepted=True)


@router.post("/jobs/{job_id}/fail", status_code=204)
async def fail(
    job_id: uuid.UUID,
    body: FailRequest,
    session: Annotated[AsyncSession, Depends(db_session)],
    follower: Annotated[Follower, Depends(current_follower)],
) -> Response:
    await store.fail(session, job_id, body, follower, now=utcnow())
    await session.commit()
    return Response(status_code=204)


@router.post("/jobs/{job_id}/release", status_code=204)
async def release(
    job_id: uuid.UUID,
    body: ReleaseRequest,
    session: Annotated[AsyncSession, Depends(db_session)],
    follower: Annotated[Follower, Depends(current_follower)],
) -> Response:
    await store.release(session, job_id, body.lease_id, follower, now=utcnow())
    await session.commit()
    return Response(status_code=204)


@router.post("/followers/deregister", status_code=204)
async def deregister(
    session: Annotated[AsyncSession, Depends(db_session)],
    follower: Annotated[Follower, Depends(current_follower)],
) -> Response:
    released = await store.release_all(session, follower, now=utcnow())
    await session.execute(
        update(Follower)
        .where(Follower.id == follower.id, Follower.state == "active")
        .values(state="gone")
    )
    audit.record(
        session,
        actor=f"follower:{follower.id}",
        action="follower.deregister",
        subject_type="follower",
        subject_id=follower.id,
        detail={"released": released},
    )
    await session.commit()
    return Response(status_code=204)
