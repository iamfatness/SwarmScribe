import asyncio
from datetime import timedelta

from sqlalchemy import select
from swarmscribe_leader.background import run_exclusive, run_periodically
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.models import Follower, Job, JobAttempt
from swarmscribe_leader.jobs import store
from swarmscribe_leader.jobs.reaper import ReapResult, reap

GONE_AFTER = timedelta(minutes=10)


async def claim(sessionmaker, follower, *, now):
    async with sessionmaker() as session:
        job = await store.claim(session, follower, now=now, lease_seconds=120)
        await session.commit()
    return job


async def run_reaper(sessionmaker, *, now) -> ReapResult:
    async with sessionmaker() as session:
        result = await reap(session, now=now, gone_after=GONE_AFTER)
        await session.commit()
    return result


async def test_an_expired_lease_is_requeued(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    start = utcnow()
    await claim(sessionmaker, follower, now=start)
    result = await run_reaper(sessionmaker, now=start + timedelta(seconds=121))
    assert result == ReapResult(requeued=1, failed=0, gone=0)
    async with sessionmaker() as session:
        stored = await session.get(Job, job.id)
        attempt = (await session.scalars(select(JobAttempt))).one()
    assert (stored.state, stored.lease_id, stored.leased_by) == ("queued", None, None)
    assert attempt.outcome == "expired"


async def test_a_lease_that_has_not_expired_is_left_alone(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    start = utcnow()
    await claim(sessionmaker, follower, now=start)
    await run_reaper(sessionmaker, now=start + timedelta(seconds=60))
    async with sessionmaker() as session:
        assert (await session.get(Job, job.id)).state == "leased"


async def test_expiring_the_last_attempt_fails_the_job(sessionmaker, factory):
    job = await factory.job(max_attempts=1)
    follower, _ = await factory.follower()
    start = utcnow()
    await claim(sessionmaker, follower, now=start)
    result = await run_reaper(sessionmaker, now=start + timedelta(seconds=121))
    assert result.failed == 1
    async with sessionmaker() as session:
        stored = await session.get(Job, job.id)
    assert (stored.state, stored.failure_reason) == ("failed", "lease expired too many times")


async def test_a_job_locked_by_another_transaction_is_skipped(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    start = utcnow()
    await claim(sessionmaker, follower, now=start)
    async with sessionmaker() as holder:
        await holder.get(Job, job.id, with_for_update=True)  # e.g. a submit in progress
        result = await run_reaper(sessionmaker, now=start + timedelta(seconds=121))
        assert result.requeued == 0
        await holder.rollback()
    async with sessionmaker() as session:
        assert (await session.get(Job, job.id)).state == "leased"


async def test_silent_followers_without_a_lease_are_marked_gone(sessionmaker, factory):
    await factory.job()  # before `now`: a job is claimable from its creation time on
    now = utcnow()
    quiet, _ = await factory.follower(last_seen_at=now - timedelta(minutes=11))
    busy, _ = await factory.follower(last_seen_at=now - timedelta(minutes=11))
    recent, _ = await factory.follower(last_seen_at=now - timedelta(minutes=1))
    await claim(sessionmaker, busy, now=now)
    result = await run_reaper(sessionmaker, now=now)
    assert result.gone == 1
    async with sessionmaker() as session:
        states = {f.id: f.state for f in (await session.scalars(select(Follower))).all()}
    assert states == {quiet.id: "gone", busy.id: "active", recent.id: "active"}


async def test_only_one_process_runs_exclusive_work_at_a_time(engine):
    started = asyncio.Event()
    release = asyncio.Event()
    runs = []

    async def slow():
        runs.append("slow")
        started.set()
        await release.wait()

    async def quick():
        runs.append("quick")

    first = asyncio.create_task(run_exclusive(engine, "reaper", slow))
    await started.wait()
    assert await run_exclusive(engine, "reaper", quick) is False
    release.set()
    assert await first is True
    assert await run_exclusive(engine, "reaper", quick) is True
    assert runs == ["slow", "quick"]


async def test_run_periodically_survives_errors_and_stops_when_told():
    stop = asyncio.Event()
    calls = []

    async def step():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        if len(calls) == 3:
            stop.set()

    await asyncio.wait_for(run_periodically(stop, 0.01, step, "test"), timeout=5)
    assert len(calls) == 3
