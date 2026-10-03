import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from swarmscribe_protocol import (
    ClaimResponse,
    FailRequest,
    HeartbeatRequest,
    HeartbeatResponse,
    RegisterRequest,
    RegisterResponse,
    ReleaseRequest,
    SubmitRequest,
    SubmitResponse,
)

from .. import audit
from ..auth.followers import authenticate, register
from ..clock import utcnow
from ..db.models import Follower, Job
from ..errors import Unauthorized
from ..jobs import store
from ..jobs.claims import build_claim, outputs_present
from .deps import db_session, settings_of

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
):
    settings = settings_of(request)
    if follower.state == "draining":
        await session.commit()
        return _no_work(request)
    job = await store.claim(session, follower, now=utcnow(), lease_seconds=settings.lease_seconds)
    if job is None:
        await session.commit()
        return _no_work(request)
    claim = await build_claim(
        session, job, follower, settings=settings, backend_factory=request.app.state.backend_factory
    )
    await session.commit()
    return claim


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


@router.post("/jobs/{job_id}/submit", response_model=SubmitResponse)
async def submit(
    job_id: uuid.UUID,
    body: SubmitRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    follower: Annotated[Follower, Depends(current_follower)],
) -> SubmitResponse:
    async def present(job: Job) -> bool:
        return await outputs_present(
            session, job, backend_factory=request.app.state.backend_factory
        )

    await store.submit(session, job_id, body, follower, now=utcnow(), outputs_present=present)
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
    follower.state = "gone"
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
