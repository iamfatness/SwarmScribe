"""Helpers for tests, in this package and in others, that need a real leader: a migrated
database, recordings queued as jobs, a token to join with. conftest.py puts this folder on
sys.path; another package's conftest does the same to use it (the follower's does).

Nothing here is a fixture: fixtures do not cross packages. Everything is a plain function
or coroutine, and nothing prints a token, a credential or a link."""

import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from swarmscribe_leader.auth.followers import create_join_token
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.migrate import upgrade
from swarmscribe_leader.db.models import Base, StorageLocation
from swarmscribe_leader.ingest.locations import add_location
from swarmscribe_leader.ingest.scanner import scan_location
from swarmscribe_leader.storage.links import LinkSigner
from swarmscribe_leader.storage.registry import backend_for

REPO_ROOT = Path(__file__).resolve().parents[3]
LINK_KEY = "k" * 32
KEEP_TABLES = {"alembic_version", "settings_profiles"}


def admin_url() -> str:
    """A Postgres to create test databases in: $SWARMSCRIBE_TEST_DATABASE_URL, or a local
    server started from the repository's .pgdata folder."""
    url = os.environ.get("SWARMSCRIBE_TEST_DATABASE_URL")
    if url:
        return url
    import pgserver

    server = pgserver.get_server(str(REPO_ROOT / ".pgdata"), cleanup_mode="stop")
    return server.get_uri()


def with_database(url: str, name: str) -> str:
    return urlunsplit(urlsplit(url)._replace(path="/" + name))


async def recreate(url: str, name: str) -> None:
    conn = await asyncpg.connect(url)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


async def migrated_database(name: str) -> str:
    """Drop and recreate the database `name`, migrate it to the head, return its URL."""
    import asyncio

    url = admin_url()
    await recreate(url, name)
    database = with_database(url, name)
    await asyncio.to_thread(upgrade, database)
    return database


async def empty_tables(session: AsyncSession) -> None:
    """Empty every table but the migration's own rows (the seeded settings profiles)."""
    tables = [t.name for t in reversed(Base.metadata.sorted_tables) if t.name not in KEEP_TABLES]
    await session.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    await session.commit()


async def add_recordings(
    sessionmaker: async_sessionmaker[AsyncSession],
    root: Path,
    files: dict[str, bytes],
    *,
    name: str = "testkit",
    consent: str = "**/*\n",
    public_url: str = "http://leader",
    **location: object,
) -> StorageLocation:
    """Write `files` (key -> content) and a consent.txt under `root`, add the folder as a
    location and scan it once, so that every consented file is a queued job. `location`
    passes pool, required_device, channel_mode or channel_labels to the location."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "consent.txt").write_text(consent, encoding="utf-8")
    for key, data in files.items():
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    values = {
        "name": name,
        "root": str(root),
        "input_prefix": "",
        "output_prefix": "transcripts/",
        "pool": "default",
        "required_device": "any",
        "scan_interval_s": 900,
        "channel_mode": "mono",
        "channel_labels": ("Left", "Right"),
    }
    values.update(location)
    async with sessionmaker() as session:
        added = await add_location(session, **values, actor="testkit")
        backend = backend_for(
            added, signer=LinkSigner(LINK_KEY.encode()), public_url=public_url
        )
        await scan_location(session, added, backend, now=utcnow(), max_attempts=3)
        await session.commit()
    return added


async def new_join_token(
    sessionmaker: async_sessionmaker[AsyncSession], *, pool: str = "default", max_uses: int = 5
) -> str:
    async with sessionmaker() as session:
        _, plaintext = await create_join_token(
            session,
            pool=pool,
            expires_at=utcnow() + timedelta(days=1),
            max_uses=max_uses,
            created_by="testkit",
        )
        await session.commit()
    return plaintext
