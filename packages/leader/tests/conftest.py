import asyncio
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
from sqlalchemy import text
from swarmscribe_leader.auth.secrets import hash_secret, new_secret
from swarmscribe_leader.clock import utcnow
from swarmscribe_leader.db.migrate import upgrade
from swarmscribe_leader.db.models import Base, Follower, Job, Recording, StorageLocation
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


class Factory:
    """Creates committed rows for tests. Each method uses its own session."""

    def __init__(self, sessionmaker, root: Path):
        self.sessionmaker = sessionmaker
        self.root = root

    async def _save(self, row):
        async with self.sessionmaker() as session:
            session.add(row)
            await session.commit()
        return row

    async def location(self, **overrides) -> StorageLocation:
        values = {
            "id": uuid.uuid4(),
            "name": f"location-{uuid.uuid4().hex[:8]}",
            "backend": "local",
            "config": {"root": str(self.root)},
            "input_prefix": "",
            "output_prefix": "transcripts/",
            "pool": "default",
            "required_device": "any",
            "scan_interval_s": 900,
            "enabled": True,
            "vocabulary_version": 0,
        }
        values.update(overrides)
        return await self._save(StorageLocation(**values))

    async def recording(self, location=None, *, key="talks/one.mp3", **overrides) -> Recording:
        location = location or await self.location()
        now = utcnow()
        values = {
            "id": uuid.uuid4(),
            "location_id": location.id,
            "key": key,
            "size": 10,
            "source_version": "10-1",
            "consent": "consented",
            "first_seen_at": now,
            "last_seen_at": now,
            "missing": False,
        }
        values.update(overrides)
        return await self._save(Recording(**values))

    async def job(self, recording=None, **overrides) -> Job:
        recording = recording or await self.recording()
        values = {
            "id": uuid.uuid4(),
            "recording_id": recording.id,
            "source_version": recording.source_version,
            "state": "queued",
            "pool": "default",
            "required_device": "any",
            "priority": 0,
            "attempts": 0,
            "max_attempts": 3,
        }
        values.update(overrides)
        return await self._save(Job(**values))

    async def follower(
        self, *, pool="default", device="cpu", state="active", last_seen_at=None
    ) -> tuple[Follower, str]:
        credential = new_secret()
        follower = Follower(
            id=uuid.uuid4(),
            pool=pool,
            capabilities={"device": device, "models": [], "engine_version": "0.1.0", "pool": pool},
            credential_hash=hash_secret(credential),
            state=state,
            last_seen_at=last_seen_at or utcnow(),
        )
        await self._save(follower)
        return follower, credential


@pytest.fixture
def factory(sessionmaker, tmp_path):
    return Factory(sessionmaker, tmp_path)
