import asyncio

from alembic import context
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from swarmscribe_console.db.models import Base

config = context.config
target_metadata = Base.metadata

# Concurrent `migrate` runs (several replicas starting) queue on this lock. Not the leader's
# key: a console and a leader never share a database, but keep them distinct anyway.
MIGRATION_LOCK_KEY = 0x53430000


def _run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        connection.execute(text("select pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
        context.run_migrations()


async def _main() -> None:
    engine = create_async_engine(config.get_main_option("sqlalchemy.url"))
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations)
            await connection.commit()
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("offline migrations are not supported")
asyncio.run(_main())
