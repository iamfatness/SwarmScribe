"""The signed-in person's session: who they are, their CSRF token, and sign-out."""

from datetime import datetime

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from .. import audit, grants
from ..sessions import clear_session_cookie, end_session
from .deps import Person, Session, keys_of

router = APIRouter(prefix="/api/session")


class SessionOut(BaseModel):
    provider: str
    issuer: str
    subject: str
    email: str | None
    console_admin: bool
    csrf_token: str
    expires_at: datetime
    idle_expires_at: datetime


@router.get("", response_model=SessionOut)
async def current_session(request: Request, person: Person, session: Session) -> SessionOut:
    return SessionOut(
        provider=person.provider,
        issuer=person.issuer,
        subject=person.subject,
        email=person.email,
        console_admin=await grants.is_console_admin(session, person.principals),
        csrf_token=keys_of(request).csrf_token(person.session_id),
        expires_at=person.expires_at,
        idle_expires_at=person.idle_expires_at,
    )


@router.post("/logout", status_code=204)
async def logout(person: Person, session: Session) -> Response:
    await end_session(session, person.session_id)
    audit.record(session, actor=person.actor, action="sign_out")
    await session.commit()
    response = Response(status_code=204)
    clear_session_cookie(response)
    return response
