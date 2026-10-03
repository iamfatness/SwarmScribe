"""fleet console credentials: name, role cap and the credential's SHA-256

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-03

Hand-written.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "console_credentials",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("credential_hash", sa.String(length=64), nullable=False),
        sa.Column("max_role", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "max_role IN ('viewer','operator','admin')",
            name="ck_console_credentials_max_role",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("credential_hash"),
    )
    # Names are unique ignoring case; the code checks first, the database has the last word.
    op.create_index(
        "uq_console_credentials_name_lower",
        "console_credentials",
        [sa.text("lower(name)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("console_credentials")
