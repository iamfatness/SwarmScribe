"""Job state machine. Every function leaves committing to the caller."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from swarmscribe_protocol import Directive, FailRequest, SubmitRequest

from .. import audit
from ..db.models import Follower, Job, JobAttempt, JobResult
from ..errors import Conflict, NotFound, StaleLease

OPEN_STATES = ("queued", "leased")
NON_RETRYABLE = frozenset({"source_changed", "undecodable"})


def _parse_lease(lease_id: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(lease_id)
    except ValueError:
        return None


async def _locked_job(session: AsyncSession, job_id: uuid.UUID) -> Job:
    job = await session.get(Job, job_id, with_for_update=True, populate_existing=True)
    if job is None:
        raise NotFound("no such job")
    return job


def _require_lease(job: Job, lease_id: str, follower: Follower) -> None:
    if (
        job.state != "leased"
        or job.lease_id is None
        or job.lease_id != _parse_lease(lease_id)
        or job.leased_by != follower.id
    ):
        raise StaleLease("this job is not leased to you with that lease")


def clear_lease(job: Job) -> None:
    job.lease_id = None
    job.leased_by = None
    job.lease_expires_at = None


async def close_attempt(
    session: AsyncSession, job: Job, outcome: str, reason: str | None, now: datetime
) -> None:
    await session.execute(
        update(JobAttempt)
        .where(
            JobAttempt.job_id == job.id,
            JobAttempt.lease_id == job.lease_id,
            JobAttempt.ended_at.is_(None),
        )
        .values(ended_at=now, outcome=outcome, reason=reason)
    )


async def claim(
    session: AsyncSession, follower: Follower, *, now: datetime, lease_seconds: int
) -> Job | None:
    device = follower.capabilities.get("device", "cpu")
    job = await session.scalar(
        select(Job)
        .where(
            Job.state == "queued",
            Job.pool == follower.pool,
            Job.required_device.in_(("any", device)),
        )
        .order_by(Job.priority.desc(), Job.created_at, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None
    job.state = "leased"
    job.lease_id = uuid.uuid4()
    job.leased_by = follower.id
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    job.attempts += 1
    session.add(
        JobAttempt(job_id=job.id, follower_id=follower.id, lease_id=job.lease_id, started_at=now)
    )
    audit.record(
        session,
        actor=f"follower:{follower.id}",
        action="job.claim",
        subject_type="job",
        subject_id=job.id,
        detail={"attempt": job.attempts},
    )
    return job


async def heartbeat(
    session: AsyncSession,
    job_id: uuid.UUID,
    lease_id: str,
    follower: Follower,
    *,
    now: datetime,
    lease_seconds: int,
) -> Directive:
    job = await _locked_job(session, job_id)
    if (
        job.state == "cancelled"
        and job.lease_id is not None
        and job.lease_id == _parse_lease(lease_id)
        and job.leased_by == follower.id
    ):
        return "cancel"
    _require_lease(job, lease_id, follower)
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    return "drain" if follower.state == "draining" else "continue"


def _same_checksums(result: JobResult, request: SubmitRequest) -> bool:
    c = request.checksums
    return (result.source_sha256, result.txt_sha256, result.srt_sha256, result.segments_sha256) == (
        c.source,
        c.txt,
        c.srt,
        c.segments_json,
    )


async def submit(
    session: AsyncSession,
    job_id: uuid.UUID,
    request: SubmitRequest,
    follower: Follower,
    *,
    now: datetime,
    outputs_present: Callable[[Job], Awaitable[bool]],
) -> None:
    job = await _locked_job(session, job_id)
    if job.state == "completed":
        result = await session.scalar(select(JobResult).where(JobResult.job_id == job.id))
        if (
            job.lease_id == _parse_lease(request.lease_id)
            and job.leased_by == follower.id
            and result is not None
            and _same_checksums(result, request)
        ):
            return
        raise StaleLease("this job is already completed")
    _require_lease(job, request.lease_id, follower)
    if not await outputs_present(job):
        raise Conflict("the outputs are not in storage yet", code="outputs_missing")
    c = request.checksums
    session.add(
        JobResult(
            job_id=job.id,
            source_sha256=c.source,
            txt_sha256=c.txt,
            srt_sha256=c.srt,
            segments_sha256=c.segments_json,
        )
    )
    await close_attempt(session, job, "completed", None, now)
    job.state = "completed"
    job.completed_at = now
    job.lease_expires_at = None
    audit.record(
        session,
        actor=f"follower:{follower.id}",
        action="job.submit",
        subject_type="job",
        subject_id=job.id,
    )


async def fail(
    session: AsyncSession,
    job_id: uuid.UUID,
    request: FailRequest,
    follower: Follower,
    *,
    now: datetime,
) -> None:
    job = await _locked_job(session, job_id)
    _require_lease(job, request.lease_id, follower)
    reason = f"{request.code}: {request.reason}"
    await close_attempt(session, job, "failed", reason, now)
    if request.code in NON_RETRYABLE or not request.retryable or job.attempts >= job.max_attempts:
        job.state = "failed"
        job.failure_reason = reason
    else:
        job.state = "queued"
    clear_lease(job)
    audit.record(
        session,
        actor=f"follower:{follower.id}",
        action="job.fail",
        subject_type="job",
        subject_id=job.id,
        detail={"code": request.code, "retryable": request.retryable, "state": job.state},
    )


async def _release(session: AsyncSession, job: Job, now: datetime) -> None:
    await close_attempt(session, job, "released", None, now)
    job.attempts = max(0, job.attempts - 1)
    job.state = "queued"
    clear_lease(job)


async def release(
    session: AsyncSession, job_id: uuid.UUID, lease_id: str, follower: Follower, *, now: datetime
) -> None:
    job = await _locked_job(session, job_id)
    _require_lease(job, lease_id, follower)
    await _release(session, job, now)


async def release_all(session: AsyncSession, follower: Follower, *, now: datetime) -> int:
    jobs = (
        await session.scalars(
            select(Job).where(Job.leased_by == follower.id, Job.state == "leased").with_for_update()
        )
    ).all()
    for job in jobs:
        await _release(session, job, now)
    return len(jobs)


async def cancel(session: AsyncSession, job: Job, *, now: datetime, reason: str) -> None:
    """Cancel a queued or leased job. A leased job keeps its lease id so the holder hears cancel."""
    if job.state == "leased":
        await close_attempt(session, job, "cancelled", reason, now)
        job.lease_expires_at = None
    job.state = "cancelled"
    job.failure_reason = reason
