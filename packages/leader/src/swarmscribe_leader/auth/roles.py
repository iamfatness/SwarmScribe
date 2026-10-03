"""Turn a signed-in person into a role. Roles are cumulative: admin > operator > viewer.

Entra ID: the token's group object ids, or Microsoft Graph when the person is in too many
groups for the token ("overage"). Google: the person's Google Groups through Cloud Identity
(when a service account is configured), plus email and domain lists. Lookups are cached
per person; a directory that cannot be asked is a transient failure, never "no role".
"""

import logging
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from urllib.parse import quote

import httpx
import jwt
from pydantic import SecretStr

from ..config import ROLES, Settings
from .oidc import ENTRA_AUTHORITY, Identity

logger = logging.getLogger(__name__)

Role = Literal["viewer", "operator", "admin"]
RANK = {"viewer": 1, "operator": 2, "admin": 3}
DIRECTORY_TIMEOUT_SECONDS = 10.0


class RoleLookupFailed(Exception):
    """A group directory (Microsoft Graph, Cloud Identity) could not be asked."""


class GraphClient(Protocol):
    async def member_object_ids(self, user_object_id: str) -> set[str]: ...


class GoogleGroupsClient(Protocol):
    async def group_emails(self, email: str) -> set[str]: ...


def at_least(role: str | None, required: str) -> bool:
    return role is not None and RANK[role] >= RANK[required]


def highest(roles: Iterable[str]) -> Role | None:
    ranked = sorted(set(roles), key=RANK.__getitem__)
    return ranked[-1] if ranked else None


@dataclass(frozen=True)
class RoleMapping:
    entra_groups: dict[str, frozenset[str]]
    google_groups: dict[str, frozenset[str]]
    emails: dict[str, frozenset[str]]
    domains: dict[str, frozenset[str]]

    @classmethod
    def from_settings(cls, settings: Settings) -> "RoleMapping":
        def per_role(kind: str) -> dict[str, frozenset[str]]:
            return {role: frozenset(getattr(settings, f"role_{role}_{kind}")) for role in ROLES}

        def folded(kind: str) -> dict[str, frozenset[str]]:
            return {
                role: frozenset(n.casefold() for n in names)
                for role, names in per_role(kind).items()
            }

        return cls(
            entra_groups=folded("entra_groups"),
            google_groups=folded("google_groups"),
            emails=folded("emails"),
            domains=folded("domains"),
        )

    def wants_google_groups(self) -> bool:
        return any(self.google_groups.values())


def _has_group_overage(claims: dict[str, Any]) -> bool:
    names = claims.get("_claim_names")
    if isinstance(names, dict) and "groups" in names:
        return True
    return claims.get("hasgroups") in (True, "true")


class RoleResolver:
    def __init__(
        self,
        mapping: RoleMapping,
        *,
        graph: GraphClient | None = None,
        google_groups: GoogleGroupsClient | None = None,
        cache_seconds: float = 300,
        clock: Callable[[], float] = time.monotonic,
        max_entries: int = 4096,
    ):
        self.mapping = mapping
        self.graph = graph
        self.google_groups = google_groups
        self.cache_seconds = cache_seconds
        self.clock = clock
        self.max_entries = max_entries
        self._cache: dict[tuple[str, str], tuple[Role | None, float]] = {}

    async def role_for(self, identity: Identity) -> Role | None:
        key = (identity.issuer, identity.subject)
        now = self.clock()
        cached = self._cache.get(key)
        if cached is not None and now - cached[1] < self.cache_seconds:
            return cached[0]
        role = await self._resolve(identity)  # RoleLookupFailed propagates, uncached
        self._cache.pop(key, None)
        if len(self._cache) >= self.max_entries:
            self._cache.pop(next(iter(self._cache)))  # the oldest entry
        self._cache[key] = (role, now)
        return role

    async def _resolve(self, identity: Identity) -> Role | None:
        if identity.provider == "entra":
            groups = await self._entra_groups(identity)
            return highest(role for role, ids in self.mapping.entra_groups.items() if ids & groups)
        roles: set[str] = set()
        email = identity.email
        if email and self.google_groups is not None and self.mapping.wants_google_groups():
            groups = {group.lower() for group in await self.google_groups.group_emails(email)}
            roles |= {role for role, names in self.mapping.google_groups.items() if names & groups}
        if email:
            address = email.casefold()
            roles |= {role for role, names in self.mapping.emails.items() if address in names}
            local, at, domain = address.partition("@")
            if local and at and domain and "@" not in domain:  # exactly one "@"
                roles |= {role for role, names in self.mapping.domains.items() if domain in names}
        return highest(roles)

    async def _entra_groups(self, identity: Identity) -> set[str]:
        claims = identity.claims
        if not _has_group_overage(claims):
            return {str(group).lower() for group in claims.get("groups") or []}
        oid = claims.get("oid")
        if self.graph is None or not oid:
            logger.warning(
                "an Entra ID sign-in is in too many groups to list in its token and no "
                "client secret is configured to look them up; it gets no role"
            )
            return set()
        return {group.lower() for group in await self.graph.member_object_ids(str(oid))}


class MicrosoftGraph:
    """getMemberObjects with the leader's own app credentials (client-credentials flow).
    The app needs the GroupMember.Read.All application permission."""

    SCOPE = "https://graph.microsoft.com/.default"

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: SecretStr,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.transport = transport
        self.clock = clock
        self._token: tuple[str, float] | None = None

    async def _access_token(self, client: httpx.AsyncClient) -> str:
        if self._token is not None and self._token[1] - 60 > self.clock():
            return self._token[0]
        response = await client.post(
            f"{ENTRA_AUTHORITY}/{self.tenant_id}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret.get_secret_value(),
                "scope": self.SCOPE,
            },
        )
        response.raise_for_status()
        body = response.json()
        expires_at = self.clock() + float(body.get("expires_in", 3600))
        self._token = (str(body["access_token"]), expires_at)
        return self._token[0]

    async def member_object_ids(self, user_object_id: str) -> set[str]:
        try:
            async with httpx.AsyncClient(
                timeout=DIRECTORY_TIMEOUT_SECONDS, transport=self.transport
            ) as client:
                token = await self._access_token(client)
                response = await client.post(
                    "https://graph.microsoft.com/v1.0/users/"
                    f"{quote(user_object_id, safe='')}/getMemberObjects",
                    json={"securityEnabledOnly": False},
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()
                return {str(value).lower() for value in response.json().get("value", [])}
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise RoleLookupFailed(
                f"Microsoft Graph could not be asked: {type(exc).__name__}"
            ) from exc


class GoogleCloudIdentity:
    """The person's Google Groups (direct and inherited) through the Cloud Identity Groups
    API, signed in as a service account that holds the Groups Reader admin role."""

    SCOPE = "https://www.googleapis.com/auth/cloud-identity.groups.readonly"
    SEARCH_URL = (
        "https://cloudidentity.googleapis.com/v1/groups/-/memberships:searchTransitiveGroups"
    )
    MAX_PAGES = 20
    _SAFE_EMAIL = re.compile(r"[^'\\\s]+@[^'\\\s]+")

    def __init__(
        self,
        service_account: dict[str, str],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.account = service_account
        self.transport = transport
        self.clock = clock
        self._token: tuple[str, float] | None = None

    async def _access_token(self, client: httpx.AsyncClient) -> str:
        if self._token is not None and self._token[1] - 60 > self.clock():
            return self._token[0]
        now = int(self.clock())
        key_id = self.account.get("private_key_id")
        assertion = jwt.encode(
            {
                "iss": self.account["client_email"],
                "scope": self.SCOPE,
                "aud": self.account["token_uri"],
                "iat": now,
                "exp": now + 3600,
            },
            self.account["private_key"],
            algorithm="RS256",
            headers={"kid": key_id} if key_id else None,
        )
        response = await client.post(
            self.account["token_uri"],
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
        )
        response.raise_for_status()
        body = response.json()
        expires_at = self.clock() + float(body.get("expires_in", 3600))
        self._token = (str(body["access_token"]), expires_at)
        return self._token[0]

    async def group_emails(self, email: str) -> set[str]:
        if not self._SAFE_EMAIL.fullmatch(email):
            return set()  # cannot be quoted safely into the search query
        query = (
            f"member_key_id == '{email}' "
            "&& 'cloud.googleapis.com/groups.discussion_forum' in labels"
        )
        found: set[str] = set()
        page_token: str | None = None
        try:
            async with httpx.AsyncClient(
                timeout=DIRECTORY_TIMEOUT_SECONDS, transport=self.transport
            ) as client:
                token = await self._access_token(client)
                for _ in range(self.MAX_PAGES):
                    params = {"query": query}
                    if page_token:
                        params["pageToken"] = page_token
                    response = await client.get(
                        self.SEARCH_URL,
                        params=params,
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    response.raise_for_status()
                    body = response.json()
                    for membership in body.get("memberships", []):
                        group = (membership.get("groupKey") or {}).get("id")
                        if group:
                            found.add(str(group).lower())
                    page_token = body.get("nextPageToken")
                    if not page_token:
                        break
        except (httpx.HTTPError, KeyError, TypeError, ValueError, jwt.PyJWTError) as exc:
            raise RoleLookupFailed(
                f"Google Cloud Identity could not be asked: {type(exc).__name__}"
            ) from exc
        return found
