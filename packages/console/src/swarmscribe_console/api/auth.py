"""Sign-in routes: /auth/providers, /auth/login, /auth/callback.

The callback answers with a small HTML page that refreshes to the return URL, not with a
redirect: the browser's next navigation is then same-site, so the SameSite=Strict session
cookie is sent with it. Pages carry no script (CSP) and echo nothing the provider sent."""

import hmac
import html
import logging
import re
from datetime import timedelta
from urllib.parse import unquote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from swarmscribe_leader.auth.oidc import MetadataUnavailable
from swarmscribe_leader.auth.roles import RoleLookupFailed
from swarmscribe_leader.auth.secrets import hash_secret
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.errors import Unauthorized

from .. import audit, grants
from ..errors import NotFound
from ..oidc import (
    CALLBACK_PATH,
    CodeExchangeFailed,
    SignInFailed,
    begin_sign_in,
    exchange_code,
    finish_sign_in,
)
from ..principals import principals_for
from ..sessions import (
    LOGIN_COOKIE,
    SESSION_COOKIE,
    clear_login_cookie,
    clear_session_cookie,
    create_session,
    end_session,
    end_session_by_hash,
    is_token,
    prune_expired,
    set_login_cookie,
    set_session_cookie,
)
from .deps import _session_cookie, settings_of

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth")

MAX_RETURN_TO = 512
_RETURN_TO = re.compile(r"/[A-Za-z0-9\-._~/?=&%#:+,@]*")


def safe_return_to(value: str | None) -> str:
    """A path on this console, or "/". Never a scheme, a host or a protocol-relative URL, in
    the text as sent or after any percent-decoding a browser or proxy might apply."""
    if not value or len(value) > MAX_RETURN_TO or not _RETURN_TO.fullmatch(value):
        return "/"
    decoded = value
    for _ in range(3):  # %252f -> %2f -> /
        decoded = unquote(decoded)
        if (
            decoded.startswith("//")
            or "\\" in decoded
            or any(ord(ch) < 0x21 or ord(ch) > 0x7E for ch in decoded)
        ):
            return "/"
    return value


_FAILED = "Sign-in failed"
_UNAVAILABLE = "Sign-in unavailable"
_TRY_AGAIN = "Your sign-in could not be verified. Sign in again."


def _page(
    status: int, title: str, message: str, *, refresh_to: str | None = None
) -> HTMLResponse:
    meta = ""
    if refresh_to is not None:
        target = html.escape(refresh_to, quote=True)
        meta = f'<meta http-equiv="refresh" content="0;url={target}">'
    link = html.escape(refresh_to or "/", quote=True)
    body = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"{meta}<title>{html.escape(title)}</title></head>"
        f"<body><main><h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>"
        f'<p><a href="{link}">Continue</a></p></main></body></html>'
    )
    return HTMLResponse(body, status_code=status)


class _Refused(Exception):
    def __init__(self, status: int, title: str, message: str):
        super().__init__(message)
        self.status, self.title, self.message = status, title, message


@router.get("/providers")
async def providers(request: Request) -> dict[str, list[str]]:
    return {"providers": sorted(request.app.state.web_providers)}


@router.get("/login")
async def login(request: Request, provider: str, return_to: str | None = None) -> Response:
    web = request.app.state.web_providers.get(provider)
    if web is None:
        raise NotFound("no such sign-in provider", code="unknown_provider")
    settings = settings_of(request)
    now = utcnow()
    brought, ambiguous = _session_cookie(request)
    # A sign-in never builds on a session the browser already holds: end it now (fixation),
    # and remember its hash so the callback ends it too if it is still there.
    prior = hash_secret(brought) if is_token(brought) else None
    async with request.app.state.sessionmaker() as session:
        await end_session(session, brought)
        await prune_expired(
            session, now=now, idle=timedelta(seconds=settings.session_idle_seconds)
        )
        url, browser = await begin_sign_in(
            session,
            web,
            redirect_uri=settings.public_url + CALLBACK_PATH,
            return_to=safe_return_to(return_to),
            now=now,
            ttl=timedelta(seconds=settings.login_attempt_seconds),
            prior_session_hash=prior,
        )
        await session.commit()
    response = RedirectResponse(url, status_code=302)
    if brought is not None or ambiguous:
        clear_session_cookie(response)
    set_login_cookie(response, browser, settings.login_attempt_seconds)
    return response


@router.get("/callback")  # CALLBACK_PATH
async def callback(request: Request) -> Response:
    try:
        response = await _complete(request)
    except _Refused as refusal:
        response = _page(refusal.status, refusal.title, refusal.message)
    clear_login_cookie(response)
    return response


async def _complete(request: Request) -> Response:
    settings = settings_of(request)
    state = request.app.state
    params = request.query_params
    now = utcnow()
    states = params.getlist("state")
    async with state.sessionmaker() as session:
        try:
            attempt = await finish_sign_in(
                session,
                state=states[0] if len(states) == 1 else None,
                browser_secret=request.cookies.get(LOGIN_COOKIE),
                now=now,
            )
        except SignInFailed as exc:
            raise _Refused(400, _FAILED, str(exc)) from None
        finally:
            await session.commit()  # the state is single-use even when the callback fails

    web = state.web_providers.get(attempt.provider)
    if web is None:  # the provider was unconfigured between login and callback
        raise _Refused(400, _FAILED, "That sign-in provider is no longer offered.")
    if "error" in params:
        raise _Refused(400, "Sign-in was not completed", "You were not signed in. Sign in again.")
    codes = params.getlist("code")
    if len(codes) != 1:
        raise _Refused(400, _FAILED, "The sign-in answer was incomplete. Sign in again.")
    redirect_uri = settings.public_url + CALLBACK_PATH
    try:
        id_token = await exchange_code(
            web,
            code=codes[0],
            verifier=attempt.code_verifier,
            redirect_uri=redirect_uri,
            transport=state.idp_transport,
        )
    except CodeExchangeFailed as exc:
        logger.warning("a sign-in's code exchange failed: %s", exc)
        raise _Refused(502, _FAILED, "The identity provider did not answer. Try again.") from None
    try:
        identity = await state.verifier.verify(id_token)
    except Unauthorized as exc:
        logger.warning("a sign-in's ID token was refused: %s", exc.message)
        raise _Refused(401, _FAILED, _TRY_AGAIN) from None
    except MetadataUnavailable:
        raise _Refused(503, _UNAVAILABLE, "Sign-in cannot be checked now. Try again.") from None
    nonce = identity.claims.get("nonce")
    if (
        identity.provider != attempt.provider
        or not isinstance(nonce, str)
        or not hmac.compare_digest(nonce.encode(), attempt.nonce.encode())
    ):
        logger.warning("a sign-in's ID token did not belong to its request")
        raise _Refused(401, _FAILED, _TRY_AGAIN)
    try:
        principals = await principals_for(
            identity, graph=state.graph, google_groups=state.google_groups
        )
    except RoleLookupFailed as exc:
        logger.warning("a sign-in's groups could not be read: %s", exc)
        raise _Refused(503, _UNAVAILABLE, "Your groups cannot be read now. Try again.") from None

    idle = timedelta(seconds=settings.session_idle_seconds)
    async with state.sessionmaker() as session:
        if not await grants.has_any_access(session, principals):
            audit.record(
                session, actor=identity.actor, action="sign_in.refused", outcome="no_access"
            )
            await session.commit()
            raise _Refused(
                403,
                "No access",
                "You signed in, but you hold no role in this console. "
                "Ask a console administrator for one.",
            )
        # Never adopt a session id the browser brought with it (session fixation).
        await end_session(session, request.cookies.get(SESSION_COOKIE))
        await end_session_by_hash(session, attempt.prior_session_hash)
        await prune_expired(session, now=now, idle=idle)
        session_id = await create_session(
            session,
            provider=identity.provider,
            issuer=identity.issuer,
            subject=identity.subject,
            email=identity.email,
            principals=principals,
            now=now,
            lifetime=timedelta(seconds=settings.session_lifetime_seconds),
        )
        audit.record(
            session,
            actor=identity.actor,
            action="sign_in",
            detail={"provider": identity.provider},
        )
        await session.commit()
    response = _page(200, "Signed in", "Continuing to the console.", refresh_to=attempt.return_to)
    set_session_cookie(response, session_id, settings.session_lifetime_seconds)
    return response
