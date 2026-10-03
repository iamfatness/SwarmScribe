from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from .session import to_async_url

MIGRATIONS = Path(__file__).parent / "migrations"


def alembic_config(database_url: str | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    if database_url is not None:
        config.set_main_option("sqlalchemy.url", to_async_url(database_url).replace("%", "%%"))
    return config


def upgrade(database_url: str) -> None:
    command.upgrade(alembic_config(database_url), "head")


def autogenerate(database_url: str, message: str, rev_id: str) -> None:
    """Developer tool: write a new revision from the difference between models and database."""
    config = alembic_config(database_url)
    command.revision(config, message=message, autogenerate=True, rev_id=rev_id)


def head_revision() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


async def current_revision(engine: AsyncEngine) -> str | None:
    async with engine.connect() as conn:
        try:
            return await conn.scalar(text("select version_num from alembic_version"))
        except DBAPIError:
            return None
