"""Browser sign-in (fleet console spec 5.2): OIDC authorization code with PKCE against Entra
ID and Google, as a confidential web client.

ID tokens are validated by the leader's TokenVerifier (leader spec section 10's rules). This
module adds the browser half: the authorization URL with an S256 code challenge, a nonce, and
a single-use state bound to the browser that started the sign-in (by a cookie whose SHA-256
is stored with the state); and the code exchange. The ID token lives only for the duration
of the callback; the access and refresh tokens the provider returns are dropped unread."""

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx
from pydantic import SecretStr
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from swarmscribe_leader.auth.oidc import (
    ENTRA_AUTHORITY,
    GOOGLE_DISCOVERY,
    GOOGLE_ISSUERS,
    Provider,
    ProviderName,
)
from swarmscribe_leader.auth.secrets import hash_secret, new_secret

from .config import Settings
from .db.models import LoginAttempt
from .sessions import is_token

SCOPE = "openid email profile"  # no offline_access: no refresh token is asked for
CALLBACK_PATH = "/auth/callback"
GOOGLE_AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
EXCHANGE_TIMEOUT_SECONDS = 10.0
MAX_CODE_CHARS = 4096


@dataclass(frozen=True)
class WebProvider:
    name: ProviderName
    client_id: str
    client_secret: SecretStr = field(repr=False)
    authorization_endpoint: str
    token_endpoint: str
    verification: Provider


def web_providers(settings: Settings) -> dict[str, WebProvider]:
    hosted = settings.google_hosted_domain
    if hosted is not None and not hosted.strip():
        # The leader's verifier tests the domain by truthiness, so "" would mean "any
        # domain". Refuse it here rather than sign anyone in on that reading.
        raise ValueError("google_hosted_domain is empty; unset it or name a domain")
    found: dict[str, WebProvider] = {}
    if settings.entra_client_id and settings.entra_tenant_id and settings.entra_client_secret:
        tenant = settings.entra_tenant_id
        issuer = f"{ENTRA_AUTHORITY}/{tenant}/v2.0"
        base = f"{ENTRA_AUTHORITY}/{tenant}/oauth2/v2.0"
        found["entra"] = WebProvider(
            name="entra",
            client_id=settings.entra_client_id,
            client_secret=settings.entra_client_secret,
            authorization_endpoint=f"{base}/authorize",
            token_endpoint=f"{base}/token",
            verification=Provider(
                name="entra",
                issuers=(issuer,),
                client_id=settings.entra_client_id,
                discovery_url=f"{issuer}/.well-known/openid-configuration",
                tenant_id=tenant,
            ),
        )
    if settings.google_client_id and settings.google_client_secret:
        found["google"] = WebProvider(
            name="google",
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            authorization_endpoint=GOOGLE_AUTHORIZE,
            token_endpoint=GOOGLE_TOKEN,
            verification=Provider(
                name="google",
                issuers=GOOGLE_ISSUERS,
                client_id=settings.google_client_id,
                discovery_url=GOOGLE_DISCOVERY,
                hosted_domain=hosted,
            ),
        )
    return found


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorization_url(
    provider: WebProvider, *, redirect_uri: str, state: str, nonce: str, verifier: str
) -> str:
    params = {
        "client_id": provider.client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge(verifier),
        "code_challenge_method": "S256",
    }
    if provider.verification.hosted_domain is not None:
        params["hd"] = provider.verification.hosted_domain  # a hint; the token is checked
    return f"{provider.authorization_endpoint}?{urlencode(params)}"


async def begin_sign_in(
    session: AsyncSession,
    provider: WebProvider,
    *,
    redirect_uri: str,
    return_to: str,
    now: datetime,
    ttl: timedelta,
    prior_session_hash: str | None = None,
) -> tuple[str, str]:
    """Record a pending sign-in. Returns the provider URL to send the browser to and the
    secret for the browser's login cookie. `prior_session_hash` is the SHA-256 of any
    session cookie the browser sent when it started; the callback ends that session too.
    The caller commits."""
    state, browser, nonce = new_secret(), new_secret(), new_secret()
    verifier = secrets.token_urlsafe(64)  # 86 characters, inside PKCE's 43 to 128
    session.add(
        LoginAttempt(
            state_hash=hash_secret(state),
            browser_hash=hash_secret(browser),
            provider=provider.name,
            nonce=nonce,
            code_verifier=verifier,
            return_to=return_to,
            prior_session_hash=prior_session_hash,
            expires_at=now + ttl,
        )
    )
    url = authorization_url(
        provider, redirect_uri=redirect_uri, state=state, nonce=nonce, verifier=verifier
    )
    return url, browser


@dataclass(frozen=True)
class SignInAttempt:
    provider: str
    nonce: str = field(repr=False)
    code_verifier: str = field(repr=False)
    return_to: str
    prior_session_hash: str | None = None


class SignInFailed(Exception):
    """A callback that belongs to no sign-in this browser started, or to an expired one.
    The message is shown to the person."""


class CodeExchangeFailed(Exception):
    """The provider would not exchange the code. The message names no secret."""


async def finish_sign_in(
    session: AsyncSession, *, state: str | None, browser_secret: str | None, now: datetime
) -> SignInAttempt:
    """Take (delete) the pending sign-in `state` names. It is gone afterwards whatever the
    outcome, so a state can never be used twice; the caller commits even on failure."""
    if not is_token(state):
        raise SignInFailed("this sign-in link is not valid; sign in again")
    row = (
        await session.execute(
            delete(LoginAttempt)
            .where(LoginAttempt.state_hash == hash_secret(state))
            .returning(
                LoginAttempt.browser_hash,
                LoginAttempt.provider,
                LoginAttempt.nonce,
                LoginAttempt.code_verifier,
                LoginAttempt.return_to,
                LoginAttempt.prior_session_hash,
                LoginAttempt.expires_at,
            )
        )
    ).one_or_none()
    if row is None:
        raise SignInFailed("this sign-in is unknown or was already used; sign in again")
    if now >= row.expires_at:
        raise SignInFailed("this sign-in took too long; sign in again")
    if not is_token(browser_secret) or not hmac.compare_digest(
        row.browser_hash, hash_secret(browser_secret)
    ):
        raise SignInFailed("this sign-in was started in another browser; sign in again")
    return SignInAttempt(
        provider=row.provider,
        nonce=row.nonce,
        code_verifier=row.code_verifier,
        return_to=row.return_to,
        prior_session_hash=row.prior_session_hash,
    )


async def exchange_code(
    provider: WebProvider,
    *,
    code: str,
    verifier: str,
    redirect_uri: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """The ID token for `code`. The access and refresh tokens in the answer are not read."""
    if not code or len(code) > MAX_CODE_CHARS:
        raise CodeExchangeFailed(f"{provider.name} sent no usable authorization code")
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": provider.client_id,
        "client_secret": provider.client_secret.get_secret_value(),
        "code_verifier": verifier,
    }
    try:
        async with httpx.AsyncClient(
            timeout=EXCHANGE_TIMEOUT_SECONDS, transport=transport, follow_redirects=False
        ) as client:
            response = await client.post(
                provider.token_endpoint, data=form, headers={"Accept": "application/json"}
            )
            body = response.json() if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError) as exc:
        raise CodeExchangeFailed(
            f"{provider.name}'s token endpoint could not be used: {type(exc).__name__}"
        ) from None
    if response.status_code != 200:
        raise CodeExchangeFailed(
            f"{provider.name} refused the authorization code (HTTP {response.status_code})"
        )
    token = body.get("id_token") if isinstance(body, dict) else None
    if not isinstance(token, str) or not token:
        raise CodeExchangeFailed(f"{provider.name} returned no ID token")
    return token
