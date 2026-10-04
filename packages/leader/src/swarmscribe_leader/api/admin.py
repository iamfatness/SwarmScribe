"""The /v1/admin API.

Each route authenticates the caller and checks their role (`require`), calls one service
function, writes an audit entry and commits. Changes are audited by the service functions
with the change; reads are audited here. A read or change refused after the role check
(404, 409, 422, ...) is audited by the error handlers (api/errors.py) in its own session.
"""

import uuid
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, reports
from ..auth import consoles, followers
from ..auth.followers import create_join_token
from ..auth.oidc import login_providers
from ..clock import utcnow
from ..ingest import locations
from ..jobs import admin as job_admin
from .admin_auth import Admin, require
from .admin_models import (
    ConsentReport,
    ConsoleCreated,
    ConsoleIn,
    ConsoleOut,
    FollowerOut,
    FollowerRevoked,
    FollowerState,
    JobOut,
    JobState,
    LocationIn,
    LocationOut,
    LoginConfig,
    PriorityIn,
    ScanRequested,
    Status,
    TokenCreated,
    TokenIn,
    TokenOut,
    WhoAmI,
)
from .deps import db_session, settings_of

router = APIRouter(prefix="/v1/admin")

Viewer = Annotated[Admin, Depends(require("viewer"))]
Operator = Annotated[Admin, Depends(require("operator"))]
Administrator = Annotated[Admin, Depends(require("admin"))]
# Console credentials are managed by people only: a console cannot mint or revoke them.
PersonAdministrator = Annotated[Admin, Depends(require("admin", consoles_allowed=False))]
Session = Annotated[AsyncSession, Depends(db_session)]


# The reads the console's poller makes every 15 seconds. Owner ruling 2026-10-03: these, when
# successful, write no audit row. Any other read by the poller is audited, so a console cannot
# read the rest of the system unseen by claiming to be the poller.
POLLER_UNAUDITED_READS = frozenset({"status.view", "followers.view", "whoami.view"})


async def _viewed(
    session: AsyncSession, admin: Admin, action: str, detail: dict[str, Any] | None = None
) -> None:
    if admin.is_poller and action in POLLER_UNAUDITED_READS:
        return
    audit.record(session, actor=admin.actor, action=action, detail=detail)
    await session.commit()


@router.get("/login-config", response_model=LoginConfig)
async def login_config(request: Request) -> LoginConfig:
    """What the CLI needs to start a device-code sign-in. Needs no sign-in itself."""
    return LoginConfig.model_validate({"providers": login_providers(settings_of(request))})


@router.get("/whoami", response_model=WhoAmI)
async def whoami(admin: Viewer, session: Session) -> WhoAmI:
    await _viewed(session, admin, "whoami.view")
    return WhoAmI(
        provider=admin.provider,
        issuer=admin.issuer,
        subject=admin.subject,
        email=admin.email,
        role=admin.role,
        console=admin.console,
    )


@router.get("/status", response_model=Status)
async def status(admin: Viewer, session: Session) -> Status:
    summary = await reports.status_summary(session)
    await _viewed(session, admin, "status.view")
    return Status.model_validate(summary)


@router.get("/locations", response_model=list[LocationOut])
async def list_locations(admin: Viewer, session: Session) -> list[LocationOut]:
    rows = await reports.list_locations(session)
    await _viewed(session, admin, "locations.view")
    return [LocationOut.model_validate(row) for row in rows]


@router.get("/jobs", response_model=list[JobOut])
async def list_jobs(
    admin: Viewer,
    session: Session,
    state: JobState | None = None,
    location: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[JobOut]:
    rows = await reports.list_jobs(session, state=state, location=location, limit=limit)
    await _viewed(session, admin, "jobs.view", {"state": state, "location": location})
    return [JobOut.model_validate(row) for row in rows]


@router.get("/followers", response_model=list[FollowerOut])
async def list_followers(
    admin: Viewer, session: Session, state: FollowerState | None = None
) -> list[FollowerOut]:
    rows = await reports.list_followers(session, state=state)
    await _viewed(session, admin, "followers.view", {"state": state})
    return [FollowerOut.model_validate(row) for row in rows]


@router.get("/tokens", response_model=list[TokenOut])
async def list_tokens(admin: Administrator, session: Session) -> list[TokenOut]:
    rows = await reports.list_tokens(session)
    await _viewed(session, admin, "tokens.view")
    return [TokenOut.model_validate(row) for row in rows]


@router.get("/consent/report", response_model=ConsentReport)
async def consent_report(
    admin: Viewer,
    session: Session,
    location: str | None = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
) -> ConsentReport:
    report = await reports.consent_report(session, location=location, limit=limit)
    await _viewed(session, admin, "consent.view", {"location": location})
    return ConsentReport.model_validate(report)


@router.post("/locations", response_model=LocationOut, status_code=201)
async def add_location(body: LocationIn, admin: Administrator, session: Session) -> LocationOut:
    location = await locations.add_location(session, **body.model_dump(), actor=admin.actor)
    view = reports.location_view(location)
    await session.commit()
    return LocationOut.model_validate(view)


async def _set_enabled(
    session: AsyncSession, name: str, enabled: bool, admin: Admin
) -> LocationOut:
    location = await locations.set_enabled(session, name, enabled, actor=admin.actor)
    view = reports.location_view(location)
    await session.commit()
    return LocationOut.model_validate(view)


@router.post("/locations/{name}/disable", response_model=LocationOut)
async def disable_location(name: str, admin: Administrator, session: Session) -> LocationOut:
    return await _set_enabled(session, name, False, admin)


@router.post("/locations/{name}/enable", response_model=LocationOut)
async def enable_location(name: str, admin: Administrator, session: Session) -> LocationOut:
    return await _set_enabled(session, name, True, admin)


@router.post("/locations/{name}/ingest", response_model=ScanRequested, status_code=202)
async def ingest_location(name: str, admin: Operator, session: Session) -> ScanRequested:
    location = await locations.request_scan(session, name, now=utcnow(), actor=admin.actor)
    answer = ScanRequested(name=location.name, requested_at=location.scan_requested_at)
    await session.commit()
    return answer


@router.post("/jobs/{job_id}/retry", response_model=JobOut)
async def retry_job(
    job_id: uuid.UUID, request: Request, admin: Operator, session: Session
) -> JobOut:
    job = await job_admin.retry_job(
        session, job_id, actor=admin.actor, max_attempts=settings_of(request).max_attempts
    )
    view = await reports.describe_job(session, job)
    await session.commit()
    return JobOut.model_validate(view)


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: uuid.UUID, admin: Operator, session: Session) -> JobOut:
    job = await job_admin.cancel_job(session, job_id, now=utcnow(), actor=admin.actor)
    view = await reports.describe_job(session, job)
    await session.commit()
    return JobOut.model_validate(view)


@router.post("/jobs/{job_id}/priority", response_model=JobOut)
async def set_priority(
    job_id: uuid.UUID, body: PriorityIn, admin: Operator, session: Session
) -> JobOut:
    job = await job_admin.set_priority(session, job_id, body.priority, actor=admin.actor)
    view = await reports.describe_job(session, job)
    await session.commit()
    return JobOut.model_validate(view)


@router.post("/followers/{follower_id}/drain", response_model=FollowerOut)
async def drain_follower(follower_id: uuid.UUID, admin: Operator, session: Session) -> FollowerOut:
    follower = await followers.drain(session, follower_id, actor=admin.actor)
    view = await reports.describe_follower(session, follower)
    await session.commit()
    return FollowerOut.model_validate(view)


@router.post("/followers/{follower_id}/revoke", response_model=FollowerRevoked)
async def revoke_follower(
    follower_id: uuid.UUID, admin: Administrator, session: Session
) -> FollowerRevoked:
    follower, released = await followers.revoke_follower(
        session, follower_id, now=utcnow(), actor=admin.actor
    )
    await session.commit()
    return FollowerRevoked(id=str(follower.id), state=follower.state, released=released)


@router.post("/tokens", response_model=TokenCreated, status_code=201)
async def create_token(body: TokenIn, admin: Administrator, session: Session) -> TokenCreated:
    """The only response that carries a join token. Nothing logs response bodies."""
    token, plaintext = await create_join_token(
        session,
        pool=body.pool,
        expires_at=utcnow() + timedelta(seconds=body.expires_in_seconds),
        max_uses=body.max_uses,
        created_by=admin.actor,
    )
    await session.commit()
    return TokenCreated(
        id=str(token.id),
        token=plaintext,
        pool=token.pool,
        expires_at=token.expires_at,
        max_uses=token.max_uses,
    )


@router.post("/tokens/{token_id}/revoke", response_model=TokenOut)
async def revoke_token(token_id: uuid.UUID, admin: Administrator, session: Session) -> TokenOut:
    token = await followers.revoke_token(session, token_id, actor=admin.actor)
    view = reports.token_view(token)
    await session.commit()
    return TokenOut.model_validate(view)


@router.get("/consoles", response_model=list[ConsoleOut])
async def list_console_credentials(
    admin: PersonAdministrator, session: Session
) -> list[ConsoleOut]:
    rows = await reports.list_consoles(session)
    await _viewed(session, admin, "consoles.view")
    return [ConsoleOut.model_validate(row) for row in rows]


@router.post("/consoles", response_model=ConsoleCreated, status_code=201)
async def create_console_credential(
    body: ConsoleIn, admin: PersonAdministrator, session: Session
) -> ConsoleCreated:
    """The only response that carries a console credential. Nothing logs response bodies."""
    try:
        console, credential = await consoles.create_console(
            session, name=body.name, max_role=body.max_role, actor=admin.actor
        )
    except consoles.InvalidConsoleName as exc:
        # The request model refuses bad names first; this keeps one code if it ever does not.
        raise consoles.InvalidConsoleName(exc.message, code="invalid_request") from exc
    await session.commit()
    return ConsoleCreated(
        id=str(console.id), name=console.name, max_role=console.max_role, credential=credential
    )


@router.post("/consoles/{name}/revoke", response_model=ConsoleOut)
async def revoke_console_credential(
    name: str, admin: PersonAdministrator, session: Session
) -> ConsoleOut:
    """A malformed name is answered 404 like any unknown one (the store decides)."""
    console = await consoles.revoke_console(session, name, now=utcnow(), actor=admin.actor)
    view = reports.console_view(console)
    await session.commit()
    return ConsoleOut.model_validate(view)
