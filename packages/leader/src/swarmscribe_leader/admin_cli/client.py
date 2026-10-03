"""HTTPS client for the leader's /v1/admin API that keeps the caller signed in."""

import time
from dataclasses import replace
from typing import Any

import httpx

from .credentials import CredentialStore, SignIn
from .device_flow import id_token_expiry, refresh_tokens

REFRESH_MARGIN_SECONDS = 120


LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


class CliError(Exception):
    """A command failed; the message is for the person at the terminal."""


class UsageError(CliError):
    """The command line itself is wrong (exit code 2)."""


def normalise_leader(text: str) -> str:
    """The one spelling of a leader URL (scheme://host[:port], no trailing slash) that
    credentials are saved and looked up under. Plain http is for this machine only."""
    bad = UsageError(
        "the leader URL must be https://host[:port] (http only for localhost, 127.0.0.1 or ::1)"
    )
    try:
        url = httpx.URL(text.strip())
        url_host = url.host  # decoded lazily: an invalid IDNA host (xn--) fails here
    except (httpx.InvalidURL, ValueError):
        raise bad from None
    if (
        url.scheme not in ("http", "https")
        or not url_host
        or url.userinfo
        or url.query
        or url.fragment
        or url.path not in ("", "/")
    ):
        raise bad
    if url.scheme == "http" and url_host not in LOCAL_HOSTS:
        raise bad
    host = f"[{url_host}]" if ":" in url_host else url_host
    port = f":{url.port}" if url.port is not None else ""
    return f"{url.scheme}://{host}{port}"


def _body(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def printable(text: str) -> str:
    """One printable line: the leader's words never carry terminal control characters."""
    return " ".join("".join(c if c.isprintable() else " " for c in text).split())


def _line(value: Any) -> str:
    return printable(value) if isinstance(value, str) else ""


class LeaderClient:
    def __init__(
        self,
        leader: str,
        store: CredentialStore,
        *,
        http: httpx.AsyncClient,
        clock=time.time,
    ):
        self.leader = normalise_leader(leader)
        self.store = store
        self.http = http
        self.clock = clock

    async def _refreshed(self, sign_in: SignIn) -> SignIn:
        if not sign_in.refresh_token:
            raise CliError("your sign-in has expired; run `swarmscribe-admin login`")
        tokens = await refresh_tokens(
            self.http,
            token_endpoint=sign_in.token_endpoint,
            client_id=sign_in.client_id,
            refresh_token=sign_in.refresh_token,
            scope=sign_in.scope if sign_in.provider == "entra" else "",
            client_secret=sign_in.client_secret,
            provider=sign_in.provider,
            clock=self.clock,
        )
        updated = replace(
            sign_in,
            id_token=tokens.id_token,
            refresh_token=tokens.refresh_token or sign_in.refresh_token,
        )
        self.store.save(updated)
        return updated

    async def _id_token(self, *, force_refresh: bool) -> str:
        sign_in = self.store.load(self.leader)
        if sign_in is None:
            raise CliError("not signed in to this leader; run `swarmscribe-admin login`")
        expiry = id_token_expiry(sign_in.id_token)
        if force_refresh or expiry is None or expiry - self.clock() < REFRESH_MARGIN_SECONDS:
            sign_in = await self._refreshed(sign_in)
        return sign_in.id_token

    async def _send(
        self, method: str, path: str, body: Any, params: Any, *, force_refresh: bool
    ) -> httpx.Response:
        token = await self._id_token(force_refresh=force_refresh)
        return await self.http.request(
            method,
            f"{self.leader}{path}",
            json=body,
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )

    async def request(self, method: str, path: str, *, body: Any = None, params: Any = None) -> Any:
        response = await self._send(method, path, body, params, force_refresh=False)
        if response.status_code == 401 and _body(response).get("code") == "token_expired":
            response = await self._send(method, path, body, params, force_refresh=True)
        if not response.is_success:
            error = _body(response)
            message = _line(error.get("message")) or response.reason_phrase or "request refused"
            code = _line(error.get("code")) or "error"
            raise CliError(f"{message} ({response.status_code} {code})")
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            raise CliError("the leader sent an answer that is not JSON") from None
