import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select
from swarmscribe_leader.auth.followers import (
    authenticate,
    create_join_token,
    register,
    revoke_follower,
)
from swarmscribe_leader.auth.pool_tokens import (
    InvalidPoolTokenName,
    create_pool_token,
    revoke_pool_token,
)
from swarmscribe_leader.auth.secrets import hash_secret
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.models import AuditEntry, Follower, Job, PoolToken
from swarmscribe_leader.errors import Conflict, Forbidden, LeaderError, NotFound, Unauthorized
from swarmscribe_leader.jobs import store
from swarmscribe_leader.jobs.reaper import reap
from swarmscribe_protocol import Capabilities, RegisterRequest


def request(token: str, *, device: str = "cpu") -> RegisterRequest:
    return RegisterRequest(
        join_token=token,
        protocol_version=1,
        capabilities=Capabilities(
            device=device, models=["distil-large-v3"], engine_version="0.1.0", pool="ignored"
        ),
    )


async def pool_token(sessionmaker, name="gpu-pods", pool="gpu") -> str:
    async with sessionmaker() as session:
        _, plaintext = await create_pool_token(session, name=name, pool=pool, actor="test")
        await session.commit()
    return plaintext


async def join(sessionmaker, plaintext, **kwargs) -> tuple[Follower, str]:
    async with sessionmaker() as session:
        follower, credential = await register(session, request(plaintext, **kwargs), now=utcnow())
        await session.commit()
    return follower, credential


async def followers(sessionmaker) -> list[Follower]:
    async with sessionmaker() as session:
        return list((await session.scalars(select(Follower).order_by(Follower.created_at))).all())


async def test_a_pool_token_is_stored_only_as_a_hash_and_audited_by_name(sessionmaker):
    plaintext = await pool_token(sessionmaker)
    async with sessionmaker() as session:
        stored = (await session.scalars(select(PoolToken))).one()
        (entry,) = (await session.scalars(select(AuditEntry))).all()
    assert stored.token_hash == hash_secret(plaintext)
    assert (stored.name, stored.pool, stored.registrations) == ("gpu-pods", "gpu", 0)
    assert (entry.action, entry.detail) == (
        "pool_token.create",
        {"name": "gpu-pods", "pool": "gpu"},
    )
    assert plaintext not in f"{entry.actor} {entry.detail} {stored.name}"


async def test_a_pool_token_registers_any_number_of_followers_into_its_pool(sessionmaker):
    plaintext = await pool_token(sessionmaker)
    credentials = set()
    for _ in range(12):
        follower, credential = await join(sessionmaker, plaintext)
        credentials.add(credential)
        assert (follower.pool, follower.state) == ("gpu", "active")
    rows = await followers(sessionmaker)
    assert len(rows) == 12 and len(credentials) == 12
    assert all(row.capabilities["pool"] == "gpu" for row in rows)  # not what it claimed
    async with sessionmaker() as session:
        stored = (await session.scalars(select(PoolToken))).one()
    assert stored.registrations == 12 and stored.last_used_at is not None


async def test_a_join_token_registration_also_records_the_tokens_pool(sessionmaker):
    async with sessionmaker() as session:
        _, plaintext = await create_join_token(
            session,
            pool="cpu",
            expires_at=utcnow() + timedelta(days=1),
            max_uses=1,
            created_by="test",
        )
        await session.commit()
    follower, _ = await join(sessionmaker, plaintext)
    assert (follower.pool, follower.capabilities["pool"], follower.pool_token_id) == (
        "cpu",
        "cpu",
        None,
    )


async def test_a_gone_followers_row_is_reused_and_its_old_credential_dies(sessionmaker):
    plaintext = await pool_token(sessionmaker)
    first, old_credential = await join(sessionmaker, plaintext)
    await join(sessionmaker, plaintext)  # still active: never reused
    async with sessionmaker() as session:
        (await session.get(Follower, first.id)).state = "gone"
        await session.commit()
    third, new_credential = await join(sessionmaker, plaintext, device="cuda")
    rows = await followers(sessionmaker)
    assert len(rows) == 2
    assert third.id == first.id
    reused = next(row for row in rows if row.id == first.id)
    assert (reused.state, reused.capabilities["device"]) == ("active", "cuda")
    async with sessionmaker() as session:
        with pytest.raises(Unauthorized):
            await authenticate(session, old_credential, now=utcnow())
    async with sessionmaker() as session:
        assert (await authenticate(session, new_credential, now=utcnow())).id == first.id
        entries = (
            await session.scalars(
                select(AuditEntry)
                .where(AuditEntry.action == "follower.register")
                .order_by(AuditEntry.created_at)
            )
        ).all()
    assert [entry.detail["reused"] for entry in entries] == [False, False, True]
    assert {entry.detail["pool_token"] for entry in entries} == {"gpu-pods"}


@pytest.mark.parametrize("state", ["draining", "revoked", "active"])
async def test_only_a_gone_follower_is_reused(sessionmaker, state):
    plaintext = await pool_token(sessionmaker)
    first, _ = await join(sessionmaker, plaintext)
    async with sessionmaker() as session:
        (await session.get(Follower, first.id)).state = state
        await session.commit()
    second, _ = await join(sessionmaker, plaintext)
    assert second.id != first.id
    async with sessionmaker() as session:
        assert (await session.get(Follower, first.id)).state == state


async def test_a_gone_follower_of_another_token_or_of_a_join_token_is_never_reused(
    sessionmaker, factory
):
    mine = await pool_token(sessionmaker)
    other = await pool_token(sessionmaker, name="other", pool="gpu")
    theirs, _ = await join(sessionmaker, other)
    plain, _ = await factory.follower(pool="gpu", state="gone")
    async with sessionmaker() as session:
        (await session.get(Follower, theirs.id)).state = "gone"
        await session.commit()
    joined, _ = await join(sessionmaker, mine)
    assert joined.id not in {theirs.id, plain.id}


async def test_a_gone_follower_that_still_holds_a_lease_is_not_reused(sessionmaker, factory):
    plaintext = await pool_token(sessionmaker, pool="default")
    first, _ = await join(sessionmaker, plaintext)
    await factory.job()
    async with sessionmaker() as session:
        holder = await session.get(Follower, first.id)
        assert await store.claim(session, holder, now=utcnow(), lease_seconds=120) is not None
        holder.state = "gone"  # cannot happen through the API; the row must still be safe
        await session.commit()
    second, _ = await join(sessionmaker, plaintext)
    assert second.id != first.id
    async with sessionmaker() as session:
        assert (await session.scalars(select(Job))).one().leased_by == first.id


async def test_a_pod_that_stops_and_one_that_starts_keep_the_table_the_same_size(sessionmaker):
    plaintext = await pool_token(sessionmaker)
    for _ in range(20):  # twenty pod restarts
        follower, credential = await join(sessionmaker, plaintext)
        async with sessionmaker() as session:
            (await session.get(Follower, follower.id)).state = "gone"  # what deregister does
            await session.commit()
    assert len(await followers(sessionmaker)) == 1


async def test_two_registrations_at_once_never_share_a_row(sessionmaker):
    plaintext = await pool_token(sessionmaker)
    first, _ = await join(sessionmaker, plaintext)
    async with sessionmaker() as session:
        (await session.get(Follower, first.id)).state = "gone"
        await session.commit()
    joined = await asyncio.gather(*(join(sessionmaker, plaintext) for _ in range(6)))
    assert len({follower.id for follower, _ in joined}) == 6
    assert len({credential for _, credential in joined}) == 6
    assert len(await followers(sessionmaker)) == 6


async def test_the_reaper_and_a_reused_row_agree(sessionmaker):
    plaintext = await pool_token(sessionmaker)
    first, _ = await join(sessionmaker, plaintext)
    await reap(sessionmaker, now=utcnow() + timedelta(hours=1), gone_after=timedelta(minutes=10))
    second, _ = await join(sessionmaker, plaintext)
    assert second.id == first.id
    assert [row.state for row in await followers(sessionmaker)] == ["active"]


async def test_a_revoked_pool_token_registers_nothing_and_leaves_its_followers(sessionmaker):
    plaintext = await pool_token(sessionmaker)
    follower, credential = await join(sessionmaker, plaintext)
    async with sessionmaker() as session:
        token, revoked = await revoke_pool_token(
            session, "gpu-pods", now=utcnow(), actor="admin@example.org"
        )
        await session.commit()
    assert (token.revoked_by, revoked) == ("admin@example.org", 0)
    async with sessionmaker() as session:
        with pytest.raises(Unauthorized):
            await register(session, request(plaintext), now=utcnow())
    async with sessionmaker() as session:
        assert (await authenticate(session, credential, now=utcnow())).id == follower.id


async def test_revoking_with_its_followers_revokes_them_and_releases_their_work(
    sessionmaker, factory
):
    plaintext = await pool_token(sessionmaker, pool="default")
    first, credential = await join(sessionmaker, plaintext)
    await join(sessionmaker, plaintext)
    bystander, _ = await factory.follower()
    await factory.job()
    async with sessionmaker() as session:
        holder = await session.get(Follower, first.id)
        await store.claim(session, holder, now=utcnow(), lease_seconds=120)
        await session.commit()
    async with sessionmaker() as session:
        _, revoked = await revoke_pool_token(
            session, "gpu-pods", now=utcnow(), actor="admin@example.org", revoke_followers=True
        )
        await session.commit()
    assert revoked == 2
    async with sessionmaker() as session:
        assert (await session.scalars(select(Job))).one().state == "queued"
        assert (await session.get(Follower, bystander.id)).state == "active"
        with pytest.raises(Forbidden):
            await authenticate(session, credential, now=utcnow())
        (entry,) = (
            await session.scalars(
                select(AuditEntry).where(AuditEntry.action == "pool_token.revoke")
            )
        ).all()
    assert entry.detail == {"name": "gpu-pods", "followers_revoked": 2}


async def test_revoking_twice_keeps_the_first_revocation(sessionmaker):
    await pool_token(sessionmaker)
    start = utcnow()
    async with sessionmaker() as session:
        await revoke_pool_token(session, "gpu-pods", now=start, actor="first")
        await session.commit()
    async with sessionmaker() as session:
        token, _ = await revoke_pool_token(
            session, "gpu-pods", now=start + timedelta(hours=1), actor="second"
        )
        await session.commit()
    assert (token.revoked_at, token.revoked_by) == (start, "first")


async def test_names_are_checked_unique_and_never_reused(sessionmaker):
    await pool_token(sessionmaker)
    async with sessionmaker() as session:
        await revoke_pool_token(session, "gpu-pods", now=utcnow(), actor="test")
        await session.commit()
    async with sessionmaker() as session:
        with pytest.raises(Conflict) as refused:
            await create_pool_token(session, name="gpu-pods", pool="gpu", actor="test")
    assert refused.value.code == "exists"
    for bad in ("", "has space", "-leading", "x" * 101, "line\nbreak"):
        async with sessionmaker() as session:
            with pytest.raises(InvalidPoolTokenName):
                await create_pool_token(session, name=bad, pool="gpu", actor="test")
    async with sessionmaker() as session:
        with pytest.raises(NotFound):
            await revoke_pool_token(session, "no-such", now=utcnow(), actor="test")
        with pytest.raises(NotFound):
            await revoke_pool_token(session, "has space", now=utcnow(), actor="test")


async def test_an_unknown_token_is_refused_like_any_bad_join_token(sessionmaker):
    async with sessionmaker() as session:
        with pytest.raises(Unauthorized) as refused:
            await register(session, request("not-a-token"), now=utcnow())
    assert refused.value.message == "the join token is not valid"


async def test_unknown_and_revoked_tokens_are_refused_identically(sessionmaker):
    plaintext = await pool_token(sessionmaker)
    async with sessionmaker() as session:
        await revoke_pool_token(session, "gpu-pods", now=utcnow(), actor="test")
        await session.commit()
    seen = []
    for presented in ("not-a-token", plaintext):
        async with sessionmaker() as session:
            with pytest.raises(Unauthorized) as refused:
                await register(session, request(presented), now=utcnow())
        seen.append((refused.value.status, refused.value.code, refused.value.message))
    assert seen[0] == seen[1]
    assert len(await followers(sessionmaker)) == 0


async def test_a_pool_token_is_looked_up_by_hash_and_never_written_to_the_audit_log(sessionmaker):
    plaintext = await pool_token(sessionmaker)
    await join(sessionmaker, plaintext)
    async with sessionmaker() as session:
        entries = (await session.scalars(select(AuditEntry))).all()
        stored = (await session.scalars(select(PoolToken))).one()
        rows = (await session.scalars(select(Follower))).all()
    assert stored.token_hash != plaintext and stored.token_hash == hash_secret(plaintext)
    for entry in entries:
        assert plaintext not in f"{entry.actor} {entry.action} {entry.subject_id} {entry.detail}"
    assert all(plaintext not in str(row.capabilities) for row in rows)


async def test_a_revoked_followers_row_is_never_resurrected_by_its_pool_token(sessionmaker):
    plaintext = await pool_token(sessionmaker)
    first, old = await join(sessionmaker, plaintext)
    async with sessionmaker() as session:
        await revoke_follower(session, first.id, now=utcnow(), actor="admin")
        await session.commit()
    second, _ = await join(sessionmaker, plaintext)
    assert second.id != first.id
    async with sessionmaker() as session:
        assert (await session.get(Follower, first.id)).state == "revoked"
        with pytest.raises(Forbidden):
            await authenticate(session, old, now=utcnow())


async def test_a_row_another_session_holds_locked_is_skipped_not_shared(sessionmaker):
    """Two real sessions: one holds the gone row's lock, a registration meanwhile must not
    take that row or wait for it."""
    plaintext = await pool_token(sessionmaker)
    first, _ = await join(sessionmaker, plaintext)
    async with sessionmaker() as session:
        (await session.get(Follower, first.id)).state = "gone"
        await session.commit()
    async with sessionmaker() as holder:
        locked = await holder.get(Follower, first.id, with_for_update=True)
        assert locked is not None
        joined, _ = await asyncio.wait_for(join(sessionmaker, plaintext), timeout=15)
        assert joined.id != first.id
        await holder.rollback()
    assert len(await followers(sessionmaker)) == 2


async def test_two_registrations_racing_for_one_gone_row_take_it_once(sessionmaker):
    plaintext = await pool_token(sessionmaker)
    first, _ = await join(sessionmaker, plaintext)
    async with sessionmaker() as session:
        (await session.get(Follower, first.id)).state = "gone"
        await session.commit()
    (a, ca), (b, cb) = await asyncio.gather(
        join(sessionmaker, plaintext), join(sessionmaker, plaintext)
    )
    assert a.id != b.id and ca != cb
    assert first.id in {a.id, b.id}
    async with sessionmaker() as session:
        assert (await authenticate(session, ca, now=utcnow())).id == a.id
        assert (await authenticate(session, cb, now=utcnow())).id == b.id


async def test_the_pool_comes_from_the_token_not_the_request(sessionmaker):
    plaintext = await pool_token(sessionmaker, pool="gpu")
    follower, _ = await join(sessionmaker, plaintext)  # the request claims pool="ignored"
    assert follower.pool == "gpu" and follower.capabilities["pool"] == "gpu"


async def test_a_case_variant_of_an_existing_name_is_refused(sessionmaker):
    await pool_token(sessionmaker, name="gpu-pods")
    async with sessionmaker() as session:
        with pytest.raises(Conflict) as refused:
            await create_pool_token(session, name="GPU-Pods", pool="gpu", actor="test")
    assert (refused.value.status, refused.value.code) == (409, "exists")


async def test_revoking_with_followers_survives_a_stale_job_call_in_flight(
    sessionmaker, factory, monkeypatch
):
    """The reviewer's deadlock: the revoke held one follower's job while waiting for another
    follower's row, and that follower's stale heartbeat waited for the job."""
    # The retry is a safety net; the lock order must make it unnecessary.
    monkeypatch.setattr(
        "swarmscribe_leader.auth.pool_tokens._is_lock_conflict", lambda exc: False
    )
    plaintext = await pool_token(sessionmaker, pool="default")
    one, _ = await join(sessionmaker, plaintext)
    two, _ = await join(sessionmaker, plaintext)
    low, high = sorted((one.id, two.id))
    job = await factory.job()
    async with sessionmaker() as session:
        holder = await session.get(Follower, low)
        claimed = await store.claim(session, holder, now=utcnow(), lease_seconds=120)
        assert claimed is not None
        await session.commit()
    lease_id = str(claimed.lease_id)

    async def revoke():
        async with sessionmaker() as session:
            result = await revoke_pool_token(
                session, "gpu-pods", now=utcnow(), actor="admin", revoke_followers=True
            )
            await session.commit()
            return result

    async with sessionmaker() as stale:
        # The other follower's request is in flight: it holds its own row.
        busy = await stale.get(Follower, high, with_for_update=True, populate_existing=True)
        task = asyncio.create_task(revoke())
        await asyncio.sleep(1)  # the revoke is now waiting for that row
        with pytest.raises(LeaderError):
            await store.heartbeat(
                stale, job.id, lease_id, busy, now=utcnow(), lease_seconds=120
            )
        await stale.rollback()
    _, revoked = await asyncio.wait_for(task, timeout=30)
    assert revoked == 2
    async with sessionmaker() as session:
        assert (await session.scalars(select(PoolToken))).one().revoked_at is not None
        states = {row.state for row in (await session.scalars(select(Follower))).all()}
        assert states == {"revoked"}
        assert (await session.get(Job, job.id)).state == "queued"


async def test_a_deadlocked_follower_pass_is_retried_and_the_token_stays_revoked(
    sessionmaker, monkeypatch
):
    from sqlalchemy.exc import DBAPIError
    from swarmscribe_leader.auth import pool_tokens

    class Deadlock(Exception):
        sqlstate = "40P01"

    plaintext = await pool_token(sessionmaker)
    await join(sessionmaker, plaintext)
    real = pool_tokens._revoke_followers_of
    calls = []

    async def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise DBAPIError("revoke", {}, Deadlock())
        return await real(*args, **kwargs)

    monkeypatch.setattr(pool_tokens, "_revoke_followers_of", flaky)
    async with sessionmaker() as session:
        token, revoked = await revoke_pool_token(
            session, "gpu-pods", now=utcnow(), actor="admin", revoke_followers=True
        )
        await session.commit()
    assert (len(calls), revoked, token.revoked_by) == (2, 1, "admin")
    async with sessionmaker() as session:
        assert (await session.scalars(select(PoolToken))).one().revoked_at is not None
