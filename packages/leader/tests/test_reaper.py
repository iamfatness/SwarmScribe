import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from swarmscribe_leader.background import LOCK_KEYS, run_exclusive, run_periodically
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.models import AuditEntry, Follower, Job, JobAttempt
from swarmscribe_leader.jobs import store
from swarmscribe_leader.jobs.reaper import ReapResult, reap

GONE_AFTER = timedelta(minutes=10)


async def claim(sessionmaker, follower, *, now):
    async with sessionmaker() as session:
        job = await store.claim(session, follower, now=now, lease_seconds=120)
        await session.commit()
    return job


async def run_reaper(sessionmaker, *, now, **kwargs) -> ReapResult:
    return await reap(sessionmaker, now=now, gone_after=GONE_AFTER, **kwargs)


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


async def test_a_silent_draining_follower_stays_draining(sessionmaker, factory):
    now = utcnow()
    draining, _ = await factory.follower(state="draining", last_seen_at=now - timedelta(hours=1))
    result = await run_reaper(sessionmaker, now=now)
    assert result.gone == 0
    async with sessionmaker() as session:
        assert (await session.get(Follower, draining.id)).state == "draining"


async def test_the_reaper_never_waits_on_a_follower_row(sessionmaker, factory):
    job = await factory.job()
    start = utcnow()
    holder_follower, _ = await factory.follower()
    await claim(sessionmaker, holder_follower, now=start)
    later = start + timedelta(minutes=11)
    busy, _ = await factory.follower(last_seen_at=start)
    quiet, _ = await factory.follower(last_seen_at=start)
    async with sessionmaker() as holder:
        # e.g. a request from `busy` is in flight and holds its follower row
        await holder.get(Follower, busy.id, with_for_update=True)
        result = await asyncio.wait_for(run_reaper(sessionmaker, now=later), timeout=10)
        await holder.rollback()
    assert (result.requeued, result.gone) == (1, 2)  # the job's holder and `quiet`
    async with sessionmaker() as session:
        states = {f.id: f.state for f in (await session.scalars(select(Follower))).all()}
        assert (await session.get(Job, job.id)).state == "queued"
    assert states == {holder_follower.id: "gone", busy.id: "active", quiet.id: "gone"}


async def test_the_job_requeue_commits_even_if_marking_followers_fails(
    sessionmaker, factory, monkeypatch
):
    job = await factory.job()
    follower, _ = await factory.follower()
    start = utcnow()
    await claim(sessionmaker, follower, now=start)

    async def broken(*_args, **_kwargs):
        raise RuntimeError("follower update failed")

    monkeypatch.setattr("swarmscribe_leader.jobs.reaper.mark_gone", broken)
    with pytest.raises(RuntimeError):
        await run_reaper(sessionmaker, now=start + timedelta(seconds=121))
    async with sessionmaker() as session:
        assert (await session.get(Job, job.id)).state == "queued"


async def test_no_lease_expires_during_the_startup_grace(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    start = utcnow()
    await claim(sessionmaker, follower, now=start)
    now = start + timedelta(seconds=121)
    grace = timedelta(seconds=120)
    result = await run_reaper(
        sessionmaker, now=now, started_at=now - timedelta(seconds=10), startup_grace=grace
    )
    assert result.requeued == 0
    async with sessionmaker() as session:
        assert (await session.get(Job, job.id)).state == "leased"
    result = await run_reaper(
        sessionmaker, now=now, started_at=now - timedelta(seconds=120), startup_grace=grace
    )
    assert result.requeued == 1


async def test_the_app_passes_its_start_time_and_the_lease_as_grace(
    engine, migrated_database_url, monkeypatch
):
    from swarmscribe_leader.app import create_app
    from swarmscribe_leader.config import Settings

    calls = []

    async def fake_reap(sessionmaker, **kwargs):
        calls.append(kwargs)
        return ReapResult(0, 0, 0)

    async def run_now(_engine, _name, work):
        await work()
        return True

    monkeypatch.setattr("swarmscribe_leader.app.reap", fake_reap)
    monkeypatch.setattr("swarmscribe_leader.app.run_exclusive", run_now)
    async def fake_scan(*_args, **_kwargs):
        return {}

    monkeypatch.setattr("swarmscribe_leader.app.scan_due_locations", fake_scan)
    settings = Settings(
        database_url=migrated_database_url,
        public_url="http://leader",
        link_key="k" * 32,
        lease_seconds=90,
        heartbeat_seconds=30,
        reaper_interval_seconds=0.05,
    )
    app = create_app(settings, background=True)
    before = utcnow()
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.2)
    assert calls
    started_at = calls[0]["started_at"]
    assert before <= started_at <= utcnow()
    assert app.state.started_at == started_at
    assert calls[0]["startup_grace"] == timedelta(seconds=90)


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


async def lock_holder_state(engine, name):
    async with engine.connect() as conn:
        return await conn.scalar(
            text(
                "select a.state from pg_locks l join pg_stat_activity a using (pid)"
                " where l.locktype = 'advisory' and l.granted and l.objid::bigint = :key"
            ),
            {"key": LOCK_KEYS[name]},
        )


async def acquires_soon(engine, name) -> bool:
    """Another process (a separate engine: session advisory locks are re-entrant, so a
    pooled connection that leaked the lock would 'acquire' it again) gets the lock. The lock
    of a dropped connection is freed when the server notices; allow a moment."""

    async def nothing():
        return None

    other = create_async_engine(engine.url)
    try:
        for _ in range(50):
            if await run_exclusive(other, name, nothing):
                return True
            await asyncio.sleep(0.1)
        return False
    finally:
        await other.dispose()


async def test_the_lock_connection_is_not_idle_in_a_transaction_while_work_runs(engine):
    seen = []

    async def work():
        seen.append(await lock_holder_state(engine, "reaper"))

    assert await run_exclusive(engine, "reaper", work) is True
    assert seen == ["idle"]


async def test_cancelling_exclusive_work_does_not_leak_the_lock(engine):
    started = asyncio.Event()

    async def forever():
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(run_exclusive(engine, "scanner", forever))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await acquires_soon(engine, "scanner")


async def test_an_unlock_that_does_not_complete_drops_the_connection_and_its_lock(engine):
    started = asyncio.Event()

    async def cancelled_again_on_the_way_out():
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            # A second cancellation arrives while run_exclusive is unlocking.
            asyncio.current_task().cancel()

    task = asyncio.create_task(run_exclusive(engine, "scanner", cancelled_again_on_the_way_out))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await acquires_soon(engine, "scanner")
    assert await lock_holder_state(engine, "scanner") is None


async def test_a_failed_unlock_drops_the_connection_instead_of_pooling_the_lock(
    engine, monkeypatch
):
    real_execute = AsyncConnection.execute

    async def unlock_fails(self, statement, *args, **kwargs):
        if "pg_advisory_unlock" in str(statement):
            raise RuntimeError("unlock interrupted")
        return await real_execute(self, statement, *args, **kwargs)

    async def nothing():
        return None

    monkeypatch.setattr(AsyncConnection, "execute", unlock_fails)
    with pytest.raises(RuntimeError):
        await run_exclusive(engine, "reaper", nothing)
    monkeypatch.undo()
    assert await acquires_soon(engine, "reaper")
    assert await lock_holder_state(engine, "reaper") is None


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


async def test_reaper_transitions_are_audited(sessionmaker, factory):
    job = await factory.job()
    holder, _ = await factory.follower()
    start = utcnow()
    await claim(sessionmaker, holder, now=start)
    silent, _ = await factory.follower(last_seen_at=start - timedelta(hours=1))
    await run_reaper(sessionmaker, now=start + timedelta(seconds=121))
    async with sessionmaker() as session:
        entries = (
            await session.scalars(select(AuditEntry).where(AuditEntry.actor == "system"))
        ).all()
    (expired,) = [e for e in entries if e.action == "job.expire"]
    assert (expired.subject_type, expired.subject_id) == ("job", str(job.id))
    assert expired.detail == {"follower": str(holder.id), "attempt": 1, "state": "queued"}
    (gone,) = [e for e in entries if e.action == "follower.gone"]
    assert (gone.subject_type, gone.subject_id) == ("follower", str(silent.id))


async def test_a_lease_that_expires_on_its_last_attempt_is_audited_as_failed(
    sessionmaker, factory
):
    job = await factory.job(max_attempts=1)
    holder, _ = await factory.follower()
    start = utcnow()
    await claim(sessionmaker, holder, now=start)
    await run_reaper(sessionmaker, now=start + timedelta(seconds=121))
    async with sessionmaker() as session:
        (entry,) = (
            await session.scalars(select(AuditEntry).where(AuditEntry.action == "job.expire"))
        ).all()
    assert (entry.actor, entry.subject_type, entry.subject_id) == ("system", "job", str(job.id))
    assert entry.detail == {"follower": str(holder.id), "attempt": 1, "state": "failed"}
