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
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..db.models import ConsoleCredential
from ..errors import Conflict, InvalidToken, LeaderError, NotFound
from .roles import RANK, Role
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
    be audited (an unknown credential names no one). Its code, credential_revoked, tells
    the console to stop calling until its administrator replaces the credential; only the
    holder of the real credential can see it, so it reveals nothing to a guesser."""

    code = "credential_revoked"

    def __init__(self, console: str):
        super().__init__("this console credential has been revoked")
        self.console = console


def console_actor(name: str) -> str:
    """The audit actor of a console's own refusal (no person is involved): `console <name>`.
    A request that does name a person is `<person> via console <name>` (DelegatedActor)."""
    return f"console {name}"


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


# --- who a console acts for -------------------------------------------------------------

ACTOR_HEADER = "x-swarmscribe-actor"
ROLE_HEADER = "x-swarmscribe-actor-role"
POLLER_ACTOR = "system:poller"
MAX_ISSUER_CHARS = 255  # OIDC limits sub to 255 ASCII characters; issuers are kept as short
MAX_SUBJECT_CHARS = 255
MAX_EMAIL_CHARS = 254  # RFC 5321
_VISIBLE_ASCII = re.compile(r"[\x21-\x7e]+")
_EMAIL = re.compile(r"[^@]+@[^@]+")


class InvalidActor(LeaderError):
    status = 400
    code = "invalid_actor"


class InvalidActorRole(LeaderError):
    status = 400
    code = "invalid_actor_role"


@dataclass(frozen=True)
class DelegatedActor:
    """The person a console acts for, or (issuer None) the console's own poller."""

    issuer: str | None
    subject: str | None
    email: str | None

    @property
    def is_poller(self) -> bool:
        return self.issuer is None

    @property
    def name(self) -> str:
        """As the audit log names a signed-in person (Identity.actor), or system:poller."""
        if self.issuer is None:
            return POLLER_ACTOR
        return f"{self.email or 'unknown'} ({self.issuer} {self.subject})"

    def audit_name(self, console: str) -> str:
        """The one place the audit actor of a console request is built."""
        return f"{self.name} via console {console}"


POLLER = DelegatedActor(issuer=None, subject=None, email=None)


def _single(values: list[str], header: str, error: type[LeaderError]) -> str:
    if not values:
        raise error(f"{header} is required on a console request")
    if len(values) > 1:
        raise error(f"send {header} once")
    return values[0]


def parse_delegation(
    actor_values: list[str], role_values: list[str]
) -> tuple[DelegatedActor, Role]:
    """Who a console says it acts for, and the role it asserts for them, from every value
    of X-SwarmScribe-Actor and X-SwarmScribe-Actor-Role. Only visible ASCII passes, so
    nothing that reaches the audit log can carry a line break or a control character.
    Messages are fixed text: header values are never echoed, logged or audited."""
    role = _single(role_values, "X-SwarmScribe-Actor-Role", InvalidActorRole)
    if role not in RANK:
        raise InvalidActorRole("X-SwarmScribe-Actor-Role must be viewer, operator or admin")
    value = _single(actor_values, "X-SwarmScribe-Actor", InvalidActor)
    if value == POLLER_ACTOR:
        if role != "viewer":
            raise InvalidActorRole("the console's poller acts as viewer only")
        return POLLER, "viewer"
    parts = value.split(" ")
    if len(parts) != 3 or not all(_VISIBLE_ASCII.fullmatch(part) for part in parts):
        raise InvalidActor(
            "X-SwarmScribe-Actor must be `<issuer> <subject> <email>` in printable ASCII, "
            "separated by single spaces (email - when unknown), or system:poller"
        )
    issuer, subject, email = parts
    if (
        len(issuer) > MAX_ISSUER_CHARS
        or len(subject) > MAX_SUBJECT_CHARS
        or len(email) > MAX_EMAIL_CHARS
    ):
        raise InvalidActor(
            f"X-SwarmScribe-Actor is too long: the issuer and subject may have at most "
            f"{MAX_ISSUER_CHARS} characters each and the email {MAX_EMAIL_CHARS}"
        )
    if not issuer.startswith("https://") or issuer == "https://":
        raise InvalidActor("the actor's issuer must be an https:// URL")
    if email != "-" and not _EMAIL.fullmatch(email):
        raise InvalidActor("the actor's email must be one address, or - when unknown")
    person = DelegatedActor(
        issuer=issuer, subject=subject, email=None if email == "-" else email.lower()
    )
    return person, role


def effective_role(asserted: Role, cap: str) -> Role:
    """The lower of the role a console asserts and the role its credential is capped at."""
    return asserted if RANK[asserted] <= RANK[cap] else cap
