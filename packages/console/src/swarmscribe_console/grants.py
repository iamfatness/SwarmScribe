"""Console roles (fleet console spec 5.2).

A person's role for a leader is the highest grant whose scope matches the leader — by name,
by one of its labels, or `all` — among the grants held by any of the person's principals
(Entra group ids, Google groups, emails, domains; see principals.py). Console administrators
manage the registry and the grants; that gives them no leader role.

Scopes are stored canonically: `all`, `leader:<lowercase name>`, `label:<key>=<value>`. Label
keys cannot contain `=`, so a scope splits at its first `=` and a value may contain more."""

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import exists, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from swarmscribe_leader.auth.roles import RANK, Role, highest

from .db.models import ConsoleAdmin, RoleGrant
from .errors import Invalid

logger = logging.getLogger(__name__)

PRINCIPAL_KINDS = ("entra_group", "google_group", "email", "domain")
LEADER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
LABEL_KEY = re.compile(r"[a-z0-9][a-z0-9._-]{0,62}")
LABEL_VALUE = re.compile(r"[!-~]{1,255}")
_VISIBLE = re.compile(r"[!-~]+")
_GUID = re.compile(
    r"\{?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\}?", re.IGNORECASE
)
MAX_ADDRESS = 254
_DNS_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
_SCOPE_HELP = (
    "a scope is all, leader:<name>, or label:<key>=<value> (key: lowercase letters, digits "
    "and . _ -; value: 1 to 255 printable characters without spaces)"
)


@dataclass(frozen=True)
class Scope:
    kind: Literal["all", "leader", "label"]
    leader: str | None = None
    key: str | None = None
    value: str | None = None

    def __str__(self) -> str:
        if self.kind == "all":
            return "all"
        if self.kind == "leader":
            return f"leader:{self.leader}"
        return f"label:{self.key}={self.value}"

    def matches(self, leader_name: str, labels: Mapping[str, Any]) -> bool:
        if self.kind == "all":
            return True
        if self.kind == "leader":
            # ASCII only: str.lower() folds look-alikes (the Kelvin sign lowers to "k").
            return leader_name.isascii() and leader_name.lower() == self.leader
        return labels.get(self.key) == self.value


def parse_scope(text: str) -> Scope:
    if text == "all":
        return Scope("all")
    kind, sep, rest = text.partition(":")
    if sep and kind == "leader" and LEADER_NAME.fullmatch(rest):
        return Scope("leader", leader=rest.lower())
    if sep and kind == "label":
        key, eq, value = rest.partition("=")
        if eq and LABEL_KEY.fullmatch(key) and LABEL_VALUE.fullmatch(value):
            return Scope("label", key=key, value=value)
    raise Invalid(_SCOPE_HELP, code="invalid_scope")


def _is_domain(name: str) -> bool:
    labels = name.split(".")
    return (
        len(name) <= 253
        and len(labels) >= 2
        and all(_DNS_LABEL.fullmatch(label) for label in labels)
        and labels[-1][0].isalpha()
    )


def _bad_principal(message: str) -> Invalid:
    return Invalid(message, code="invalid_principal")


def normalize_principal(kind: str, value: str) -> str:
    """The canonical stored form of a grant's or console admin's principal. Only ASCII
    passes, so a Unicode look-alike can never be stored next to the real name."""
    if kind not in PRINCIPAL_KINDS:
        raise _bad_principal("principal_kind is entra_group, google_group, email or domain")
    if not value.isascii():
        raise _bad_principal("principals are ASCII")
    text = value.strip(" ")  # only spaces: a stray newline or tab is a refusal, not trimmed
    if kind == "entra_group":
        guid = _GUID.fullmatch(text)
        if guid is None:
            raise _bad_principal("an Entra ID group is its object ID (a GUID)")
        return guid.group(1).lower()
    text = text.lower()
    if kind == "domain":
        text = text.removeprefix("@")
        if not _is_domain(text):
            raise _bad_principal("a domain is a DNS name such as example.org")
        return text
    local, at, domain = text.partition("@")
    if not (
        len(text) <= MAX_ADDRESS
        and local
        and at
        and _VISIBLE.fullmatch(local)
        and _is_domain(domain)
    ):
        raise _bad_principal("an email or Google group is one address such as a@example.org")
    return text


def principal_key(kind: str, value: str) -> str:
    return f"{kind}:{value}"


def _pairs(principals: Iterable[str]) -> list[tuple[str, str]]:
    found = set()
    for item in principals:
        kind, sep, value = item.partition(":")
        if sep and kind in PRINCIPAL_KINDS and value:
            found.add((kind, value))
    return sorted(found)


def role_for(
    held: Iterable[tuple[str, Scope]], leader_name: str, labels: Mapping[str, Any]
) -> Role | None:
    return highest(
        role for role, scope in held if role in RANK and scope.matches(leader_name, labels)
    )


async def grants_held(
    session: AsyncSession, principals: Iterable[str]
) -> list[tuple[str, Scope]]:
    """Every (role, scope) granted to any of these principals. A stored scope that no longer
    parses (edited by hand) is skipped with a warning rather than failing the request."""
    pairs = _pairs(principals)
    if not pairs:
        return []
    rows = (
        await session.execute(
            select(RoleGrant.role, RoleGrant.scope).where(
                tuple_(RoleGrant.principal_kind, RoleGrant.principal).in_(pairs)
            )
        )
    ).all()
    held: list[tuple[str, Scope]] = []
    for role, scope in rows:
        if role not in RANK:
            logger.warning("a stored role grant has an unknown role and was skipped")
            continue
        try:
            held.append((role, parse_scope(scope)))
        except Invalid:
            logger.warning("a stored role grant has an unreadable scope and was skipped")
    return held


async def is_console_admin(session: AsyncSession, principals: Iterable[str]) -> bool:
    pairs = _pairs(principals)
    if not pairs:
        return False
    return bool(
        await session.scalar(
            select(
                exists().where(
                    tuple_(ConsoleAdmin.principal_kind, ConsoleAdmin.principal).in_(pairs)
                )
            )
        )
    )


async def has_any_access(session: AsyncSession, principals: Iterable[str]) -> bool:
    """Whether a person may hold a session at all: some grant, or console administration."""
    principals = list(principals)
    return bool(await grants_held(session, principals)) or await is_console_admin(
        session, principals
    )
