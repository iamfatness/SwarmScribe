"""The /v1/admin API.

Each route authenticates the caller and checks their role (`require`), calls one service
function, writes an audit entry and commits. Changes are audited by the service functions
with the change; reads are audited here.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, reports
from ..auth.oidc import login_providers
from .admin_auth import Admin, require
from .admin_models import (
    ConsentReport,
    FollowerOut,
    FollowerState,
    JobOut,
    JobState,
    LocationOut,
    LoginConfig,
    Status,
    TokenOut,
    WhoAmI,
)
from .deps import db_session, settings_of

router = APIRouter(prefix="/v1/admin")

Viewer = Annotated[Admin, Depends(require("viewer"))]
Operator = Annotated[Admin, Depends(require("operator"))]
Administrator = Annotated[Admin, Depends(require("admin"))]
Session = Annotated[AsyncSession, Depends(db_session)]


async def _viewed(
    session: AsyncSession, admin: Admin, action: str, detail: dict[str, Any] | None = None
) -> None:
    audit.record(session, actor=admin.actor, action=action, detail=detail)
    await session.commit()


@router.get("/login-config", response_model=LoginConfig)
async def login_config(request: Request) -> LoginConfig:
    """What the CLI needs to start a device-code sign-in. Needs no sign-in itself."""
    return LoginConfig.model_validate({"providers": login_providers(settings_of(request))})


@router.get("/whoami", response_model=WhoAmI)
async def whoami(admin: Viewer, session: Session) -> WhoAmI:
    await _viewed(session, admin, "whoami.view")
    identity = admin.identity
    return WhoAmI(
        provider=identity.provider,
        issuer=identity.issuer,
        subject=identity.subject,
        email=identity.email,
        role=admin.role,
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
