import asyncio
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import CheckConstraint, delete, select, text
from sqlalchemy.exc import IntegrityError
from swarmscribe_leader.db.migrate import current_revision, head_revision
from swarmscribe_leader.db.models import Base, SettingsProfile, StorageLocation


async def test_migrations_produce_exactly_the_models(engine):
    async with engine.connect() as conn:
        diff = await conn.run_sync(
            lambda sync_conn: compare_metadata(MigrationContext.configure(sync_conn), Base.metadata)
        )
    assert diff == []


async def test_database_is_at_the_head_revision(engine):
    assert head_revision() == "0004"
    assert await current_revision(engine) == "0004"


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


async def test_locations_created_without_channel_settings_are_mono(sessionmaker):
    # As rows created before migration 0004, or seeded by SQL that predates it, are.
    async with sessionmaker() as session:
        await session.execute(
            text(
                "insert into storage_locations (id, name, backend, config, input_prefix,"
                " output_prefix, pool, required_device, scan_interval_s, enabled,"
                " vocabulary_version) values (gen_random_uuid(), 'older', 'local',"
                " '{}'::jsonb, '', 'transcripts/', 'default', 'any', 900, true, 0)"
            )
        )
        await session.commit()
        location = await session.scalar(
            select(StorageLocation).where(StorageLocation.name == "older")
        )
    assert (location.channel_mode, location.channel_labels) == ("mono", ["Left", "Right"])


BAD_CHANNEL_SETTINGS = [
    ("channel_mode", "'sideways'"),
    ("channel_labels", "'\"ab\"'::jsonb"),
    ("channel_labels", "'[\"a\", \"b\", \"c\"]'::jsonb"),
    ("channel_labels", "'{\"x\": 1, \"y\": 2}'::jsonb"),
    ("channel_labels", "'[1, 2]'::jsonb"),
]


@pytest.mark.parametrize(("column", "value"), BAD_CHANNEL_SETTINGS)
async def test_a_location_cannot_be_inserted_with_bad_channel_settings(
    sessionmaker, column, value
):
    async with sessionmaker() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "insert into storage_locations (id, name, backend, config, input_prefix,"
                    f" output_prefix, pool, required_device, scan_interval_s, enabled,"
                    f" vocabulary_version, {column}) values (gen_random_uuid(), 'bad-insert',"
                    " 'local', '{}'::jsonb, '', 'transcripts/', 'default', 'any', 900, true, 0,"
                    f" {value})"
                )
            )
        await session.rollback()


@pytest.mark.parametrize(("column", "value"), BAD_CHANNEL_SETTINGS)
async def test_a_location_cannot_be_updated_to_bad_channel_settings(
    sessionmaker, factory, column, value
):
    location = await factory.location()
    async with sessionmaker() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                text(f"update storage_locations set {column} = {value} where id = :id"),
                {"id": location.id},
            )
        await session.rollback()


async def test_the_models_check_constraints_match_the_migrations(engine):
    model = {
        c.name: c.sqltext.text
        for c in StorageLocation.__table__.constraints
        if isinstance(c, CheckConstraint)
    }
    assert sorted(model) == [
        "ck_storage_locations_channel_labels",
        "ck_storage_locations_channel_mode",
    ]
    definitions = (
        "select conname, pg_get_constraintdef(oid) from pg_constraint"
        " where conrelid = '{table}'::regclass and contype = 'c' and conname like 'ck_%'"
    )
    async with engine.connect() as conn:
        real = await conn.execute(text(definitions.format(table="storage_locations")))
        migrated = dict(real.all())
        # The model's own CHECK text, deparsed by Postgres the same way as the migration's.
        clauses = ", ".join(f"constraint {n} check ({t})" for n, t in model.items())
        await conn.execute(
            text(
                "create temp table model_probe (channel_mode varchar(16),"
                f" channel_labels jsonb, {clauses})"
            )
        )
        probe = await conn.execute(text(definitions.format(table="model_probe")))
        from_model = dict(probe.all())
        await conn.rollback()
    assert from_model == migrated


MIGRATE_SCRIPT = """
import sys
from alembic import command
from swarmscribe_leader.db.migrate import alembic_config, upgrade
url = sys.argv[1]
upgrade(url)
command.downgrade(alembic_config(url), "0003")
command.upgrade(alembic_config(url), "head")
"""


def test_0004_downgrades_to_0003_and_upgrades_again(database_url):
    # A separate database and process: the shared test database must stay at head.
    name = "swarmscribe_migrate_roundtrip"
    url = urlunsplit(urlsplit(database_url)._replace(path="/" + name))
    asyncio.run(_recreate(database_url, name))
    try:
        run = subprocess.run(
            [sys.executable, "-c", MIGRATE_SCRIPT, url], capture_output=True, timeout=120
        )
        assert run.returncode == 0, run.stderr.decode()[-2000:]

        async def state() -> tuple[str, list[str]]:
            conn = await asyncpg.connect(url)
            try:
                revision = await conn.fetchval("select version_num from alembic_version")
                constraints = await conn.fetch(
                    "select conname from pg_constraint where conname like 'ck_storage_locations_%'"
                )
                return revision, sorted(row["conname"] for row in constraints)
            finally:
                await conn.close()

        assert asyncio.run(state()) == (
            head_revision(),
            ["ck_storage_locations_channel_labels", "ck_storage_locations_channel_mode"],
        )
    finally:
        asyncio.run(_recreate(database_url, name, drop_only=True))
