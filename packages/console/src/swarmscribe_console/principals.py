"""A signed-in person's principals: what grants and console admins name (fleet console spec
5.1), computed once at sign-in with the leader's rules (leader spec section 10) and kept in
the session. A change of group membership therefore applies at the next sign-in.

- Entra ID: the token's group object ids, or Microsoft Graph on overage.
- Google: Google Groups through Cloud Identity (when a service account is configured), plus
  the email and its domain under Workspace rules — a domain only when the token's hd equals
  it; an email when hd equals its domain, or for gmail.com/googlemail.com addresses.
Entra ID emails are not principals; the leader does not map them either.

Principals are lowercase ASCII, always: grants are matched by exact string (grants.py does not
normalise), so a principal that is not in that form can only fail closed. Text that is not
ASCII is dropped before lowercasing, because some lowercase to ASCII (U+212A KELVIN SIGN
becomes "k") and would impersonate an ASCII name.

This module re-implements two private helpers of the leader's swarmscribe_leader.auth.roles,
so that the leader's rules apply here without importing its underscore names. Keep them in
step with these (leader roles.py):
- `_hosted_domain`  -> `_hosted_domain` below (a Google token's hd claim, ASCII, lowercased);
- `_has_group_overage` -> `_has_group_overage` below (Entra's too-many-groups markers).
The rest of RoleResolver._resolve's logic is mirrored in `principals_for`."""

import logging
import uuid
from typing import Any

from swarmscribe_leader.auth.oidc import Identity
from swarmscribe_leader.auth.roles import (
    CONSUMER_GOOGLE_DOMAINS,
    GoogleGroupsClient,
    GraphClient,
)

logger = logging.getLogger(__name__)


def _hosted_domain(claims: dict[str, Any]) -> str | None:
    hd = claims.get("hd")
    if isinstance(hd, str) and hd and hd.isascii():
        return hd.lower()
    return None


def _has_group_overage(claims: dict[str, Any]) -> bool:
    names = claims.get("_claim_names")
    if isinstance(names, dict) and "groups" in names:
        return True
    return claims.get("hasgroups") in (True, "true")


def _entra_group(value: object) -> str | None:
    if not str(value).isascii():
        return None
    try:
        return f"entra_group:{uuid.UUID(str(value))}"
    except ValueError:
        return None


async def principals_for(
    identity: Identity,
    *,
    graph: GraphClient | None,
    google_groups: GoogleGroupsClient | None,
) -> frozenset[str]:
    """Raises the leader's RoleLookupFailed when a directory cannot be asked."""
    found: set[str] = set()
    claims = identity.claims
    if identity.provider == "entra":
        if _has_group_overage(claims):
            oid = claims.get("oid")
            if graph is None or not oid:
                logger.warning("an Entra ID sign-in has too many groups and no oid; no groups")
                groups: set[str] = set()
            else:
                groups = await graph.member_object_ids(str(oid))
        else:
            groups = {str(group) for group in claims.get("groups") or []}
        found |= {p for p in (_entra_group(group) for group in groups) if p}
        return frozenset(found)

    email = identity.email
    if email and google_groups is not None:
        found |= {
            f"google_group:{group.lower()}"
            for group in await google_groups.group_emails(email)
            if group.isascii()
        }
    if email and email.isascii():
        address = email.lower()
        local, at, domain = address.partition("@")
        if local and at and domain and "@" not in domain:
            hosted = _hosted_domain(claims)
            if hosted == domain or domain in CONSUMER_GOOGLE_DOMAINS:
                found.add(f"email:{address}")
            if hosted == domain:
                found.add(f"domain:{domain}")
    return frozenset(found)
