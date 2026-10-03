"""Job state machine. Every function leaves committing to the caller."""

import uuid
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from swarmscribe_protocol import Directive, FailRequest, OutputChecksums, SubmitRequest

from .. import audit
from ..db.models import Follower, Job, JobAttempt, JobResult
from ..errors import Conflict, NotFound, StaleLease

OPEN_STATES = ("queued", "leased")
NON_RETRYABLE = frozenset({"source_changed", "undecodable"})
_OUTPUT_PROBLEMS = {
    "outputs_missing": "the outputs are not in storage yet",
    "checksum_mismatch": "the stored outputs do not match the submitted checksums",
    "outputs_inconsistent": (
        "an empty transcript needs an empty .txt and .srt and a segments.json without segments"
    ),
    "outputs_changed": "the outputs changed while they were being checked; submit again",
}


@dataclass(frozen=True)
class OutputsCheck:
    """What checking the stored outputs found: the problem (None when there is none),
    whether they are a no-speech result, and each output's (key, version) as it was hashed."""

    problem: str | None = None
    no_speech: bool = False
    versions: tuple[tuple[str, str], ...] = ()


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
    session: AsyncSession,
    follower: Follower,
    *,
    now: datetime,
    lease_seconds: int,
    exclude: Collection[uuid.UUID] = (),
) -> Job | None:
    device = follower.capabilities.get("device", "cpu")
    job = await session.scalar(
        select(Job)
        .where(
            Job.state == "queued",
            Job.pool == follower.pool,
            Job.required_device.in_(("any", device)),
            # The database's clock, the same one that stamped available_at: replicas' clocks
            # may differ from it and from each other.
            Job.available_at <= func.now(),
            Job.id.not_in(exclude),
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


async def push_back(session: AsyncSession, job_id: uuid.UUID, *, delay_seconds: int) -> None:
    """Make a queued job unclaimable for `delay_seconds`, counted on the database's clock.
    Never waits: a job someone else has locked meanwhile (e.g. just leased it) is left to
    them."""
    lockable = (
        select(Job.id)
        .where(Job.id == job_id, Job.state == "queued")
        .with_for_update(skip_locked=True)
    )
    await session.execute(
        update(Job)
        .where(Job.id.in_(lockable))
        .values(available_at=func.now() + timedelta(seconds=delay_seconds))
        .execution_options(synchronize_session=False)
    )


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
    outputs_verified: Callable[[Job, OutputChecksums], Awaitable[OutputsCheck]],
    outputs_unchanged: Callable[[Job, OutputsCheck], Awaitable[bool]] | None = None,
) -> None:
    """Complete the job.

    The outputs are hashed before the job row is locked: hashing reads every byte, and the
    reaper and administrators must not wait for it. Under the lock the lease is checked again
    and `outputs_unchanged` confirms that no output was replaced after it was hashed.
    """
    job = await session.get(Job, job_id, populate_existing=True)
    if job is None:
        raise NotFound("no such job")
    check: OutputsCheck | None = None
    if job.state != "completed":
        _require_lease(job, request.lease_id, follower)
        check = await outputs_verified(job, request.checksums)
        if check.problem is not None:
            message = _OUTPUT_PROBLEMS.get(check.problem, "the outputs cannot be verified")
            raise Conflict(message, code=check.problem)
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
    if check is None:
        raise StaleLease("this job is not leased to you with that lease")
    if outputs_unchanged is not None and not await outputs_unchanged(job, check):
        raise Conflict(_OUTPUT_PROBLEMS["outputs_changed"], code="outputs_changed")
    c = request.checksums
    session.add(
        JobResult(
            job_id=job.id,
            source_sha256=c.source,
            txt_sha256=c.txt,
            srt_sha256=c.srt,
            segments_sha256=c.segments_json,
            no_speech=check.no_speech,
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
        detail={"no_speech": check.no_speech},
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
            select(Job)
            .where(Job.leased_by == follower.id, Job.state == "leased")
            .order_by(Job.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    for job in jobs:
        await _release(session, job, now)
    return len(jobs)


async def cancel(session: AsyncSession, job: Job, *, now: datetime, reason: str) -> None:
    """Cancel a queued or leased job. A leased job keeps its lease id so the holder hears cancel."""
    if job.state not in OPEN_STATES:
        return
    if job.state == "leased":
        await close_attempt(session, job, "cancelled", reason, now)
        job.lease_expires_at = None
    job.state = "cancelled"
    job.failure_reason = reason
