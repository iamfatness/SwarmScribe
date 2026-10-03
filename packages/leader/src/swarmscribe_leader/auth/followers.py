import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from swarmscribe_protocol import PROTOCOL_VERSION, RegisterRequest

from .. import audit
from ..db.models import Follower, JoinToken
from ..errors import Conflict, Forbidden, Unauthorized
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
    token = await session.scalar(
        select(JoinToken)
        .where(JoinToken.token_hash == hash_secret(request.join_token))
        .with_for_update()
    )
    if token is None or token.revoked or token.expires_at <= now or token.uses >= token.max_uses:
        raise Unauthorized("the join token is not valid")
    token.uses += 1
    credential = new_secret()
    follower = Follower(
        id=uuid.uuid4(),
        pool=token.pool,
        capabilities=request.capabilities.model_dump(),
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


async def authenticate(session: AsyncSession, credential: str, *, now: datetime) -> Follower:
    follower = await session.scalar(
        select(Follower).where(Follower.credential_hash == hash_secret(credential))
    )
    if follower is None:
        raise Unauthorized("unknown follower credential")
    if follower.state == "revoked":
        raise Forbidden("this follower has been revoked")
    if follower.state == "gone":
        follower.state = "active"
    follower.last_seen_at = now
    return follower
