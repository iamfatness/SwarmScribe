import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from swarmscribe_protocol import PROTOCOL_VERSION, RegisterRequest

from .. import audit
from ..db.models import Follower, Job, JoinToken, PoolToken
from ..errors import Conflict, Forbidden, InvalidToken, NotFound
from ..jobs.store import release_all
from .secrets import hash_secret, new_secret


async def create_join_token(
    session: AsyncSession, *, pool: str, expires_at: datetime, max_uses: int, created_by: str
) -> tuple[JoinToken, str]:
    plaintext = new_secret()
    token = JoinToken(
        id=uuid.uuid4(),
        token_hash=hash_secret(plaintext),
        pool=pool,
        expires_at=expires_at,
        max_uses=max_uses,
        uses=0,
        revoked=False,
        created_by=created_by,
    )
    session.add(token)
    audit.record(
        session,
        actor=created_by,
        action="token.create",
        subject_type="join_token",
        subject_id=token.id,
        detail={"pool": pool, "max_uses": max_uses},
    )
    return token, plaintext


async def register(
    session: AsyncSession, request: RegisterRequest, *, now: datetime
) -> tuple[Follower, str]:
    if request.protocol_version != PROTOCOL_VERSION:
        raise Conflict(
            f"protocol version {request.protocol_version} is not supported; "
            f"this leader speaks {PROTOCOL_VERSION}",
            code="protocol_version",
        )
    token_hash = hash_secret(request.join_token)
    token = await session.scalar(
        select(JoinToken).where(JoinToken.token_hash == token_hash).with_for_update()
    )
    if token is None:
        return await _register_with_pool_token(session, request, token_hash, now=now)
    if token.revoked or token.expires_at <= now or token.uses >= token.max_uses:
        raise InvalidToken("the join token is not valid")
    token.uses += 1
    credential = new_secret()
    follower = Follower(
        id=uuid.uuid4(),
        pool=token.pool,
        capabilities=_capabilities(request, token.pool),
        credential_hash=hash_secret(credential),
        state="active",
        last_seen_at=now,
        join_token_id=token.id,
    )
    session.add(follower)
    audit.record(
        session,
        actor=f"follower:{follower.id}",
        action="follower.register",
        subject_type="follower",
        subject_id=follower.id,
        detail={"pool": token.pool, "device": request.capabilities.device},
    )
    return follower, credential


def _capabilities(request: RegisterRequest, pool: str) -> dict[str, Any]:
    """What the follower reported, with the pool the token put it in (the one that counts)."""
    return {**request.capabilities.model_dump(), "pool": pool}


async def _gone_follower_of(session: AsyncSession, pool_token: PoolToken) -> Follower | None:
    """A follower this pool token registered that is gone and holds nothing: its row is
    given to the next registration, so machines that come and go do not grow the table."""
    holding = select(Job.leased_by).where(Job.state == "leased", Job.leased_by.is_not(None))
    return await session.scalar(
        select(Follower)
        .where(
            Follower.pool_token_id == pool_token.id,
            Follower.state == "gone",
            Follower.id.not_in(holding),
        )
        .order_by(Follower.last_seen_at, Follower.id)
        .limit(1)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )


async def _register_with_pool_token(
    session: AsyncSession, request: RegisterRequest, token_hash: str, *, now: datetime
) -> tuple[Follower, str]:
    pool_token = await session.scalar(
        select(PoolToken).where(PoolToken.token_hash == token_hash).with_for_update()
    )
    if pool_token is None or pool_token.revoked_at is not None:
        raise InvalidToken("the join token is not valid")
    pool_token.registrations += 1
    pool_token.last_used_at = now
    credential = new_secret()
    follower = await _gone_follower_of(session, pool_token)
    reused = follower is not None
    if follower is None:
        follower = Follower(id=uuid.uuid4(), pool=pool_token.pool, pool_token_id=pool_token.id)
        session.add(follower)
    # A reused row's old credential stops working here: its hash is replaced.
    follower.capabilities = _capabilities(request, pool_token.pool)
    follower.credential_hash = hash_secret(credential)
    follower.state = "active"
    follower.last_seen_at = now
    audit.record(
        session,
        actor=f"follower:{follower.id}",
        action="follower.register",
        subject_type="follower",
        subject_id=follower.id,
        detail={
            "pool": pool_token.pool,
            "device": request.capabilities.device,
            "pool_token": pool_token.name,
            "reused": reused,
        },
    )
    return follower, credential


async def authenticate(session: AsyncSession, credential: str, *, now: datetime) -> Follower:
    follower = await session.scalar(
        select(Follower)
        .where(Follower.credential_hash == hash_secret(credential))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if follower is None:
        raise InvalidToken("unknown follower credential")
    if follower.state == "revoked":
        raise Forbidden("this follower has been revoked")
    if follower.state == "gone":
        follower.state = "active"
    follower.last_seen_at = now
    return follower


async def _locked_follower(session: AsyncSession, follower_id: uuid.UUID) -> Follower:
    """The follower row first, then its jobs by id: the order every path that locks both
    (deregister, submit, heartbeat, the reaper, the scanner) uses."""
    follower = await session.get(
        Follower, follower_id, with_for_update=True, populate_existing=True
    )
    if follower is None:
        raise NotFound("no such follower")
    return follower


async def drain(session: AsyncSession, follower_id: uuid.UUID, *, actor: str) -> Follower:
    """The follower finishes what it holds and takes nothing new."""
    follower = await _locked_follower(session, follower_id)
    moved = await session.execute(
        update(Follower)
        .where(Follower.id == follower.id, Follower.state != "revoked")
        .values(state="draining")
    )
    if moved.rowcount == 0:
        raise Conflict("a revoked follower cannot be drained", code="revoked")
    await session.refresh(follower)
    audit.record(
        session,
        actor=actor,
        action="follower.drain",
        subject_type="follower",
        subject_id=follower.id,
    )
    return follower


async def revoke_follower(
    session: AsyncSession, follower_id: uuid.UUID, *, now: datetime, actor: str
) -> tuple[Follower, int]:
    """Every further call from the follower is refused, and its leases are released at once
    (so its upload links stop working; the attempts are not counted). Returns the follower
    and the number of leases released."""
    follower = await _locked_follower(session, follower_id)
    released = await release_all(session, follower, now=now)
    await session.execute(
        update(Follower)
        .where(Follower.id == follower.id, Follower.state != "revoked")
        .values(state="revoked")
    )
    await session.refresh(follower)
    audit.record(
        session,
        actor=actor,
        action="follower.revoke",
        subject_type="follower",
        subject_id=follower.id,
        detail={"released": released},
    )
    return follower, released


async def revoke_token(session: AsyncSession, token_id: uuid.UUID, *, actor: str) -> JoinToken:
    token = await session.get(JoinToken, token_id, with_for_update=True, populate_existing=True)
    if token is None:
        raise NotFound("no such join token")
    token.revoked = True
    audit.record(
        session,
        actor=actor,
        action="token.revoke",
        subject_type="join_token",
        subject_id=token.id,
    )
    return token
