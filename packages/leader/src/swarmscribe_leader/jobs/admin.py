"""Administrators' changes to jobs. Each function audits its change; the caller commits."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..db.models import Job, Recording, StorageLocation
from ..errors import Conflict, NotFound
from .store import OPEN_STATES, cancel

ADMIN_CANCEL_REASON = "cancelled by an administrator"


async def _locked(session: AsyncSession, job_id: uuid.UUID) -> Job:
    job = await session.get(Job, job_id, with_for_update=True, populate_existing=True)
    if job is None:
        raise NotFound("no such job")
    return job


async def retry_job(
    session: AsyncSession, job_id: uuid.UUID, *, actor: str, max_attempts: int
) -> Job:
    """Queue a failed or cancelled job's recording again, as a new job (the old one stays as
    history; the new one is not cancelled, has no failure and is claimable at once). Consent,
    presence and version are checked again here: retry can never queue a recording its
    location's consent.txt does not match. The old job's row lock serialises two retries of
    the same job, and the recording's row lock two retries of different jobs for it."""
    job = await _locked(session, job_id)
    if job.state not in ("failed", "cancelled"):
        raise Conflict(
            f"the job is {job.state}; only failed or cancelled jobs can be retried",
            code="not_retryable",
        )
    recording = await session.get(
        Recording, job.recording_id, with_for_update=True, populate_existing=True
    )
    if recording.consent != "consented" or recording.missing:
        raise Conflict(
            "the recording is not consented or is no longer present", code="not_consented"
        )
    if recording.source_version != job.source_version:
        raise Conflict(
            "the recording changed after this job; the next scan queues the new version",
            code="recording_changed",
        )
    open_job = await session.scalar(
        select(Job.id)
        .where(Job.recording_id == recording.id, Job.state.in_(OPEN_STATES))
        .limit(1)
    )
    if open_job is not None:
        raise Conflict("the recording already has a queued or leased job", code="already_open")
    location = await session.get(StorageLocation, recording.location_id)
    retry = Job(
        id=uuid.uuid4(),
        recording_id=recording.id,
        source_version=recording.source_version,
        state="queued",
        pool=location.pool,
        required_device=location.required_device,
        priority=job.priority,
        attempts=0,
        max_attempts=max_attempts,
        failure_reason=None,
        cancelled_by=None,
    )
    session.add(retry)
    audit.record(
        session,
        actor=actor,
        action="job.retry",
        subject_type="job",
        subject_id=retry.id,
        detail={"retry_of": str(job.id)},
    )
    return retry


async def cancel_job(
    session: AsyncSession, job_id: uuid.UUID, *, now: datetime, actor: str
) -> Job:
    """Cancel a queued or leased job (the holder hears `cancel` on its next heartbeat).
    Cancelling a cancelled job changes nothing."""
    job = await _locked(session, job_id)
    if job.state not in (*OPEN_STATES, "cancelled"):
        raise Conflict(
            f"the job is {job.state}; only queued or leased jobs can be cancelled",
            code="not_open",
        )
    if job.state != "cancelled":
        await cancel(session, job, now=now, reason=ADMIN_CANCEL_REASON, by=actor)
    audit.record(session, actor=actor, action="job.cancel", subject_type="job", subject_id=job.id)
    return job


async def set_priority(
    session: AsyncSession, job_id: uuid.UUID, priority: int, *, actor: str
) -> Job:
    job = await _locked(session, job_id)
    if job.state not in OPEN_STATES:
        raise Conflict(
            f"the job is {job.state}; only queued or leased jobs can be reprioritised",
            code="not_open",
        )
    previous = job.priority
    job.priority = priority
    audit.record(
        session,
        actor=actor,
        action="job.priority",
        subject_type="job",
        subject_id=job.id,
        detail={"from": previous, "to": priority},
    )
    return job
