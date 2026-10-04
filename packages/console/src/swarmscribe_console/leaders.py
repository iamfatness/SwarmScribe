"""The leader registry (fleet console spec 5.1).

Console administrators add a leader with its https URL and the console credential a leader
administrator created (`swarmscribe-admin console create`), label it, enable or disable it,
replace the credential in place (rotation, C1 spec amendment 3) and remove it. Credentials
are sealed with AES-GCM before they reach the database and never leave it through the API.

Leader URLs (spec 7: https only) are also an SSRF surface: the console sends its credential
to them. Loopback, link-local (cloud metadata), unspecified, multicast and reserved
addresses, localhost names and numeric host spellings are refused; private addresses are
allowed because leaders are usually internal and only console administrators register them."""

import ipaddress
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from . import audit
from .crypto import ConsoleKeys
from .db.models import Leader
from .errors import Conflict, Invalid, NotFound
from .grants import LABEL_KEY, LABEL_VALUE, LEADER_NAME

MAX_LABELS = 32
MAX_URL_CHARS = 2000
# Serialises registrations, so that two at once cannot both pass the name check.
_REGISTRY_LOCK = 0x53430010
_CREDENTIAL = re.compile(r"[A-Za-z0-9_-]{43}")
_BLOCKED_HOSTS = frozenset({"localhost", "metadata", "metadata.google.internal"})
# Cloud metadata services that are not link-local or otherwise caught by the range checks.
_BLOCKED_ADDRESSES = frozenset(
    ipaddress.ip_address(a) for a in ("fd00:ec2::254", "100.100.100.200")
)
_HOST_LABEL = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
_PATH = re.compile(r"(/[A-Za-z0-9._~-]+)*")


def _bad_url(message: str) -> Invalid:
    return Invalid(message, code="invalid_url")


def validate_name(name: str) -> str:
    if not isinstance(name, str) or not LEADER_NAME.fullmatch(name):
        raise Invalid(
            "a leader name starts with a letter or digit and holds only letters, digits,"
            " '.', '_' and '-' (at most 100 characters)",
            code="invalid_name",
        )
    return name


def _check_host(host: str) -> None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        candidate = getattr(address, "ipv4_mapped", None) or address
        if (
            candidate in _BLOCKED_ADDRESSES
            or candidate.is_loopback
            or candidate.is_link_local
            or candidate.is_unspecified
            or candidate.is_multicast
            or candidate.is_reserved
        ):
            raise _bad_url(
                "a leader URL cannot use a loopback, link-local, cloud metadata, "
                "multicast or reserved address"
            )
        return
    labels = host.split(".")
    if (
        "%" in host  # an IPv6 zone id
        or host in _BLOCKED_HOSTS
        or host.endswith(".localhost")
        or len(host) > 253
        or not all(_HOST_LABEL.fullmatch(label) for label in labels)
        or not labels[-1][0].isalpha()
    ):
        raise _bad_url("a leader URL needs a DNS host name or an IP address that may be used")


def validate_base_url(value: str) -> str:
    """The leader's normalised base URL: https://host[:port][/path], no trailing slash."""
    raw = value.strip() if isinstance(value, str) else ""
    if (
        not raw
        or len(raw) > MAX_URL_CHARS
        or not raw.isascii()
        or any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F for c in raw)
    ):
        raise _bad_url("a leader URL is an https:// URL of printable ASCII without spaces")
    parts = urlsplit(raw)
    if parts.scheme.lower() != "https":
        raise _bad_url("a leader URL must use https://")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise _bad_url("a leader URL cannot carry a user name or password")
    if parts.query or parts.fragment or raw.endswith(("?", "#")):
        raise _bad_url("a leader URL has no query or fragment")
    try:
        port = parts.port
    except ValueError:
        raise _bad_url("the port must be a number from 1 to 65535") from None
    if port == 0:
        raise _bad_url("the port must be a number from 1 to 65535")
    host = parts.hostname
    if not host:
        raise _bad_url("a leader URL needs a host name")
    _check_host(host)
    path = parts.path.rstrip("/")
    if not _PATH.fullmatch(path) or any(seg in (".", "..") for seg in path.split("/")):
        raise _bad_url("a leader URL's path holds only letters, digits and . _ ~ - segments")
    netloc = f"[{host}]" if ":" in host else host
    if port is not None and port != 443:
        netloc += f":{port}"
    return f"https://{netloc}{path}"


def validate_labels(labels: Mapping[str, Any]) -> dict[str, str]:
    if len(labels) > MAX_LABELS:
        raise Invalid(f"a leader has at most {MAX_LABELS} labels", code="invalid_labels")
    for key, value in labels.items():
        if not isinstance(key, str) or not LABEL_KEY.fullmatch(key):
            raise Invalid(
                "label keys are lowercase letters, digits and . _ - (at most 63),"
                " starting with a letter or digit",
                code="invalid_labels",
            )
        if not isinstance(value, str) or not LABEL_VALUE.fullmatch(value):
            raise Invalid(
                "label values are 1 to 255 printable ASCII characters without spaces",
                code="invalid_labels",
            )
    return dict(sorted(labels.items()))


def validate_credential(value: str) -> str:
    if not isinstance(value, str) or not _CREDENTIAL.fullmatch(value):
        raise Invalid(
            "a console credential is the 43-character value `swarmscribe-admin console "
            "create` printed",
            code="invalid_credential",
        )
    return value


async def find_leader(session: AsyncSession, name: str, *, lock: bool = False) -> Leader:
    if not isinstance(name, str) or not LEADER_NAME.fullmatch(name):
        raise NotFound("no leader with that name", code="leader_not_found")
    query = select(Leader).where(func.lower(Leader.name) == name.lower())
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    leader = await session.scalar(query)
    if leader is None:
        raise NotFound("no leader with that name", code="leader_not_found")
    return leader


async def list_leaders(session: AsyncSession) -> list[Leader]:
    return list((await session.scalars(select(Leader).order_by(Leader.name))).all())


async def add_leader(
    session: AsyncSession,
    keys: ConsoleKeys,
    *,
    name: str,
    base_url: str,
    labels: Mapping[str, Any],
    credential: str,
    enabled: bool,
    actor: str,
) -> Leader:
    name = validate_name(name)
    url = validate_base_url(base_url)
    clean_labels = validate_labels(labels)
    credential = validate_credential(credential)
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _REGISTRY_LOCK})
    taken = await session.scalar(
        select(Leader.name).where(func.lower(Leader.name) == name.lower())
    )
    if taken is not None:
        raise Conflict(f"a leader named {taken!r} is already registered", code="exists")
    leader = Leader(
        name=name,
        base_url=url,
        labels=clean_labels,
        enabled=enabled,
        credential=keys.seal_credential(name, credential),
        credential_updated_by=actor,
        added_by=actor,
    )
    session.add(leader)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise Conflict(f"a leader named {name!r} is already registered", code="exists") from exc
    audit.record(
        session,
        actor=actor,
        action="leader.add",
        leader=name,
        detail={"base_url": url, "labels": clean_labels, "enabled": enabled},
    )
    return leader


async def edit_leader(
    session: AsyncSession,
    name: str,
    *,
    base_url: str | None = None,
    labels: Mapping[str, Any] | None = None,
    enabled: bool | None = None,
    credential: str | None = None,
    keys: ConsoleKeys | None = None,
    now: datetime | None = None,
    actor: str,
) -> Leader:
    """Edit in place. A new base URL must come with the credential in the same call (ruling
    R3): the poller sends the credential to the URL, so an edit that only repointed the URL
    could hand the stored credential to another host. The credential is then sealed again
    for the new URL and the change is audited as a URL change plus a rotation. A credential
    without a URL change is refused here too: that is what the rotation call is for."""
    if credential is not None and base_url is None:
        raise Invalid(
            "send the credential with a new base_url, or replace it on its own",
            code="credential_without_url",
        )
    # Everything is checked before anything is changed.
    url = None if base_url is None else validate_base_url(base_url)
    clean_labels = None if labels is None else validate_labels(labels)
    clean_credential = None if credential is None else validate_credential(credential)
    leader = await find_leader(session, name, lock=True)
    url_changed = url is not None and url != leader.base_url
    if url_changed and clean_credential is None:
        raise Invalid(
            "changing the base URL needs the credential in the same request",
            code="credential_required",
        )
    changed: dict[str, Any] = {}
    if url is not None and url_changed:
        leader.base_url = changed["base_url"] = url
    if clean_labels is not None:
        leader.labels = changed["labels"] = clean_labels
    if enabled is not None:
        leader.enabled = changed["enabled"] = enabled
    audit.record(session, actor=actor, action="leader.edit", leader=leader.name, detail=changed)
    if clean_credential is not None:
        if keys is None or now is None:
            raise TypeError("keys and now are needed to re-seal a credential")
        _reseal(session, keys, leader, clean_credential, now=now, actor=actor,
                reason="base_url_change" if url_changed else "rotation")
    return leader


async def replace_credential(
    session: AsyncSession,
    keys: ConsoleKeys,
    name: str,
    credential: str,
    *,
    now: datetime,
    actor: str,
) -> Leader:
    """Rotation in place: the leader keeps its name, labels, grants and history. A revoked
    mark and the poller's failure count are cleared, so polling resumes at once."""
    credential = validate_credential(credential)
    leader = await find_leader(session, name, lock=True)
    _reseal(session, keys, leader, credential, now=now, actor=actor)
    return leader


def _reseal(
    session: AsyncSession,
    keys: ConsoleKeys,
    leader: Leader,
    credential: str,
    *,
    now: datetime,
    actor: str,
    reason: str | None = None,
) -> None:
    """The credential is sealed against the leader's name, and the leader's health starts
    over (a new credential, or a new address, has not failed yet). `credential` was
    validated by the caller."""
    leader.credential = keys.seal_credential(leader.name, credential)
    leader.credential_updated_at = now
    leader.credential_updated_by = actor
    leader.credential_revoked_at = None
    leader.consecutive_failures = 0
    leader.last_error = None
    leader.last_polled_at = None
    audit.record(
        session,
        actor=actor,
        action="leader.credential_replace",
        leader=leader.name,
        detail={} if reason is None else {"reason": reason},
    )


async def remove_leader(session: AsyncSession, name: str, *, actor: str) -> None:
    leader = await find_leader(session, name, lock=True)
    await session.delete(leader)
    audit.record(session, actor=actor, action="leader.remove", leader=leader.name)


def leader_view(leader: Leader) -> dict[str, Any]:
    return {
        "name": leader.name,
        "base_url": leader.base_url,
        "labels": leader.labels,
        "enabled": leader.enabled,
        "added_by": leader.added_by,
        "created_at": leader.created_at,
        "credential_updated_at": leader.credential_updated_at,
        "credential_updated_by": leader.credential_updated_by,
        "credential_revoked": leader.credential_revoked_at is not None,
        "credential_revoked_at": leader.credential_revoked_at,
    }
