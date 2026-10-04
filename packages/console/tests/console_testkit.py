"""Constants and helpers shared by the console's test files. conftest.py puts this folder on
sys.path; test files import from here, never from conftest."""

import base64
from urllib.parse import urlsplit, urlunsplit

import asyncpg

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
