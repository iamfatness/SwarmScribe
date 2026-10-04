"""Console administration (fleet console spec 5.2): the leader registry, role grants and
console administrators. Console administrators only; holding a leader role is not enough,
and being a console administrator gives no leader role. Every change is audited."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from swarmscribe_leader.clock import utcnow

from .. import grants, leaders
from ..errors import Forbidden
from ..sessions import SignedIn
from .deps import Person, Session, keys_of
from .models import (
    ConsoleAdminIn,
    ConsoleAdminOut,
    CredentialIn,
    GrantIn,
    GrantOut,
    LeaderEdit,
    LeaderIn,
    LeaderOut,
)

router = APIRouter(prefix="/api/admin")


async def console_admin(person: Person, session: Session) -> SignedIn:
    if not await grants.is_console_admin(session, person.principals):
        raise Forbidden("this needs a console administrator")
    return person


ConsoleAdministrator = Annotated[SignedIn, Depends(console_admin)]


@router.get("/leaders", response_model=list[LeaderOut])
async def list_registered(admin: ConsoleAdministrator, session: Session) -> list[LeaderOut]:
    rows = await leaders.list_leaders(session)
    return [LeaderOut.model_validate(leaders.leader_view(row)) for row in rows]


@router.post("/leaders", response_model=LeaderOut, status_code=201)
async def register(
    body: LeaderIn, request: Request, admin: ConsoleAdministrator, session: Session
) -> LeaderOut:
    leader = await leaders.add_leader(
        session,
        keys_of(request),
        name=body.name,
        base_url=body.base_url,
        labels=body.labels,
        credential=body.credential.get_secret_value(),
        enabled=body.enabled,
        actor=admin.actor,
    )
    view = leaders.leader_view(leader)
    await session.commit()
    return LeaderOut.model_validate(view)


@router.patch("/leaders/{name}", response_model=LeaderOut)
async def edit(
    name: str,
    body: LeaderEdit,
    request: Request,
    admin: ConsoleAdministrator,
    session: Session,
) -> LeaderOut:
    leader = await leaders.edit_leader(
        session,
        name,
        base_url=body.base_url,
        labels=body.labels,
        enabled=body.enabled,
        credential=None if body.credential is None else body.credential.get_secret_value(),
        keys=keys_of(request),
        now=utcnow(),
        actor=admin.actor,
    )
    view = leaders.leader_view(leader)
    await session.commit()
    return LeaderOut.model_validate(view)


@router.put("/leaders/{name}/credential", response_model=LeaderOut)
async def rotate(
    name: str,
    body: CredentialIn,
    request: Request,
    admin: ConsoleAdministrator,
    session: Session,
) -> LeaderOut:
    leader = await leaders.replace_credential(
        session,
        keys_of(request),
        name,
        body.credential.get_secret_value(),
        now=utcnow(),
        actor=admin.actor,
    )
    view = leaders.leader_view(leader)
    await session.commit()
    return LeaderOut.model_validate(view)


@router.delete("/leaders/{name}", status_code=204)
async def remove(name: str, admin: ConsoleAdministrator, session: Session) -> Response:
    await leaders.remove_leader(session, name, actor=admin.actor)
    await session.commit()
    return Response(status_code=204)


def _grant_out(grant) -> GrantOut:
    return GrantOut.model_validate(grant, from_attributes=True)


def _admin_out(admin) -> ConsoleAdminOut:
    return ConsoleAdminOut.model_validate(admin, from_attributes=True)


@router.get("/grants", response_model=list[GrantOut])
async def list_grants(admin: ConsoleAdministrator, session: Session) -> list[GrantOut]:
    return [_grant_out(grant) for grant in await grants.list_grants(session)]


@router.post("/grants", response_model=GrantOut, status_code=201)
async def add_grant(body: GrantIn, admin: ConsoleAdministrator, session: Session) -> GrantOut:
    grant = await grants.add_grant(
        session,
        role=body.role,
        scope=body.scope,
        principal_kind=body.principal_kind,
        principal=body.principal,
        actor=admin.actor,
    )
    out = _grant_out(grant)
    await session.commit()
    return out


@router.delete("/grants/{grant_id}", status_code=204)
async def remove_grant(
    grant_id: uuid.UUID, admin: ConsoleAdministrator, session: Session
) -> Response:
    await grants.remove_grant(session, grant_id, actor=admin.actor)
    await session.commit()
    return Response(status_code=204)


@router.get("/console-admins", response_model=list[ConsoleAdminOut])
async def list_admins(admin: ConsoleAdministrator, session: Session) -> list[ConsoleAdminOut]:
    return [_admin_out(row) for row in await grants.list_console_admins(session)]


@router.post("/console-admins", response_model=ConsoleAdminOut, status_code=201)
async def add_admin(
    body: ConsoleAdminIn, admin: ConsoleAdministrator, session: Session
) -> ConsoleAdminOut:
    row = await grants.add_console_admin(
        session, principal_kind=body.principal_kind, principal=body.principal, actor=admin.actor
    )
    out = _admin_out(row)
    await session.commit()
    return out


@router.delete("/console-admins/{admin_id}", status_code=204)
async def remove_admin(
    admin_id: uuid.UUID, admin: ConsoleAdministrator, session: Session
) -> Response:
    await grants.remove_console_admin(session, admin_id, actor=admin.actor)
    await session.commit()
    return Response(status_code=204)
