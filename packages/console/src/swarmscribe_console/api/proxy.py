"""GET and POST /api/leaders/{name}/... — the allow-listed leader actions, proxied live with
the signed-in person as actor and their console role for that leader (fleet console spec
5.4). The console refuses first (unknown or ungranted leader, role below the route's,
disabled, revoked, unsendable identity, odd query or body); then the leader applies its cap
and role checks. A leader 401 never reaches the browser as a 401. Every POST is audited.

No echo (ruling R2): the three routes with a body are validated here with the leader's own
request models and refused with the console's fixed validation text; a leader's 422 keeps its
status and code but its message is replaced. The database session is released before the
leader is called, and a fresh one writes the audit entry afterwards."""

import json
import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, NoReturn

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ValidationError, field_validator
from swarmscribe_leader.api.admin_models import LocationIn, PriorityIn, TokenIn
from swarmscribe_leader.auth.roles import at_least
from swarmscribe_leader.clock import utcnow

from .. import audit
from ..crypto import CredentialUnreadable
from ..db.models import Leader
from ..errors import (
    BadGateway,
    Conflict,
    ConsoleError,
    CredentialRejected,
    CredentialRevoked,
    CredentialUnreadableError,
    Forbidden,
    Invalid,
    LeaderUnavailable,
    NotFound,
    PassedThrough,
    PayloadTooLarge,
)
from ..leader_client import (
    LeaderBadAnswer,
    LeaderReply,
    LeaderTarget,
    LeaderUnreachable,
    is_revoked,
    person_actor,
)
from ..leaders import sealing_context
from ..poller import mark_revoked
from ..proxy import ProxyRoute, match_route, target_of
from ..sessions import SignedIn
from .deps import Person, Session, keys_of, settings_of
from .errors import invalid_summary
from .fleet import leader_for

router = APIRouter(prefix="/api/leaders")

MAX_BODY_BYTES = 64 * 1024
MAX_QUERY_CHARS = 200
MAX_RETRY_AFTER = 3600
LEADER_INVALID_TEXT = "the leader did not accept the request as sent"
_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
class ConsoleLocationIn(LocationIn):
    """The leader's LocationIn with one check replaced. The leader's `root` rule uses the path
    rules of the machine it runs on, which here would be the console's. The leader is the
    authority on its own operating system, so the console accepts a root that is absolute
    under either POSIX or Windows rules and leaves the final word to the leader. Every other
    field, `extra="forbid"` and the mode check are the leader's own (a validator named like
    the parent's replaces it)."""

    @field_validator("root")
    @classmethod
    def _absolute_folder(cls, value: str) -> str:
        if any(ord(ch) < 0x20 for ch in value) or not (
            PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()
        ):
            raise ValueError("root must be an absolute folder path")
        return value


BODY_MODELS: dict[str, type[BaseModel]] = {
    "locations.add": ConsoleLocationIn,
    "jobs.priority": PriorityIn,
    "tokens.create": TokenIn,
}


def _forwarded_query(request: Request, route: ProxyRoute) -> dict[str, str]:
    query: dict[str, str] = {}
    for name in route.query:
        values = request.query_params.getlist(name)
        if len(values) > 1:
            raise Invalid(f"send {name} once")
        if values:
            value = values[0]
            if len(value) > MAX_QUERY_CHARS or not value.isprintable():
                raise Invalid(f"{name} must be at most {MAX_QUERY_CHARS} printable characters")
            query[name] = value
    return query


async def _forwarded_body(request: Request, route: ProxyRoute) -> dict[str, Any] | None:
    if not route.body:
        return None
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise PayloadTooLarge("the request body is larger than 64 KiB")
    try:
        body = json.loads(raw) if raw else None
    except (ValueError, RecursionError):
        raise Invalid("the request body is not JSON") from None
    if not isinstance(body, dict):
        raise Invalid("the request body must be a JSON object")
    try:
        # The leader's own model: what it would accept is what is forwarded, in its own form.
        model = BODY_MODELS[route.action].model_validate(body)
    except ValidationError as exc:
        raise Invalid(invalid_summary(exc.errors())) from None
    return model.model_dump(mode="json", exclude_unset=True)


def _passed_through(reply: LeaderReply) -> ConsoleError:
    body = reply.body
    if 400 <= reply.status <= 599 and isinstance(body, dict):
        code, message = body.get("code"), body.get("message")
        if isinstance(code, str) and _CODE.fullmatch(code) and isinstance(message, str):
            retry = reply.retry_after
            retry_after = None
            if retry and retry.isascii() and retry.isdigit():
                retry_after = min(int(retry), MAX_RETRY_AFTER)
            if reply.status == 422:
                message = LEADER_INVALID_TEXT  # a leader's own text can quote our input
            return PassedThrough(reply.status, code, message[:500], retry_after)
    return BadGateway(f"the leader answered HTTP {reply.status} without a usable error")


def _detail(route: ProxyRoute, role: str, body: Any) -> dict[str, Any]:
    detail: dict[str, Any] = {"role": role}
    if route.action == "tokens.create" and isinstance(body, dict):
        # Never "token": the plaintext passes to the browser once and is recorded nowhere.
        for key, name in (("id", "token_id"), ("pool", "pool")):
            value = body.get(key)
            if isinstance(value, str) and len(value) <= 100:
                detail[name] = value
    return detail


async def _audit(
    request: Request,
    person: SignedIn,
    route: ProxyRoute,
    leader: str,
    target: str | None,
    outcome: str,
    detail: dict[str, Any],
) -> None:
    if route.method != "POST":
        return
    request.state.audited = True  # the error handler must not add a second entry
    await audit.record_apart(
        request.app.state.sessionmaker,
        actor=person.actor,
        action=route.action,
        leader=leader,
        target=target,
        outcome=outcome,
        detail=detail,
    )


@router.api_route("/{name}/{rest:path}", methods=["GET", "POST"])
async def proxied(
    name: str, rest: str, request: Request, person: Person, session: Session
) -> Response:
    found = match_route(request.method, rest)
    if found is None:
        raise NotFound("the console does not offer that leader action")
    route, params = found
    row, role = await leader_for(session, person, name)
    # Copy what the call needs, then give the connection back: nothing below holds the
    # database while a slow leader answers.
    leader_id, leader_name, base_url = row.id, row.name, row.base_url
    sealed, enabled, revoked = row.credential, row.enabled, row.credential_revoked_at
    await session.close()
    target = target_of(params)

    async def refuse(error: ConsoleError) -> NoReturn:
        await _audit(request, person, route, leader_name, target, error.code, {"role": role})
        raise error

    if not at_least(role, route.role):
        await refuse(
            Forbidden(f"this needs the {route.role} role on {leader_name}; you have {role}")
        )
    if not enabled:
        await refuse(Conflict("this leader is disabled in the console", code="leader_disabled"))
    if revoked is not None:
        await refuse(
            CredentialRevoked(
                f"leader {leader_name} revoked this console's credential; "
                "a console administrator must replace it"
            )
        )
    try:
        actor = person_actor(person.issuer, person.subject, person.email)
        query = _forwarded_query(request, route)
        body = await _forwarded_body(request, route)
    except ConsoleError as exc:
        await refuse(exc)
    try:
        # Bound to the name and URL, from the same row as `sealed` (C2a `sealing_context`).
        credential = keys_of(request).open_credential(
            sealing_context(leader_name, base_url), sealed
        )
    except CredentialUnreadable:
        await refuse(
            CredentialUnreadableError(
                f"the stored credential for {leader_name} cannot be read; replace it"
            )
        )
    try:
        reply = await request.app.state.leader_client.call(
            LeaderTarget(leader_name, base_url, credential),
            route.method,
            route.leader_path(params),
            actor=actor,
            role=role,
            timeout=settings_of(request).proxy_timeout_seconds,
            params=query or None,
            json_body=body,
        )
    except LeaderUnreachable:
        await refuse(LeaderUnavailable(f"leader {leader_name} cannot be reached; try again"))
    except LeaderBadAnswer:
        await refuse(BadGateway(f"leader {leader_name} answered something not usable"))
    except ConsoleError as exc:
        await refuse(exc)
    except ValueError:
        # The client's path guard. The allow-list should make this unreachable; if it is
        # ever reached it is a refusal, not a 500.
        await refuse(BadGateway("the console could not form a safe request for that leader"))

    if is_revoked(reply):
        async with request.app.state.sessionmaker() as marking:
            fresh = await marking.get(
                Leader, leader_id, with_for_update=True, populate_existing=True
            )
            if fresh is not None:
                await mark_revoked(marking, fresh, sealed, now=utcnow(), actor=person.actor)
            await marking.commit()
        await refuse(
            CredentialRevoked(
                f"leader {leader_name} revoked this console's credential; "
                "a console administrator must replace it"
            )
        )
    if reply.status == 401:
        await refuse(CredentialRejected(f"leader {leader_name} does not accept the credential"))
    if 200 <= reply.status < 300:
        if reply.body is None and reply.status != 204:
            await refuse(
                BadGateway(
                    f"leader {leader_name} answered success with no content; "
                    "whether the action happened is unknown"
                )
            )
        # Build the answer first: a body that cannot be rendered is a bad gateway, audited
        # as one, never an "ok" entry followed by a 500.
        try:
            answer: Response = (
                Response(status_code=204)
                if reply.status == 204
                else JSONResponse(reply.body, status_code=reply.status)
            )
        except (ValueError, TypeError, RecursionError):
            await refuse(
                BadGateway(
                    f"leader {leader_name} answered something not usable; "
                    "whether the action happened is unknown"
                )
            )
        await _audit(
            request, person, route, leader_name, target, "ok", _detail(route, role, reply.body)
        )
        return answer
    await refuse(_passed_through(reply))
