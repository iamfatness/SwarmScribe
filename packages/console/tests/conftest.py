import asyncio
import os
import sys
from pathlib import Path

# console_testkit (shared constants and helpers) sits beside this file.
sys.path.insert(0, str(Path(__file__).parent))

import pytest  # noqa: E402
from console_testkit import MASTER_KEY, recreate, with_database  # noqa: E402
from sqlalchemy import text  # noqa: E402
from swarmscribe_console.crypto import ConsoleKeys  # noqa: E402
from swarmscribe_console.db.migrate import upgrade  # noqa: E402
from swarmscribe_console.db.models import Base  # noqa: E402
from swarmscribe_leader.db.session import make_engine, make_sessionmaker  # noqa: E402

TEST_DATABASE = "swarmscribe_console_test"
REPO_ROOT = Path(__file__).resolve().parents[3]
KEEP_TABLES = {"alembic_version"}


def _admin_url() -> str:
    url = os.environ.get("SWARMSCRIBE_TEST_DATABASE_URL")
    if url:
        return url
    import pgserver

    server = pgserver.get_server(str(REPO_ROOT / ".pgdata"), cleanup_mode="stop")
    return server.get_uri()


@pytest.fixture(scope="session")
def admin_database_url() -> str:
    return _admin_url()


@pytest.fixture(scope="session")
def database_url(admin_database_url) -> str:
    asyncio.run(recreate(admin_database_url, TEST_DATABASE))
    return with_database(admin_database_url, TEST_DATABASE)


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


@pytest.fixture
def keys():
    return ConsoleKeys(MASTER_KEY)
