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
    assert head_revision() == "0002"
    assert await current_revision(engine) == "0002"


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


async def test_default_settings_profiles_are_seeded(sessionmaker):
    async with sessionmaker() as session:
        profiles = (await session.scalars(select(SettingsProfile))).all()
    found = {p.name: (p.device, p.model, p.compute_type, p.temperatures) for p in profiles}
    assert found == {
        "cuda": ("cuda", "large-v3", "float16", [0.0, 0.2, 0.4]),
        "cpu": ("cpu", "distil-large-v3", "int8", [0.0, 0.2, 0.4]),
    }
