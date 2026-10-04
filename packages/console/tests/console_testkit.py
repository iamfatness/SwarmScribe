"""Constants and helpers shared by the console's test files. conftest.py puts this folder on
sys.path; test files import from here, never from conftest."""

import base64
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import httpx
from sqlalchemy import text
from swarmscribe_console.db.models import Base

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
