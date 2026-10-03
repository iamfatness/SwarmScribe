from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..db.models import Follower, Job
from .store import clear_lease, close_attempt


@dataclass(frozen=True)
class ReapResult:
    requeued: int
    failed: int
    gone: int


async def expire_leases(session: AsyncSession, *, now: datetime) -> tuple[int, int]:
    """Return expired leases to the queue (or fail them). Locks job rows only, and skips
    any another transaction holds (a submit in progress). Returns (requeued, failed)."""
    jobs = (
        await session.scalars(
            select(Job)
            .where(Job.state == "leased", Job.lease_expires_at < now)
            .order_by(Job.id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
    ).all()
    requeued = failed = 0
    for job in jobs:
        await close_attempt(session, job, "expired", "lease expired", now)
        if job.attempts >= job.max_attempts:
            job.state = "failed"
            job.failure_reason = "lease expired too many times"
            failed += 1
        else:
            job.state = "queued"
            requeued += 1
        clear_lease(job)
    return requeued, failed


async def mark_gone(session: AsyncSession, *, now: datetime, gone_after: timedelta) -> int:
    """Mark silent, lease-less `active` followers gone. Locks follower rows only, and skips
    any another transaction holds (a request from that follower is in flight). A draining
    follower is never touched: gone -> active on its next call would end the drain."""
    holding = select(Job.leased_by).where(Job.state == "leased", Job.leased_by.is_not(None))
    silent = (
        select(Follower.id)
        .where(
            Follower.state == "active",
            Follower.last_seen_at < now - gone_after,
            Follower.id.not_in(holding),
        )
        .with_for_update(skip_locked=True)
    )
    result = await session.execute(
        update(Follower)
        .where(Follower.id.in_(silent))
        .values(state="gone")
        .execution_options(synchronize_session=False)
    )
    return result.rowcount


async def reap(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    gone_after: timedelta,
    started_at: datetime | None = None,
    startup_grace: timedelta = timedelta(0),
) -> ReapResult:
    """Expire leases, then mark silent followers gone, in two separate transactions so the
    reaper never holds job and follower locks together.

    No lease expires until the process has run for `startup_grace` since `started_at`:
    after a leader-wide outage, live followers need one lease length to heartbeat again.
    """
    requeued = failed = 0
    if started_at is None or now - started_at >= startup_grace:
        async with sessionmaker() as session:
            requeued, failed = await expire_leases(session, now=now)
            await session.commit()
    async with sessionmaker() as session:
        gone = await mark_gone(session, now=now, gone_after=gone_after)
        await session.commit()
    return ReapResult(requeued=requeued, failed=failed, gone=gone)
