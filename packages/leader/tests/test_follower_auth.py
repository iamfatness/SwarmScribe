from datetime import timedelta

import pytest
from sqlalchemy import select
from swarmscribe_leader.auth.followers import authenticate, create_join_token, register
from swarmscribe_leader.auth.secrets import hash_secret, new_secret
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.models import AuditEntry, Follower, JoinToken
from swarmscribe_leader.errors import Conflict, Forbidden, Unauthorized
from swarmscribe_protocol import Capabilities, RegisterRequest


def request(token: str, *, protocol_version: int = 1) -> RegisterRequest:
    return RegisterRequest(
        join_token=token,
        protocol_version=protocol_version,
        capabilities=Capabilities(
            device="cpu", models=["distil-large-v3"], engine_version="0.1.0", pool="ignored"
        ),
    )


async def make_token(sessionmaker, **overrides) -> str:
    values = {
        "pool": "gpu",
        "expires_at": utcnow() + timedelta(days=1),
        "max_uses": 5,
        "created_by": "test",
    }
    values.update(overrides)
    async with sessionmaker() as session:
        _, plaintext = await create_join_token(session, **values)
        await session.commit()
    return plaintext


def test_secrets_are_long_random_and_hashed_with_sha256():
    first, second = new_secret(), new_secret()
    assert first != second
    assert len(first) >= 43
    assert len(hash_secret(first)) == 64
    assert hash_secret(first) == hash_secret(first)


async def test_join_token_is_stored_only_as_a_hash(sessionmaker):
    plaintext = await make_token(sessionmaker)
    async with sessionmaker() as session:
        stored = (await session.scalars(select(JoinToken))).one()
    assert stored.token_hash == hash_secret(plaintext)
    assert plaintext not in stored.token_hash


async def test_register_creates_a_follower_in_the_token_pool(sessionmaker):
    plaintext = await make_token(sessionmaker, pool="gpu")
    async with sessionmaker() as session:
        follower, credential = await register(session, request(plaintext), now=utcnow())
        await session.commit()
    async with sessionmaker() as session:
        stored = await session.get(Follower, follower.id)
        token = (await session.scalars(select(JoinToken))).one()
        audit = (await session.scalars(select(AuditEntry))).all()
    assert stored.pool == "gpu"
    assert stored.state == "active"
    assert stored.capabilities["device"] == "cpu"
    assert stored.credential_hash == hash_secret(credential)
    assert token.uses == 1
    assert sorted(a.action for a in audit) == ["follower.register", "token.create"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"revoked": True},
        {"expires_at": utcnow() - timedelta(seconds=1)},
        {"max_uses": 0},
    ],
    ids=["revoked", "expired", "used-up"],
)
async def test_register_rejects_an_unusable_token(sessionmaker, overrides):
    revoked = overrides.pop("revoked", False)
    plaintext = await make_token(sessionmaker, **overrides)
    if revoked:
        async with sessionmaker() as session:
            token = (await session.scalars(select(JoinToken))).one()
            token.revoked = True
            await session.commit()
    async with sessionmaker() as session:
        with pytest.raises(Unauthorized):
            await register(session, request(plaintext), now=utcnow())


async def test_register_rejects_an_unknown_token(sessionmaker):
    async with sessionmaker() as session:
        with pytest.raises(Unauthorized):
            await register(session, request("not-a-token"), now=utcnow())


async def test_a_single_use_token_works_once(sessionmaker):
    plaintext = await make_token(sessionmaker, max_uses=1)
    async with sessionmaker() as session:
        await register(session, request(plaintext), now=utcnow())
        await session.commit()
    async with sessionmaker() as session:
        with pytest.raises(Unauthorized):
            await register(session, request(plaintext), now=utcnow())


async def test_a_protocol_mismatch_is_rejected_without_using_the_token(sessionmaker):
    plaintext = await make_token(sessionmaker)
    async with sessionmaker() as session:
        with pytest.raises(Conflict, match="protocol"):
            await register(session, request(plaintext, protocol_version=2), now=utcnow())
        await session.commit()
    async with sessionmaker() as session:
        assert (await session.scalars(select(JoinToken))).one().uses == 0


async def test_authenticate_finds_the_follower_and_marks_it_seen(sessionmaker, factory):
    follower, credential = await factory.follower(last_seen_at=utcnow() - timedelta(hours=1))
    now = utcnow()
    async with sessionmaker() as session:
        found = await authenticate(session, credential, now=now)
        await session.commit()
    assert found.id == follower.id
    async with sessionmaker() as session:
        assert (await session.get(Follower, follower.id)).last_seen_at == now


async def test_authenticate_rejects_an_unknown_credential(sessionmaker):
    async with sessionmaker() as session:
        with pytest.raises(Unauthorized):
            await authenticate(session, "nope", now=utcnow())


async def test_authenticate_refuses_a_revoked_follower(sessionmaker, factory):
    _, credential = await factory.follower(state="revoked")
    async with sessionmaker() as session:
        with pytest.raises(Forbidden):
            await authenticate(session, credential, now=utcnow())


async def test_a_gone_follower_that_returns_is_active_again(sessionmaker, factory):
    _, credential = await factory.follower(state="gone")
    async with sessionmaker() as session:
        found = await authenticate(session, credential, now=utcnow())
    assert found.state == "active"
