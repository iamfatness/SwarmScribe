"""Pool tokens: what a pool of followers that come and go by themselves registers with.

A join token expires (90 days at most) and has a limited number of uses, which suits a
machine a person sets up. A Kubernetes pool registers a follower every time a pod starts,
for as long as the pool exists, so its token must not run out on a calendar: a pool token
has no expiry and no limit on uses. It is therefore the more dangerous secret, and is
handled like a console credential: named, shown once, stored only as its SHA-256, created
and revoked only by an administrator signed in as a person, never through a console.
Names are never reused, because the audit log names pool tokens by name.
"""

import re
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..db.models import Follower, PoolToken
from ..errors import Conflict, LeaderError, NotFound
from .followers import revoke_follower
from .secrets import hash_secret, new_secret

# The API's NAME_PATTERN: nothing that could forge or break an audit line.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")


class InvalidPoolTokenName(LeaderError):
    status = 422
    code = "invalid_name"


def _exists(name: str) -> Conflict:
    return Conflict(
        f"a pool token named {name!r} already exists; pool token names are never reused",
        code="exists",
    )


async def create_pool_token(
    session: AsyncSession, *, name: str, pool: str, actor: str
) -> tuple[PoolToken, str]:
    """A new pool token for `pool`. Returns the row and the plaintext, kept nowhere."""
    if not _NAME.fullmatch(name):
        raise InvalidPoolTokenName(
            "a pool token name starts with a letter or digit and holds only letters, digits,"
            " '.', '_' and '-' (at most 100 characters)"
        )
    if await session.scalar(select(PoolToken.id).where(PoolToken.name == name)) is not None:
        raise _exists(name)
    plaintext = new_secret()
    token = PoolToken(
        id=uuid.uuid4(),
        name=name,
        token_hash=hash_secret(plaintext),
        pool=pool,
        created_by=actor,
        registrations=0,
    )
    session.add(token)
    try:
        # The unique constraint has the last word if two creations passed the check at once.
        await session.flush()
    except IntegrityError as exc:
        raise _exists(name) from exc
    audit.record(
        session,
        actor=actor,
        action="pool_token.create",
        subject_type="pool_token",
        subject_id=token.id,
        detail={"name": name, "pool": pool},
    )
    return token, plaintext


async def revoke_pool_token(
    session: AsyncSession,
    name: str,
    *,
    now: datetime,
    actor: str,
    revoke_followers: bool = False,
) -> tuple[PoolToken, int]:
    """Refuse every further registration with the token. With `revoke_followers`, every
    follower it registered is revoked too (what a leaked token needs). Revoking again keeps
    the first revocation's time and administrator. Returns the token and how many followers
    were revoked."""
    if not _NAME.fullmatch(name):
        raise NotFound("no pool token with that name")
    token = await session.scalar(
        select(PoolToken)
        .where(PoolToken.name == name)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if token is None:
        raise NotFound(f"no pool token named {name!r}")
    if token.revoked_at is None:
        token.revoked_at = now
        token.revoked_by = actor
    revoked = 0
    if revoke_followers:
        follower_ids = (
            await session.scalars(
                select(Follower.id)
                .where(Follower.pool_token_id == token.id, Follower.state != "revoked")
                .order_by(Follower.id)
            )
        ).all()
        for follower_id in follower_ids:
            await revoke_follower(session, follower_id, now=now, actor=actor)
            revoked += 1
    audit.record(
        session,
        actor=actor,
        action="pool_token.revoke",
        subject_type="pool_token",
        subject_id=token.id,
        detail={"name": token.name, "followers_revoked": revoked},
    )
    return token, revoked
