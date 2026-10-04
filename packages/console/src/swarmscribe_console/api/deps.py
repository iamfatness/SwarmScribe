"""Request dependencies: the database session, the signed-in person and the CSRF check.

Every /api route takes `Person`, so the CSRF rule cannot be forgotten on a new route: a
request with any method but GET, HEAD or OPTIONS needs exactly one X-CSRF-Token equal to
the session's token, and an Origin header, if sent, must be the console's own."""

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from swarmscribe_leader.clock import utcnow

from ..config import Settings
from ..crypto import ConsoleKeys
from ..errors import CsrfRejected, Unauthenticated
from ..sessions import SESSION_COOKIE, SignedIn, find_session

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_HEADER = "x-csrf-token"


async def db_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def settings_of(request: Request) -> Settings:
    return request.app.state.settings


def keys_of(request: Request) -> ConsoleKeys:
    return request.app.state.keys


def session_cookie(request: Request) -> tuple[str | None, bool]:
    """The session cookie, and whether the request is ambiguous: two cookies of that name (a
    sibling-subdomain or injected duplicate) are refused, never guessed between."""
    seen = [
        part.partition("=")[2].strip()
        for header in request.headers.getlist("cookie")
        for part in header.split(";")
        if part.partition("=")[0].strip() == SESSION_COOKIE
    ]
    return (seen[0] if len(seen) == 1 else None), len(seen) > 1


async def signed_in(request: Request) -> SignedIn:
    cookie, ambiguous = session_cookie(request)
    if ambiguous:
        raise Unauthenticated("sign in to the console first", clear_cookie=True)
    idle = timedelta(seconds=settings_of(request).session_idle_seconds)
    async with request.app.state.sessionmaker() as session:
        person = await find_session(session, cookie, now=utcnow(), idle=idle)
        await session.commit()
    if person is None:
        raise Unauthenticated("sign in to the console first", clear_cookie=cookie is not None)
    request.state.person = person
    return person


async def checked(request: Request, person: Annotated[SignedIn, Depends(signed_in)]) -> SignedIn:
    if request.method not in SAFE_METHODS:
        # Defence in depth: a browser says when the request is not from the console's own
        # origin; "none" is a user-initiated navigation or a non-browser client.
        site = request.headers.get("sec-fetch-site")
        if site is not None and site not in ("same-origin", "none"):
            raise CsrfRejected("this request did not come from the console")
        origins = request.headers.getlist("origin")
        if origins and origins != [settings_of(request).public_url]:
            raise CsrfRejected("this request did not come from the console")
        tokens = request.headers.getlist(CSRF_HEADER)
        if len(tokens) != 1 or not keys_of(request).csrf_matches(person.session_id, tokens[0]):
            raise CsrfRejected("missing or wrong CSRF token; reload the console")
    return person


Person = Annotated[SignedIn, Depends(checked)]
Session = Annotated[AsyncSession, Depends(db_session)]
