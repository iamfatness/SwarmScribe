"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
# The previous release's replicas serve on this schema for the length of a rolling upgrade
# (the migration runs first; /readyz stays 200 on a newer schema). upgrade() must leave in
# place everything they use: add in this release, remove in a later one. A step that takes
# something away needs, directly above it, a comment that starts `# contract:` and says why
# the previous release no longer needs it; tests/test_migration_compatibility.py refuses it
# otherwise. Delete this comment once you have read it.
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
