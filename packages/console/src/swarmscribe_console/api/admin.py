"""Console administration (fleet console spec 5.2): the leader registry, role grants and
console administrators. Console administrators only; holding a leader role is not enough,
and being a console administrator gives no leader role. Every change is audited."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from swarmscribe_leader.clock import utcnow

from .. import grants, leaders
from ..errors import Forbidden
from ..sessions import SignedIn
from .deps import Person, Session, keys_of
from .models import CredentialIn, LeaderEdit, LeaderIn, LeaderOut

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
