"""Server-side sessions (fleet console spec 5.2).

The browser holds only a random session id in an HttpOnly, Secure, SameSite=Strict cookie; the
database holds its SHA-256, so a copy of the table cannot be replayed as a cookie. A session
ends 8 hours after sign-in or after 1 hour without a request, whichever comes first; activity
is written at most once a minute. Nothing here keeps a person's ID, access or refresh token."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import delete, or_, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response
from swarmscribe_leader.auth.secrets import hash_secret, new_secret

from .db.models import ConsoleSession, LoginAttempt

SESSION_COOKIE = "__Host-swarmscribe-session"
LOGIN_COOKIE = "__Host-swarmscribe-login"
TOUCH_EVERY = timedelta(seconds=60)
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")


def is_token(value: str | None) -> bool:
    """Whether `value` has new_secret()'s shape; anything else is refused before hashing."""
    return bool(value) and _TOKEN.fullmatch(value) is not None


@dataclass(frozen=True)
class SignedIn:
    session_id: str = field(repr=False)
    provider: str
    issuer: str
    subject: str
    email: str | None
    principals: frozenset[str]
    created_at: datetime
    expires_at: datetime
    idle_expires_at: datetime

    @property
    def actor(self) -> str:
        """How the audit log names this person: the leader's Identity.actor format."""
        return f"{self.email or 'unknown'} ({self.issuer} {self.subject})"


async def create_session(
    session: AsyncSession,
    *,
    provider: str,
    issuer: str,
    subject: str,
    email: str | None,
    principals: frozenset[str],
    now: datetime,
    lifetime: timedelta,
) -> str:
    session_id = new_secret()
    session.add(
        ConsoleSession(
            id_hash=hash_secret(session_id),
            provider=provider,
            issuer=issuer,
            subject=subject,
            email=email,
            principals=sorted(principals),
            created_at=now,
            last_seen_at=now,
            expires_at=now + lifetime,
        )
    )
    return session_id


async def find_session(
    session: AsyncSession, cookie: str | None, *, now: datetime, idle: timedelta
) -> SignedIn | None:
    if not is_token(cookie):
        return None
    row = await session.get(ConsoleSession, hash_secret(cookie))
    if row is None:
        return None
    if now >= row.expires_at or now >= row.last_seen_at + idle:
        await session.execute(
            delete(ConsoleSession).where(ConsoleSession.id_hash == row.id_hash)
        )
        return None
    last_seen_at = row.last_seen_at
    if now - last_seen_at >= TOUCH_EVERY:
        # A statement, not an ORM attribute write: if a logout or expiry deleted the row since
        # it was read, nothing matches and the person is signed out (no StaleDataError/500).
        touched = await session.execute(
            update(ConsoleSession)
            .where(ConsoleSession.id_hash == row.id_hash)
            .values(last_seen_at=now)
            .execution_options(synchronize_session=False)
        )
        if touched.rowcount == 0:
            return None
        last_seen_at = now
    return SignedIn(
        session_id=cookie,
        provider=row.provider,
        issuer=row.issuer,
        subject=row.subject,
        email=row.email,
        principals=frozenset(row.principals),
        created_at=row.created_at,
        expires_at=row.expires_at,
        idle_expires_at=min(last_seen_at + idle, row.expires_at),
    )


async def end_session(session: AsyncSession, cookie: str | None) -> None:
    if is_token(cookie):
        await session.execute(
            delete(ConsoleSession).where(ConsoleSession.id_hash == hash_secret(cookie))
        )


async def prune_expired(session: AsyncSession, *, now: datetime, idle: timedelta) -> int:
    """Delete ended sessions and abandoned sign-ins. Returns how many rows went."""
    ended = await session.execute(
        delete(ConsoleSession).where(
            or_(ConsoleSession.expires_at <= now, ConsoleSession.last_seen_at <= now - idle)
        )
    )
    abandoned = await session.execute(delete(LoginAttempt).where(LoginAttempt.expires_at <= now))
    return ended.rowcount + abandoned.rowcount


def set_session_cookie(response: Response, value: str, max_age: int) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        value,
        max_age=max_age,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="strict")


def set_login_cookie(response: Response, value: str, max_age: int) -> None:
    # Lax, not Strict: the identity provider's redirect back is a cross-site navigation, on
    # which a Strict cookie would not be sent.
    response.set_cookie(
        LOGIN_COOKIE, value, max_age=max_age, path="/", secure=True, httponly=True, samesite="lax"
    )


def clear_login_cookie(response: Response) -> None:
    response.delete_cookie(LOGIN_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
