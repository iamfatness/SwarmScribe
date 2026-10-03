import asyncio
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
from sqlalchemy import text
from swarmscribe_leader.db.migrate import upgrade
from swarmscribe_leader.db.models import Base
from swarmscribe_leader.db.session import make_engine, make_sessionmaker

TEST_DATABASE = "swarmscribe_test"
REPO_ROOT = Path(__file__).resolve().parents[3]
KEEP_TABLES = {"alembic_version", "settings_profiles"}


def _admin_url() -> str:
    url = os.environ.get("SWARMSCRIBE_TEST_DATABASE_URL")
    if url:
        return url
    import pgserver

    server = pgserver.get_server(str(REPO_ROOT / ".pgdata"), cleanup_mode="stop")
    return server.get_uri()


def _with_database(url: str, name: str) -> str:
    return urlunsplit(urlsplit(url)._replace(path="/" + name))


async def _recreate(admin_url: str, name: str) -> None:
    conn = await asyncpg.connect(admin_url)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def database_url() -> str:
    admin = _admin_url()
    asyncio.run(_recreate(admin, TEST_DATABASE))
    return _with_database(admin, TEST_DATABASE)


@pytest.fixture(scope="session")
def migrated_database_url(database_url) -> str:
    upgrade(database_url)
    return database_url


@pytest.fixture
async def engine(migrated_database_url):
    engine = make_engine(migrated_database_url)
    tables = [t.name for t in reversed(Base.metadata.sorted_tables) if t.name not in KEEP_TABLES]
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    yield engine
    await engine.dispose()


@pytest.fixture
def sessionmaker(engine):
    return make_sessionmaker(engine)
