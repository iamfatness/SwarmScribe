"""Who is calling /v1/admin, and may they? Refusals for a role are audited; tokens are never
logged or stored."""

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Request

from .. import audit
from ..auth.oidc import (
    Fetch,
    Identity,
    MetadataUnavailable,
    TokenVerifier,
    http_fetch,
    providers_from,
)
from ..auth.roles import (
    GoogleCloudIdentity,
    GoogleGroupsClient,
    GraphClient,
    MicrosoftGraph,
    Role,
    RoleLookupFailed,
    RoleMapping,
    RoleResolver,
    at_least,
)
from ..config import Settings
from ..errors import Forbidden, ServiceUnavailable, Unauthorized

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Admin:
    identity: Identity
    role: Role

    @property
    def actor(self) -> str:
        return self.identity.actor


class AdminAuth:
    def __init__(self, verifier: TokenVerifier, resolver: RoleResolver):
        self.verifier = verifier
        self.resolver = resolver

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        fetch: Fetch | None = None,
        graph: GraphClient | None = None,
        google_groups: GoogleGroupsClient | None = None,
        clock: Callable[[], float] = time.time,
    ) -> "AdminAuth":
        """Real directory clients are built only when configured and not given."""
        if graph is None and settings.entra_client_id and settings.entra_client_secret:
            graph = MicrosoftGraph(
                settings.entra_tenant_id, settings.entra_client_id, settings.entra_client_secret
            )
        service_account = settings.google_service_account_key()
        if google_groups is None and service_account is not None:
            google_groups = GoogleCloudIdentity(service_account)
        verifier = TokenVerifier(providers_from(settings), fetch=fetch or http_fetch, clock=clock)
        resolver = RoleResolver(
            RoleMapping.from_settings(settings),
            graph=graph,
            google_groups=google_groups,
            cache_seconds=settings.role_cache_seconds,
        )
        return cls(verifier, resolver)

    async def authenticate(self, token: str) -> tuple[Identity, Role | None]:
        try:
            identity = await self.verifier.verify(token)
            return identity, await self.resolver.role_for(identity)
        except (MetadataUnavailable, RoleLookupFailed) as exc:
            logger.warning("an administrator's sign-in could not be checked: %s", exc)
            raise ServiceUnavailable("sign-in cannot be checked right now; retry shortly") from exc


def _bearer(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise Unauthorized(
            "sign in with `swarmscribe-admin login` and send the ID token as a Bearer token"
        )
    return token.strip()


async def _audit_refusal(
    request: Request, identity: Identity, granted: Role | None, required: Role
) -> None:
    route = request.scope.get("route")
    async with request.app.state.sessionmaker() as session:
        audit.record(
            session,
            actor=identity.actor,
            action="admin.refused",
            subject_type="endpoint",
            subject_id=f"{request.method} {getattr(route, 'path', '?')}",
            detail={"role": granted, "required": required},
        )
        await session.commit()


def require(role: Role) -> Callable[[Request], Awaitable[Admin]]:
    """A dependency admitting people whose role is `role` or higher."""

    async def dependency(request: Request) -> Admin:
        auth: AdminAuth = request.app.state.admin_auth
        identity, granted = await auth.authenticate(_bearer(request))
        if not at_least(granted, role):
            await _audit_refusal(request, identity, granted, role)
            if granted is None:
                raise Forbidden("you have no SwarmScribe role; ask an administrator for one")
            raise Forbidden(f"this needs the {role} role; you have {granted}")
        return Admin(identity=identity, role=granted)

    return dependency
