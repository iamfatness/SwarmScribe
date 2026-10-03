import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.models import Job, JobAttempt, JobResult
from swarmscribe_leader.errors import Conflict, NotFound, StaleLease
from swarmscribe_leader.jobs import store
from swarmscribe_protocol import FailRequest, OutputChecksums, SubmitRequest

H = "a" * 64


async def present(_job):
    return True


async def absent(_job):
    return False


def submission(lease_id, checksum=H) -> SubmitRequest:
    return SubmitRequest(
        lease_id=str(lease_id),
        checksums=OutputChecksums(
            source=checksum, txt=checksum, srt=checksum, segments_json=checksum
        ),
    )


async def claim(sessionmaker, follower, *, now=None):
    async with sessionmaker() as session:
        job = await store.claim(session, follower, now=now or utcnow(), lease_seconds=120)
        await session.commit()
    return job


async def load(sessionmaker, job_id) -> Job:
    async with sessionmaker() as session:
        return await session.get(Job, job_id)


async def attempts(sessionmaker, job_id) -> list[JobAttempt]:
    async with sessionmaker() as session:
        return list(
            (
                await session.scalars(
                    select(JobAttempt)
                    .where(JobAttempt.job_id == job_id)
                    .order_by(JobAttempt.started_at)
                )
            ).all()
        )


# --- claim ---------------------------------------------------------------------


async def test_claim_returns_none_when_nothing_is_queued(sessionmaker, factory):
    follower, _ = await factory.follower()
    assert await claim(sessionmaker, follower) is None


async def test_claim_leases_the_job_and_opens_an_attempt(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    now = utcnow()
    claimed = await claim(sessionmaker, follower, now=now)
    stored = await load(sessionmaker, job.id)
    assert claimed.id == job.id
    assert stored.state == "leased"
    assert stored.leased_by == follower.id
    assert stored.lease_id is not None
    assert stored.attempts == 1
    assert stored.lease_expires_at == now + timedelta(seconds=120)
    (attempt,) = await attempts(sessionmaker, job.id)
    assert (attempt.follower_id, attempt.lease_id, attempt.ended_at) == (
        follower.id,
        stored.lease_id,
        None,
    )


async def test_claim_matches_pool_and_device(sessionmaker, factory):
    location = await factory.location()
    cuda_only = await factory.job(
        await factory.recording(location, key="a.mp3"), required_device="cuda"
    )
    other_pool = await factory.job(await factory.recording(location, key="b.mp3"), pool="elsewhere")
    cpu_follower, _ = await factory.follower(device="cpu")
    assert await claim(sessionmaker, cpu_follower) is None
    cuda_follower, _ = await factory.follower(device="cuda")
    assert (await claim(sessionmaker, cuda_follower)).id == cuda_only.id
    elsewhere, _ = await factory.follower(pool="elsewhere")
    assert (await claim(sessionmaker, elsewhere)).id == other_pool.id


async def test_claim_takes_priority_first_then_oldest(sessionmaker, factory):
    location = await factory.location()
    first = await factory.job(await factory.recording(location, key="1.mp3"))
    second = await factory.job(await factory.recording(location, key="2.mp3"))
    urgent = await factory.job(await factory.recording(location, key="3.mp3"), priority=5)
    follower, _ = await factory.follower()
    order = [(await claim(sessionmaker, follower)).id for _ in range(3)]
    assert order == [urgent.id, first.id, second.id]


async def test_concurrent_claims_never_share_a_job(sessionmaker, factory):
    location = await factory.location()
    for i in range(20):
        await factory.job(await factory.recording(location, key=f"talks/{i:02d}.mp3"))
    followers = [(await factory.follower())[0] for _ in range(50)]

    async def one(follower):
        job = await claim(sessionmaker, follower)
        return None if job is None else job.id

    claimed = [job_id for job_id in await asyncio.gather(*(one(f) for f in followers)) if job_id]
    assert len(claimed) == 20
    assert len(set(claimed)) == 20


# --- heartbeat -----------------------------------------------------------------


async def test_heartbeat_extends_the_lease(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)
    later = utcnow() + timedelta(seconds=60)
    async with sessionmaker() as session:
        directive = await store.heartbeat(
            session, job.id, str(claimed.lease_id), follower, now=later, lease_seconds=120
        )
        await session.commit()
    assert directive == "continue"
    assert (await load(sessionmaker, job.id)).lease_expires_at == later + timedelta(seconds=120)


@pytest.mark.parametrize("who", ["wrong-lease", "other-follower", "not-a-uuid"])
async def test_heartbeat_with_a_stale_lease_is_refused(sessionmaker, factory, who):
    job = await factory.job()
    follower, _ = await factory.follower()
    other, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)
    lease = {
        "wrong-lease": str(uuid.uuid4()),
        "other-follower": str(claimed.lease_id),
        "not-a-uuid": "x",
    }[who]
    caller = other if who == "other-follower" else follower
    async with sessionmaker() as session:
        with pytest.raises(StaleLease):
            await store.heartbeat(session, job.id, lease, caller, now=utcnow(), lease_seconds=120)


async def test_heartbeat_tells_a_draining_follower_to_drain(sessionmaker, factory):
    await factory.job()
    follower, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)
    follower.state = "draining"
    async with sessionmaker() as session:
        directive = await store.heartbeat(
            session, claimed.id, str(claimed.lease_id), follower, now=utcnow(), lease_seconds=120
        )
    assert directive == "drain"


async def test_heartbeat_on_a_cancelled_job_says_cancel_and_submit_is_refused(
    sessionmaker, factory
):
    job = await factory.job()
    follower, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)
    async with sessionmaker() as session:
        locked = await session.get(Job, job.id, with_for_update=True)
        await store.cancel(session, locked, now=utcnow(), reason="consent withdrawn")
        await session.commit()
    async with sessionmaker() as session:
        directive = await store.heartbeat(
            session, job.id, str(claimed.lease_id), follower, now=utcnow(), lease_seconds=120
        )
    assert directive == "cancel"
    async with sessionmaker() as session:
        with pytest.raises(StaleLease):
            await store.submit(
                session,
                job.id,
                submission(claimed.lease_id),
                follower,
                now=utcnow(),
                outputs_present=present,
            )
    (attempt,) = await attempts(sessionmaker, job.id)
    assert attempt.outcome == "cancelled"


async def test_unknown_job_is_not_found(sessionmaker, factory):
    follower, _ = await factory.follower()
    async with sessionmaker() as session:
        with pytest.raises(NotFound):
            await store.heartbeat(
                session, uuid.uuid4(), str(uuid.uuid4()), follower, now=utcnow(), lease_seconds=120
            )


# --- submit --------------------------------------------------------------------


async def test_submit_completes_the_job_and_records_checksums(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)
    async with sessionmaker() as session:
        await store.submit(
            session,
            job.id,
            submission(claimed.lease_id),
            follower,
            now=utcnow(),
            outputs_present=present,
        )
        await session.commit()
    stored = await load(sessionmaker, job.id)
    assert stored.state == "completed"
    assert stored.completed_at is not None
    async with sessionmaker() as session:
        result = (await session.scalars(select(JobResult))).one()
    assert (result.source_sha256, result.txt_sha256) == (H, H)
    (attempt,) = await attempts(sessionmaker, job.id)
    assert attempt.outcome == "completed"


async def test_submit_is_idempotent(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)
    for _ in range(2):
        async with sessionmaker() as session:
            await store.submit(
                session,
                job.id,
                submission(claimed.lease_id),
                follower,
                now=utcnow(),
                outputs_present=present,
            )
            await session.commit()
    async with sessionmaker() as session:
        assert len((await session.scalars(select(JobResult))).all()) == 1


async def test_resubmitting_different_checksums_is_refused(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)
    async with sessionmaker() as session:
        await store.submit(
            session,
            job.id,
            submission(claimed.lease_id),
            follower,
            now=utcnow(),
            outputs_present=present,
        )
        await session.commit()
    async with sessionmaker() as session:
        with pytest.raises(StaleLease):
            await store.submit(
                session,
                job.id,
                submission(claimed.lease_id, "b" * 64),
                follower,
                now=utcnow(),
                outputs_present=present,
            )


async def test_submit_without_outputs_in_storage_is_refused_and_keeps_the_lease(
    sessionmaker, factory
):
    job = await factory.job()
    follower, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)
    async with sessionmaker() as session:
        with pytest.raises(Conflict) as excinfo:
            await store.submit(
                session,
                job.id,
                submission(claimed.lease_id),
                follower,
                now=utcnow(),
                outputs_present=absent,
            )
    assert excinfo.value.code == "outputs_missing"
    assert (await load(sessionmaker, job.id)).state == "leased"


# --- fail, release, release_all -------------------------------------------------


async def fail_with(
    sessionmaker, job_id, lease_id, follower, *, code="engine_error", retryable=True
):
    async with sessionmaker() as session:
        await store.fail(
            session,
            job_id,
            FailRequest(lease_id=str(lease_id), code=code, reason="it broke", retryable=retryable),
            follower,
            now=utcnow(),
        )
        await session.commit()


async def test_a_retryable_failure_requeues(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)
    await fail_with(sessionmaker, job.id, claimed.lease_id, follower)
    stored = await load(sessionmaker, job.id)
    assert (stored.state, stored.lease_id, stored.leased_by, stored.attempts) == (
        "queued",
        None,
        None,
        1,
    )
    (attempt,) = await attempts(sessionmaker, job.id)
    assert (attempt.outcome, attempt.reason) == ("failed", "engine_error: it broke")


@pytest.mark.parametrize("code", ["source_changed", "undecodable"])
async def test_non_retryable_codes_fail_the_job_even_if_marked_retryable(
    sessionmaker, factory, code
):
    job = await factory.job()
    follower, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)
    await fail_with(sessionmaker, job.id, claimed.lease_id, follower, code=code, retryable=True)
    stored = await load(sessionmaker, job.id)
    assert stored.state == "failed"
    assert stored.failure_reason == f"{code}: it broke"


async def test_the_last_attempt_fails_the_job(sessionmaker, factory):
    job = await factory.job(max_attempts=1)
    follower, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)
    await fail_with(sessionmaker, job.id, claimed.lease_id, follower)
    assert (await load(sessionmaker, job.id)).state == "failed"


async def test_release_requeues_without_counting_the_attempt(sessionmaker, factory):
    job = await factory.job()
    follower, _ = await factory.follower()
    claimed = await claim(sessionmaker, follower)
    async with sessionmaker() as session:
        await store.release(session, job.id, str(claimed.lease_id), follower, now=utcnow())
        await session.commit()
    stored = await load(sessionmaker, job.id)
    assert (stored.state, stored.attempts, stored.lease_id) == ("queued", 0, None)
    (attempt,) = await attempts(sessionmaker, job.id)
    assert attempt.outcome == "released"


async def test_release_all_frees_every_lease_a_follower_holds(sessionmaker, factory):
    location = await factory.location()
    for key in ("a.mp3", "b.mp3"):
        await factory.job(await factory.recording(location, key=key))
    follower, _ = await factory.follower()
    await claim(sessionmaker, follower)
    await claim(sessionmaker, follower)
    async with sessionmaker() as session:
        released = await store.release_all(session, follower, now=utcnow())
        await session.commit()
    assert released == 2
    async with sessionmaker() as session:
        states = {j.state for j in (await session.scalars(select(Job))).all()}
    assert states == {"queued"}


async def test_cancel_a_queued_job(sessionmaker, factory):
    job = await factory.job()
    async with sessionmaker() as session:
        locked = await session.get(Job, job.id, with_for_update=True)
        await store.cancel(session, locked, now=utcnow(), reason="recording missing")
        await session.commit()
    stored = await load(sessionmaker, job.id)
    assert (stored.state, stored.failure_reason) == ("cancelled", "recording missing")
