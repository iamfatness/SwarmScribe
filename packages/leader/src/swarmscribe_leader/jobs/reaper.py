from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Follower, Job
from .store import clear_lease, close_attempt


@dataclass(frozen=True)
class ReapResult:
    requeued: int
    failed: int
    gone: int


async def reap(session: AsyncSession, *, now: datetime, gone_after: timedelta) -> ReapResult:
    """Return expired leases to the queue and mark silent followers gone.

    Jobs locked by another transaction (a submit in progress) are skipped this round.
    """
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
    await session.flush()
    holding = select(Job.leased_by).where(Job.state == "leased", Job.leased_by.is_not(None))
    result = await session.execute(
        update(Follower)
        .where(
            Follower.state.in_(("active", "draining")),
            Follower.last_seen_at < now - gone_after,
            Follower.id.not_in(holding),
        )
        .values(state="gone")
    )
    return ReapResult(requeued=requeued, failed=failed, gone=result.rowcount)
