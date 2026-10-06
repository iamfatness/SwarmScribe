"""The leader's schema migrations (Alembic), and what `serve` and `/readyz` ask about them.

THE RULE FOR A NEW MIGRATION: it must leave the previous release working. In a rolling
upgrade `migrate` runs first, and the previous release's replicas keep serving on the new
schema until they are replaced: `/readyz` deliberately stays 200 on a schema newer than the
leader (api/health.py). So a change that takes something away is made in two releases:

  1. expand:   add the new column or table; keep, and keep filling, the old one;
  2. contract: a release later, when no running leader uses the old one, remove it.

Dropping or renaming a column or table, narrowing a type, making a column required, or
adding a required column without a server default, all in the release that stops using it,
makes the old replicas answer 500 for the length of the rollout while still Ready.
tests/test_migration_compatibility.py refuses such a step unless a `# contract: <why>`
comment stands above it; nothing checks the rest (a new constraint the old release's writes
break, a changed meaning). A rollback to the previous image needs the same: `serve` refuses
to START on a schema it does not know, so going back means restoring the database.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.script.revision import RevisionError
from alembic.util import CommandError
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
    """Developer tool: write a new revision from the difference between models and database.

    Read what it wrote against the rule at the top of this module before committing it:
    autogenerate turns a renamed or removed model attribute into `drop_column`, which the
    previous release's replicas do not survive."""
    config = alembic_config(database_url)
    command.revision(config, message=message, autogenerate=True, rev_id=rev_id)


def head_revision() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def is_known_revision(revision: str) -> bool:
    """Whether `revision` is in this leader's migration history (False: a newer leader
    migrated the database)."""
    try:
        return ScriptDirectory.from_config(alembic_config()).get_revision(revision) is not None
    except (CommandError, RevisionError):
        return False


async def current_revision(engine: AsyncEngine) -> str | None:
    async with engine.connect() as conn:
        try:
            return await conn.scalar(text("select version_num from alembic_version"))
        except DBAPIError:
            return None
