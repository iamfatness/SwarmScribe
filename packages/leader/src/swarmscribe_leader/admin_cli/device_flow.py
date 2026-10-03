"""OAuth 2.0 device authorization grant (RFC 8628) against Entra ID or Google, and token
refresh. Nothing here prints a token or the device code; only the user code is shown."""

import asyncio
import base64
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
SLOW_DOWN_SECONDS = 5


class SignInError(Exception):
    """Signing in did not work; the message says what to do next."""


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    client_id: str
    device_authorization_endpoint: str
    token_endpoint: str
    scope: str
    client_secret: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class TokenSet:
    id_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    expires_at: float


def id_token_expiry(token: str) -> float | None:
    """The token's `exp`, read without verifying it: the leader verifies, the CLI only needs
    to know when to refresh."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return float(claims["exp"])
    except (IndexError, KeyError, TypeError, ValueError):
        return None


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _token_set(body: dict[str, Any], clock: Callable[[], float]) -> TokenSet:
    id_token = body.get("id_token")
    if not isinstance(id_token, str) or not id_token:
        raise SignInError("the identity provider returned no ID token (is `openid` in scope?)")
    refresh = body.get("refresh_token")
    expires_at = id_token_expiry(id_token) or clock() + float(body.get("expires_in", 3600))
    return TokenSet(
        id_token=id_token,
        refresh_token=refresh if isinstance(refresh, str) and refresh else None,
        expires_at=expires_at,
    )


async def device_sign_in(
    http: httpx.AsyncClient,
    provider: ProviderConfig,
    *,
    prompt: Callable[[str], object],
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    clock: Callable[[], float] = time.time,
) -> TokenSet:
    response = await http.post(
        provider.device_authorization_endpoint,
        data={"client_id": provider.client_id, "scope": provider.scope},
    )
    start = _json(response)
    if response.status_code != 200 or "device_code" not in start:
        reason = start.get("error") or response.status_code
        raise SignInError(f"{provider.name} would not start a sign-in ({reason})")
    uri = start.get("verification_uri") or start.get("verification_url")
    prompt(f"To sign in, open {uri} and enter the code {start.get('user_code')}")
    interval = float(start.get("interval", SLOW_DOWN_SECONDS))
    deadline = clock() + float(start.get("expires_in", 900))
    poll = {
        "grant_type": DEVICE_GRANT,
        "client_id": provider.client_id,
        "device_code": start["device_code"],
    }
    if provider.client_secret:
        poll["client_secret"] = provider.client_secret
    while clock() < deadline:
        await sleep(interval)
        response = await http.post(provider.token_endpoint, data=poll)
        body = _json(response)
        if response.status_code == 200:
            return _token_set(body, clock)
        error = body.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += SLOW_DOWN_SECONDS
            continue
        if error in ("access_denied", "authorization_declined"):
            raise SignInError("the sign-in was declined")
        if error in ("expired_token", "code_expired"):
            break
        raise SignInError(f"sign-in failed ({error or response.status_code})")
    raise SignInError("the sign-in code expired; run `swarmscribe-admin login` again")


async def refresh_tokens(
    http: httpx.AsyncClient,
    *,
    token_endpoint: str,
    client_id: str,
    refresh_token: str,
    scope: str = "",
    client_secret: str | None = None,
    clock: Callable[[], float] = time.time,
) -> TokenSet:
    form = {"grant_type": "refresh_token", "client_id": client_id, "refresh_token": refresh_token}
    if scope:
        form["scope"] = scope
    if client_secret:
        form["client_secret"] = client_secret
    response = await http.post(token_endpoint, data=form)
    if response.status_code != 200:
        raise SignInError("your sign-in has expired; run `swarmscribe-admin login` again")
    return _token_set(_json(response), clock)
