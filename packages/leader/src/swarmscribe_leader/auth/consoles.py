"""Fleet console credentials. A leader administrator creates one per console, with a role
cap; the console calls /v1/admin with it on behalf of a person (api/admin_auth.py).

A credential is 32 random bytes (URL-safe base64, 43 characters), shown once and stored only
as its SHA-256, like a follower credential. It is found by looking its hash up, so the
plaintext is never compared with anything and a guess that shares a prefix with a real
credential is answered exactly like any other guess. Names are unique ignoring case and are
never reused, even after a revocation, because the audit log names consoles by name. Each
change is audited; the caller commits.
"""

import re
import uuid
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..db.models import ConsoleCredential
from ..errors import Conflict, InvalidToken, LeaderError, NotFound
from .roles import Role
from .secrets import hash_secret, new_secret

# Serialises console creation, so that two at once cannot both pass the name check.
_CREATE_LOCK = 7_204_511_002
# What new_secret() makes. Anything else is refused before it is hashed or looked up.
_CREDENTIAL = re.compile(r"[A-Za-z0-9_-]{43}")
# Console names appear in audit actors ("... via console <name>"), so they are restricted to
# characters that cannot forge or break an audit line. Same rule as the API's NAME_PATTERN.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")


class InvalidConsoleName(LeaderError):
    """A console name outside [A-Za-z0-9][A-Za-z0-9._-]{0,99}: 422."""

    status = 422
    code = "invalid_name"


class InvalidConsoleCredential(InvalidToken):
    """An unknown, malformed or revoked console credential: 401 with
    `WWW-Authenticate: Console error="invalid_token"`."""

    scheme = "Console"


class RevokedConsoleCredential(InvalidConsoleCredential):
    """A revoked console's credential. Carries the console's name, so that the refusal can
    be audited (an unknown credential names no one)."""

    def __init__(self, console: str):
        super().__init__("this console credential has been revoked")
        self.console = console


def _exists(name: str) -> Conflict:
    return Conflict(
        f"a console named {name!r} already exists; console names are never reused",
        code="exists",
    )


async def create_console(
    session: AsyncSession, *, name: str, max_role: Role, actor: str
) -> tuple[ConsoleCredential, str]:
    """A new console credential capped at `max_role`. Returns the row and the plaintext,
    which is kept nowhere."""
    if not _NAME.fullmatch(name):
        raise InvalidConsoleName(
            "a console name starts with a letter or digit and holds only letters, digits,"
            " '.', '_' and '-' (at most 100 characters)"
        )
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _CREATE_LOCK})
    taken = await session.scalar(
        select(ConsoleCredential.name).where(func.lower(ConsoleCredential.name) == name.lower())
    )
    if taken is not None:
        raise _exists(taken)
    plaintext = new_secret()
    console = ConsoleCredential(
        id=uuid.uuid4(),
        name=name,
        credential_hash=hash_secret(plaintext),
        max_role=max_role,
        created_by=actor,
    )
    session.add(console)
    try:
        # The unique index on lower(name) is the last word if an insert got past the check.
        await session.flush()
    except IntegrityError as exc:
        raise _exists(name) from exc
    audit.record(
        session,
        actor=actor,
        action="console.create",
        subject_type="console_credential",
        subject_id=console.id,
        detail={"name": name, "max_role": max_role},
    )
    return console, plaintext


async def revoke_console(
    session: AsyncSession, name: str, *, now: datetime, actor: str
) -> ConsoleCredential:
    """Refuse the console's credential from its next request on. Revoking again keeps the
    first revocation's time and administrator; every call is audited."""
    if not _NAME.fullmatch(name):
        raise NotFound("no console with that name")
    console = await session.scalar(
        select(ConsoleCredential)
        .where(func.lower(ConsoleCredential.name) == name.lower())
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if console is None:
        raise NotFound(f"no console named {name!r}")
    if console.revoked_at is None:
        console.revoked_at = now
        console.revoked_by = actor
    audit.record(
        session,
        actor=actor,
        action="console.revoke",
        subject_type="console_credential",
        subject_id=console.id,
        detail={"name": console.name},
    )
    return console


async def authenticate_console(session: AsyncSession, credential: str) -> ConsoleCredential:
    """The console holding `credential`. Nothing is cached, so a revocation applies to the
    console's next request; nothing is locked, so a console's requests never wait on each
    other."""
    if not _CREDENTIAL.fullmatch(credential):
        raise InvalidConsoleCredential("unknown console credential")
    console = await session.scalar(
        select(ConsoleCredential).where(
            ConsoleCredential.credential_hash == hash_secret(credential)
        )
    )
    if console is None:
        raise InvalidConsoleCredential("unknown console credential")
    if console.revoked_at is not None:
        raise RevokedConsoleCredential(console.name)
    return console
