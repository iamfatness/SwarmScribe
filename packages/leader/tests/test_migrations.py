from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select
from swarmscribe_leader.db.migrate import current_revision, head_revision
from swarmscribe_leader.db.models import Base, SettingsProfile


async def test_migrations_produce_exactly_the_models(engine):
    async with engine.connect() as conn:
        diff = await conn.run_sync(
            lambda sync_conn: compare_metadata(MigrationContext.configure(sync_conn), Base.metadata)
        )
    assert diff == []


async def test_database_is_at_the_head_revision(engine):
    assert head_revision() == "0001"
    assert await current_revision(engine) == "0001"


async def test_default_settings_profiles_are_seeded(sessionmaker):
    async with sessionmaker() as session:
        profiles = (await session.scalars(select(SettingsProfile))).all()
    found = {p.name: (p.device, p.model, p.compute_type, p.temperatures) for p in profiles}
    assert found == {
        "cuda": ("cuda", "large-v3", "float16", [0.0, 0.2, 0.4]),
        "cpu": ("cpu", "distil-large-v3", "int8", [0.0, 0.2, 0.4]),
    }
