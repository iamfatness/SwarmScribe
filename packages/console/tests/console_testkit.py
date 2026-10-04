"""Constants and helpers shared by the console's test files. conftest.py puts this folder on
sys.path; test files import from here, never from conftest."""

import asyncio
import base64
import copy
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import httpx
from sqlalchemy import text
from swarmscribe_console.db.models import Base

ADMIN_PRINCIPAL = "email:admin@example.org"
CREDENTIAL = "c" * 20 + "_-" + "D" * 21  # 43 URL-safe characters, as C1 makes them

MASTER_KEY = bytes(range(32))
TEST_KEY = base64.urlsafe_b64encode(MASTER_KEY).rstrip(b"=").decode("ascii")


def with_database(url: str, name: str) -> str:
    return urlunsplit(urlsplit(url)._replace(path="/" + name))


async def recreate(admin_url: str, name: str, *, drop_only: bool = False) -> None:
    conn = await asyncpg.connect(admin_url)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if not drop_only:
            await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


def console_env(monkeypatch, database_url: str) -> None:
    """A complete SWARMSCRIBE_CONSOLE_* environment for command-line tests."""
    monkeypatch.setenv("SWARMSCRIBE_CONSOLE_DATABASE_URL", database_url)
    monkeypatch.setenv("SWARMSCRIBE_CONSOLE_PUBLIC_URL", "https://console.test")
    monkeypatch.setenv("SWARMSCRIBE_CONSOLE_KEY", TEST_KEY)
    monkeypatch.setenv("SWARMSCRIBE_CONSOLE_GOOGLE_CLIENT_ID", "google-client")
    monkeypatch.setenv("SWARMSCRIBE_CONSOLE_GOOGLE_CLIENT_SECRET", "google-secret")


PUBLIC_URL = "https://console.test"
ENTRA_TENANT = "0f0e0d0c-0b0a-4908-8706-050403020100"
ENTRA_CLIENT = "6d3a6b52-1c2e-4a8f-9d61-2f6a4c0b7e11"
ENTRA_SECRET = "entra-web-client-secret-value"
ENTRA_ISSUER = f"https://login.microsoftonline.com/{ENTRA_TENANT}/v2.0"
GOOGLE_CLIENT = "google-client-1.apps.googleusercontent.com"
GOOGLE_SECRET = "google-web-client-secret-value"
GOOGLE_ISSUER = "https://accounts.google.com"
GROUPS = {
    "viewer": "a1a1a1a1-0000-4000-8000-000000000001",
    "operator": "a1a1a1a1-0000-4000-8000-000000000002",
    "admin": "a1a1a1a1-0000-4000-8000-000000000003",
    "console": "a1a1a1a1-0000-4000-8000-0000000000c0",
}


def cookie_attributes(response: httpx.Response, name: str) -> dict[str, str] | None:
    """The attributes of the Set-Cookie header for `name` (lowercase keys), or None."""
    for header in response.headers.get_list("set-cookie"):
        first, *rest = [part.strip() for part in header.split(";")]
        key, _, value = first.partition("=")
        if key == name:
            attributes = {"value": value}
            for part in rest:
                attr, _, attr_value = part.partition("=")
                attributes[attr.lower()] = attr_value
            return attributes
    return None


async def all_rows_text(engine) -> str:
    """Every row of every console table as JSON text, for 'is this secret stored?' checks.
    (bytea columns appear hex-encoded; check sealed credentials on the raw bytes instead.)"""
    parts = []
    async with engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            rows = await conn.execute(text(f"select row_to_json(t)::text from {table.name} t"))
            parts.extend(row[0] for row in rows)
    return "\n".join(parts)


async def sign_in(client, idp, *, provider="entra", return_to=None, **claims) -> httpx.Response:
    """The whole browser sign-in: the console's /auth/login, the provider, the callback."""
    params = {"provider": provider}
    if return_to is not None:
        params["return_to"] = return_to
    started = await client.get("/auth/login", params=params)
    assert started.status_code == 302, started.text
    return await client.get(idp.authorize(started.headers["location"], **claims))


STATUS = {
    "jobs": {"queued": 3, "leased": 1, "completed": 10, "failed": 0, "cancelled": 0},
    "pools": [{"pool": "default", "queued": 3, "leased": 1}],
    "followers": {"active": 2, "draining": 0, "revoked": 0, "gone": 0},
    "follower_pools": [{"pool": "default", "active": 2, "draining": 0, "revoked": 0, "gone": 0}],
    "completed_last_hour": 7,
    "completed_last_day": 30,
    "oldest_queued_age_s": 420,
    "failed_attempts_last_day": 1,
    "locations": [],
}
# C1 + C1b: a revoked credential's code is credential_revoked; an unknown one's unauthorized.
REVOKED_BODY = {
    "code": "credential_revoked",
    "message": "this console credential has been revoked",
}
UNKNOWN_BODY = {"code": "unauthorized", "message": "unknown console credential"}


class FakeLeader:
    """Leaders as the console sees them over HTTPS, told apart by host. Records every
    request. `modes[host]` makes a host slow, down, revoked or unknown-credential;
    `replies[(method, path)] = (status, body, headers)` programs an answer (a dict or list
    body is JSON, bytes are sent as they are); otherwise GET .../v1/admin/status answers
    `status` and anything else is a leader-style 404."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.bodies: list[bytes] = []
        self.modes: dict[str, str] = {}
        self.replies: dict[tuple[str, str], tuple[int, Any, dict[str, str]]] = {}
        self.status: dict[str, Any] = copy.deepcopy(STATUS)
        self.delay = 1.0
        self.on_request: Callable[[httpx.Request], Awaitable[None]] | None = None

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.bodies.append(await request.aread())
        if self.on_request is not None:
            await self.on_request(request)
        mode = self.modes.get(request.url.host, "ok")
        if mode == "down":
            raise httpx.ConnectError("connection refused", request=request)
        if mode == "slow":
            await asyncio.sleep(self.delay)
        challenge = {"WWW-Authenticate": 'Console error="invalid_token"'}
        if mode == "revoked":
            return httpx.Response(401, json=REVOKED_BODY, headers=challenge)
        if mode == "unknown":
            return httpx.Response(401, json=UNKNOWN_BODY, headers=challenge)
        reply = self.replies.get((request.method, request.url.path))
        if reply is not None:
            status, body, headers = reply
            if isinstance(body, bytes):
                return httpx.Response(status, content=body, headers=headers)
            return httpx.Response(status, json=body, headers=headers)
        if request.method == "GET" and request.url.path.endswith("/v1/admin/status"):
            return httpx.Response(200, json=self.status)
        return httpx.Response(404, json={"code": "not_found", "message": "Not Found"})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
