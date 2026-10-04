"""Read-only views for administrators. Each returns plain data shaped like its API model."""

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .db.models import (
    ConsoleCredential,
    Follower,
    Job,
    JobAttempt,
    JobResult,
    JoinToken,
    Recording,
    StorageLocation,
)
from .errors import NotFound
from .jobs.claims import output_keys
from .jobs.store import OPEN_STATES

JOB_STATES = ("queued", "leased", "completed", "failed", "cancelled")
FOLLOWER_STATES = ("active", "draining", "revoked", "gone")


def location_view(location: StorageLocation) -> dict[str, Any]:
    return {
        "id": str(location.id),
        "name": location.name,
        "backend": location.backend,
        "root": (location.config or {}).get("root"),
        "input_prefix": location.input_prefix,
        "output_prefix": location.output_prefix,
        "pool": location.pool,
        "required_device": location.required_device,
        "scan_interval_s": location.scan_interval_s,
        "enabled": location.enabled,
        "last_scan_at": location.last_scan_at,
        "last_scan_error": location.last_scan_error,
        "scan_requested": location.scan_requested_at is not None,
        "channel_mode": location.channel_mode,
        "channel_labels": list(location.channel_labels),
    }


def job_view(
    job: Job, key: str, location_name: str, no_speech: bool | None = None
) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "state": job.state,
        "location": location_name,
        "key": key,
        "priority": job.priority,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "pool": job.pool,
        "leased_by": str(job.leased_by) if job.leased_by else None,
        "failure_reason": job.failure_reason,
        "cancelled_by": job.cancelled_by,
        "no_speech": no_speech,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
    }


def follower_view(follower: Follower, leases: int) -> dict[str, Any]:
    return {
        "id": str(follower.id),
        "pool": follower.pool,
        "state": follower.state,
        "device": (follower.capabilities or {}).get("device"),
        "last_seen_at": follower.last_seen_at,
        "created_at": follower.created_at,
        "leases": leases,
    }


def token_view(token: JoinToken) -> dict[str, Any]:
    return {
        "id": str(token.id),
        "pool": token.pool,
        "expires_at": token.expires_at,
        "max_uses": token.max_uses,
        "uses": token.uses,
        "revoked": token.revoked,
        "created_by": token.created_by,
        "created_at": token.created_at,
    }


async def status_summary(session: AsyncSession) -> dict[str, Any]:
    jobs = dict.fromkeys(JOB_STATES, 0)
    for state, count in (
        await session.execute(select(Job.state, func.count()).group_by(Job.state))
    ).all():
        jobs[state] = count
    pools: dict[str, dict[str, Any]] = {}
    for pool, state, count in (
        await session.execute(
            select(Job.pool, Job.state, func.count())
            .where(Job.state.in_(OPEN_STATES))
            .group_by(Job.pool, Job.state)
        )
    ).all():
        pools.setdefault(pool, {"pool": pool, "queued": 0, "leased": 0})[state] = count
    # One aggregate over (pool, state) gives both the totals and the per-pool counts.
    followers = dict.fromkeys(FOLLOWER_STATES, 0)
    follower_pools: dict[str, dict[str, Any]] = {}
    for pool, state, count in (
        await session.execute(
            select(Follower.pool, Follower.state, func.count()).group_by(
                Follower.pool, Follower.state
            )
        )
    ).all():
        followers[state] = followers.get(state, 0) + count
        row = follower_pools.setdefault(pool, {"pool": pool, **dict.fromkeys(FOLLOWER_STATES, 0)})
        row[state] = row.get(state, 0) + count
    completed_last_hour = await session.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.state == "completed", Job.completed_at >= func.now() - timedelta(hours=1))
    )
    failed_attempts = await session.scalar(
        select(func.count())
        .select_from(JobAttempt)
        .where(
            JobAttempt.outcome.in_(("failed", "expired")),
            JobAttempt.ended_at >= func.now() - timedelta(days=1),
        )
    )
    counts: dict[Any, dict[str, int]] = {}
    for location_id, consent, count in (
        await session.execute(
            select(Recording.location_id, Recording.consent, func.count())
            .where(Recording.missing.is_(False))
            .group_by(Recording.location_id, Recording.consent)
        )
    ).all():
        entry = counts.setdefault(location_id, {"recordings": 0, "consented": 0})
        entry["recordings"] += count
        if consent == "consented":
            entry["consented"] += count
    locations = (
        await session.scalars(select(StorageLocation).order_by(StorageLocation.name))
    ).all()
    return {
        "jobs": jobs,
        "pools": [pools[name] for name in sorted(pools)],
        "followers": followers,
        "follower_pools": [follower_pools[name] for name in sorted(follower_pools)],
        "completed_last_hour": completed_last_hour or 0,
        "failed_attempts_last_day": failed_attempts or 0,
        "locations": [
            {
                "name": location.name,
                "backend": location.backend,
                "enabled": location.enabled,
                "last_scan_at": location.last_scan_at,
                "last_scan_error": location.last_scan_error,
                "scan_requested": location.scan_requested_at is not None,
                **counts.get(location.id, {"recordings": 0, "consented": 0}),
            }
            for location in locations
        ],
    }


async def list_locations(session: AsyncSession) -> list[dict[str, Any]]:
    locations = (
        await session.scalars(select(StorageLocation).order_by(StorageLocation.name))
    ).all()
    return [location_view(location) for location in locations]


async def list_jobs(
    session: AsyncSession,
    *,
    state: str | None = None,
    location: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    query = (
        select(Job, Recording.key, StorageLocation.name, JobResult.no_speech)
        .join(Recording, Recording.id == Job.recording_id)
        .join(StorageLocation, StorageLocation.id == Recording.location_id)
        .outerjoin(JobResult, JobResult.job_id == Job.id)
        .order_by(Job.created_at.desc(), Job.id)
        .limit(limit)
    )
    if state is not None:
        query = query.where(Job.state == state)
    if location is not None:
        query = query.where(StorageLocation.name == location)
    rows = (await session.execute(query)).all()
    return [job_view(job, key, name, no_speech) for job, key, name, no_speech in rows]


async def describe_job(session: AsyncSession, job: Job) -> dict[str, Any]:
    await session.flush()
    await session.refresh(job)
    recording = await session.get(Recording, job.recording_id)
    location = await session.get(StorageLocation, recording.location_id)
    no_speech = await session.scalar(
        select(JobResult.no_speech).where(JobResult.job_id == job.id)
    )
    return job_view(job, recording.key, location.name, no_speech)


def _leases_per_follower():
    return (
        select(Job.leased_by, func.count().label("leases"))
        .where(Job.state == "leased", Job.leased_by.is_not(None))
        .group_by(Job.leased_by)
        .subquery()
    )


async def list_followers(
    session: AsyncSession, *, state: str | None = None
) -> list[dict[str, Any]]:
    leases = _leases_per_follower()
    query = (
        select(Follower, func.coalesce(leases.c.leases, 0))
        .outerjoin(leases, leases.c.leased_by == Follower.id)
        .order_by(Follower.created_at, Follower.id)
    )
    if state is not None:
        query = query.where(Follower.state == state)
    rows = (await session.execute(query)).all()
    return [follower_view(follower, count) for follower, count in rows]


async def describe_follower(session: AsyncSession, follower: Follower) -> dict[str, Any]:
    await session.flush()
    await session.refresh(follower)
    leases = await session.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.leased_by == follower.id, Job.state == "leased")
    )
    return follower_view(follower, leases or 0)


async def list_tokens(session: AsyncSession) -> list[dict[str, Any]]:
    tokens = (
        await session.scalars(select(JoinToken).order_by(JoinToken.created_at, JoinToken.id))
    ).all()
    return [token_view(token) for token in tokens]


def console_view(console: ConsoleCredential) -> dict[str, Any]:
    """A console credential as administrators see it: never the credential or its hash."""
    return {
        "id": str(console.id),
        "name": console.name,
        "max_role": console.max_role,
        "revoked": console.revoked_at is not None,
        "revoked_at": console.revoked_at,
        "revoked_by": console.revoked_by,
        "created_by": console.created_by,
        "created_at": console.created_at,
    }


async def list_consoles(session: AsyncSession) -> list[dict[str, Any]]:
    consoles = (
        await session.scalars(
            select(ConsoleCredential).order_by(ConsoleCredential.created_at, ConsoleCredential.id)
        )
    ).all()
    return [console_view(console) for console in consoles]


async def consent_report(
    session: AsyncSession, *, location: str | None = None, limit: int = 500
) -> dict[str, Any]:
    """Consent per location, and the completed outputs flagged for deletion because their
    recording's consent was withdrawn (deletion itself is Plan B)."""
    all_locations = (
        await session.scalars(select(StorageLocation).order_by(StorageLocation.name))
    ).all()
    names = {loc.id: loc.name for loc in all_locations}
    chosen = [loc for loc in all_locations if location is None or loc.name == location]
    if location is not None and not chosen:
        raise NotFound(f"no location named {location!r}")
    ids = [loc.id for loc in chosen]
    counts = {
        loc.id: {"name": loc.name, "consented": 0, "not_consented": 0, "withdrawn": 0, "missing": 0}
        for loc in chosen
    }
    for location_id, consent, missing, count in (
        await session.execute(
            select(Recording.location_id, Recording.consent, Recording.missing, func.count())
            .where(Recording.location_id.in_(ids))
            .group_by(Recording.location_id, Recording.consent, Recording.missing)
        )
    ).all():
        counts[location_id]["missing" if missing else consent] += count
    rows = (
        await session.execute(
            select(Job, Recording, StorageLocation)
            .join(Recording, Recording.id == Job.recording_id)
            .join(StorageLocation, StorageLocation.id == Recording.location_id)
            .where(
                Job.state == "completed",
                Job.outputs_flagged_for_deletion.is_(True),
                StorageLocation.id.in_(ids),
            )
            .order_by(Job.completed_at, Job.id)
            .limit(limit + 1)
        )
    ).all()
    flagged = [
        {
            "job_id": str(job.id),
            "location": loc.name,
            "key": recording.key,
            "completed_at": job.completed_at,
            "output_location": names[loc.output_location_id or loc.id],
            "outputs": list(output_keys(loc.output_prefix, recording.key).values()),
        }
        for job, recording, loc in rows[:limit]
    ]
    return {
        "locations": list(counts.values()),
        "flagged": flagged,
        "truncated": len(rows) > limit,
    }
