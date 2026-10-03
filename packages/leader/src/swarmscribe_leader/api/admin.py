"""The /v1/admin API.

Each route authenticates the caller and checks their role (`require`), calls one service
function, writes an audit entry and commits. Changes are audited by the service functions
with the change; reads are audited here.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..auth.oidc import login_providers
from .admin_auth import Admin, require
from .admin_models import LoginConfig, WhoAmI
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
