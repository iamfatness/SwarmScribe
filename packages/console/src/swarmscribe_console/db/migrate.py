from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.script.revision import RevisionError
from alembic.util import CommandError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine
from swarmscribe_leader.db.session import to_async_url

UNDEFINED_TABLE = "42P01"
MIGRATIONS = Path(__file__).parent / "migrations"


def alembic_config(database_url: str | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    if database_url is not None:
        config.set_main_option("sqlalchemy.url", to_async_url(database_url).replace("%", "%%"))
    return config


def upgrade(database_url: str) -> None:
    command.upgrade(alembic_config(database_url), "head")


def head_revision() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def is_known_revision(revision: str) -> bool:
    """Whether `revision` is in this console's history (False: a newer console migrated)."""
    try:
        return ScriptDirectory.from_config(alembic_config()).get_revision(revision) is not None
    except (CommandError, RevisionError):
        return False


async def current_revision(engine: AsyncEngine) -> str | None:
    async with engine.connect() as conn:
        try:
            return await conn.scalar(text("select version_num from alembic_version"))
        except DBAPIError as exc:
            # Only "no alembic_version table" means "not migrated". Any other failure (a
            # permission error, a dropped connection) is the caller's to see, so a readiness
            # probe can tell "cannot query" from "migrations not current".
            if getattr(exc.orig, "sqlstate", None) == UNDEFINED_TABLE:
                return None
            raise
