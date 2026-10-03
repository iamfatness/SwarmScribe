import asyncio
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from swarmscribe_leader.db.migrate import current_revision, head_revision
from swarmscribe_leader.db.models import Base, SettingsProfile


async def test_migrations_produce_exactly_the_models(engine):
    async with engine.connect() as conn:
        diff = await conn.run_sync(
            lambda sync_conn: compare_metadata(MigrationContext.configure(sync_conn), Base.metadata)
        )
    assert diff == []


async def test_database_is_at_the_head_revision(engine):
    assert head_revision() == "0003"
    assert await current_revision(engine) == "0003"


async def test_the_claim_index_serves_priority_descending(engine):
    async with engine.connect() as conn:
        definition = await conn.scalar(
            text("select indexdef from pg_indexes where indexname = 'ix_jobs_claim'")
        )
        recording_index = await conn.scalar(
            text("select indexdef from pg_indexes where indexname = 'ix_jobs_recording_id'")
        )
    assert "priority DESC" in definition
    assert "(recording_id)" in recording_index


async def test_a_device_has_at_most_one_settings_profile(sessionmaker):
    async with sessionmaker() as session:
        session.add(
            SettingsProfile(
                name="second-cpu",
                device="cpu",
                model="tiny",
                compute_type="int8",
                temperatures=[0.0],
            )
        )
        try:
            with pytest.raises(IntegrityError):
                await session.commit()
        finally:
            await session.rollback()
            await session.execute(
                delete(SettingsProfile).where(SettingsProfile.name == "second-cpu")
            )
            await session.commit()


async def _recreate(admin_url: str, name: str, *, drop_only: bool = False) -> None:
    conn = await asyncpg.connect(admin_url)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if not drop_only:
            await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


UPGRADE_SCRIPT = """
import sys, time
from swarmscribe_leader.db.migrate import upgrade
start_at = float(sys.argv[2])
time.sleep(max(0.0, start_at - time.time()))
upgrade(sys.argv[1])
"""


def test_concurrent_migrate_runs_serialise(database_url):
    # Separate processes, as two replicas running `migrate` would be: Alembic's `context` is a
    # process-wide global, so two upgrades in threads of one process interfere regardless of
    # the database.
    name = "swarmscribe_migrate_race"
    url = urlunsplit(urlsplit(database_url)._replace(path="/" + name))
    asyncio.run(_recreate(database_url, name))
    try:
        start_at = str(time.time() + 3)  # both start together, after interpreter start-up
        runs = [
            subprocess.Popen(
                [sys.executable, "-c", UPGRADE_SCRIPT, url, start_at],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for _ in range(2)
        ]
        outcomes = [
            (run.communicate(timeout=120)[1].decode()[-2000:], run.returncode) for run in runs
        ]
        assert [code for _, code in outcomes] == [0, 0], outcomes

        async def revision() -> str:
            conn = await asyncpg.connect(url)
            try:
                return await conn.fetchval("select version_num from alembic_version")
            finally:
                await conn.close()

        assert asyncio.run(revision()) == head_revision()
    finally:
        asyncio.run(_recreate(database_url, name, drop_only=True))


async def test_default_settings_profiles_are_seeded(sessionmaker):
    async with sessionmaker() as session:
        profiles = (await session.scalars(select(SettingsProfile))).all()
    found = {p.name: (p.device, p.model, p.compute_type, p.temperatures) for p in profiles}
    assert found == {
        "cuda": ("cuda", "large-v3", "float16", [0.0, 0.2, 0.4]),
        "cpu": ("cpu", "distil-large-v3", "int8", [0.0, 0.2, 0.4]),
    }
