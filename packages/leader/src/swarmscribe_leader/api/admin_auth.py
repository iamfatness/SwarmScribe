"""Who is calling /v1/admin, and may they?

A person signs in with an ID token (`Authorization: Bearer`). A fleet console calls with its
own credential (`Authorization: Console`) on behalf of the person named in
X-SwarmScribe-Actor, or of its poller; its role is the lower of the asserted role and the
credential's cap, then the same role check applies. The actor headers are read on console
requests only. Refusals are audited; tokens and credentials are never logged or stored."""

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Request

from .. import audit
from ..auth import consoles
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
from ..errors import Forbidden, LeaderError, ServiceUnavailable, Unauthorized

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Admin:
    """The caller of an admin route. `provider` is "entra" or "google" for a signed-in
    person and "console" for a console's delegated request; `issuer` is None only for the
    console's poller; `role` is None only before `require` admits the caller."""

    provider: str
    issuer: str | None
    subject: str | None
    email: str | None
    role: Role | None
    console: str | None = None

    @classmethod
    def person(cls, identity: Identity, role: Role | None) -> "Admin":
        return cls(
            provider=identity.provider,
            issuer=identity.issuer,
            subject=identity.subject,
            email=identity.email,
            role=role,
        )

    @property
    def is_poller(self) -> bool:
        """The console's own status poller (fleet console spec 5.3), not a person."""
        return self.console is not None and self.issuer is None

    @property
    def actor(self) -> str:
        """How the audit log names the caller. Built by DelegatedActor and nowhere else, so
        that a person is named the same way signed in or through a console
        (`<email> (<issuer> <subject>)`), the poller is `system:poller`, and a console
        request adds ` via console <name>`."""
        who = consoles.DelegatedActor(self.issuer, self.subject, self.email)
        return who.name if self.console is None else who.audit_name(self.console)


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


_SCHEMES = ("bearer", "console")


def _authorization(request: Request) -> tuple[str, str]:
    """The one Authorization header's scheme (lowercase) and credential. Several headers (a
    Bearer and a Console, say) are refused rather than one being picked."""
    values = request.headers.getlist("authorization")
    if len(values) > 1:
        raise Unauthorized("send exactly one Authorization header")
    scheme, _, credential = (values[0] if values else "").partition(" ")
    scheme = scheme.lower()
    if scheme not in _SCHEMES or not credential.strip():
        raise Unauthorized(
            "sign in with `swarmscribe-admin login` and send the ID token as a Bearer token"
        )
    return scheme, credential.strip()


async def _audit(request: Request, *, actor: str, action: str, detail: dict[str, Any]) -> None:
    """An entry about this request in its own session; only the route template is kept."""
    route = request.scope.get("route")
    async with request.app.state.sessionmaker() as session:
        audit.record(
            session,
            actor=actor,
            action=action,
            subject_type="endpoint",
            subject_id=f"{request.method} {getattr(route, 'path', '?')}",
            detail=detail,
        )
        await session.commit()


async def _console_caller(request: Request, credential: str) -> tuple[Admin, dict[str, Any]]:
    """Authenticate the console first (401 for an unknown or revoked credential, whatever
    the headers say), then read whom it acts for. Nothing is cached: a revocation applies
    to the console's next request."""
    try:
        async with request.app.state.sessionmaker() as session:
            console = await consoles.authenticate_console(session, credential)
            name, cap = console.name, console.max_role
    except consoles.RevokedConsoleCredential as exc:
        # A revoked console that keeps calling is worth seeing; an unknown one names no one.
        await _audit(
            request,
            actor=f"console {exc.console}",
            action="console.refused",
            detail={"code": "revoked"},
        )
        raise
    try:
        delegate, asserted = consoles.parse_delegation(
            request.headers.getlist(consoles.ACTOR_HEADER),
            request.headers.getlist(consoles.ROLE_HEADER),
        )
    except LeaderError as exc:
        # A known console sent malformed headers: record that it did, never what it sent.
        await _audit(
            request, actor=f"console {name}", action="console.refused", detail={"code": exc.code}
        )
        raise
    caller = Admin(
        provider="console",
        issuer=delegate.issuer,
        subject=delegate.subject,
        email=delegate.email,
        role=consoles.effective_role(asserted, cap),
        console=name,
    )
    return caller, {"asserted": asserted, "cap": cap}


def _refusal(caller: Admin, required: Role, console: dict[str, Any]) -> str:
    if caller.role is None:
        return "you have no SwarmScribe role; ask an administrator for one"
    if console and not at_least(console["cap"], required):
        cap = console["cap"]
        return f"this needs the {required} role; console {caller.console} is limited to {cap}"
    return f"this needs the {required} role; you have {caller.role}"


_READS = ("GET", "HEAD", "OPTIONS")


async def audit_refused_request(request: Request, code: str) -> None:
    """Record that an admitted caller's request (past the role check) was refused: a change
    for a business reason (admin.change_refused) or a read that failed (admin.read_refused),
    so every admin call leaves an entry. Own session: the request's transaction is rolled
    back. Only the route template and the error code are recorded, never request values."""
    admin = getattr(request.state, "admin", None)
    if admin is None:
        return
    action = "admin.read_refused" if request.method in _READS else "admin.change_refused"
    try:
        await _audit(request, actor=admin.actor, action=action, detail={"code": code})
    except Exception:
        logger.exception("a refused admin request could not be audited")


def require(
    role: Role, *, consoles_allowed: bool = True
) -> Callable[[Request], Awaitable[Admin]]:
    """A dependency admitting callers whose role is `role` or higher: a signed-in person, or
    (unless consoles_allowed is False) a console acting for one."""

    async def dependency(request: Request) -> Admin:
        scheme, credential = _authorization(request)
        console: dict[str, Any] = {}
        if scheme == "console":
            caller, console = await _console_caller(request, credential)
            if not consoles_allowed:
                detail = {
                    "role": caller.role,
                    "required": role,
                    **console,
                    "console_allowed": False,
                }
                await _audit(request, actor=caller.actor, action="admin.refused", detail=detail)
                raise Forbidden(
                    "a console credential cannot do this; "
                    "sign in as a person with `swarmscribe-admin login`"
                )
        else:
            auth: AdminAuth = request.app.state.admin_auth
            identity, granted = await auth.authenticate(credential)
            caller = Admin.person(identity, granted)
        if not at_least(caller.role, role):
            detail = {"role": caller.role, "required": role, **console}
            await _audit(request, actor=caller.actor, action="admin.refused", detail=detail)
            raise Forbidden(_refusal(caller, role, console))
        request.state.admin = caller
        return caller

    return dependency
