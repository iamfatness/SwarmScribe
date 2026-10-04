"""follower support: pool tokens, the follower's pool token, when a lease's links were issued

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-04

Hand-written.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pool_tokens",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("pool", sa.String(length=100), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("registrations", sa.Integer(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("token_hash"),
    )
    # Names are unique ignoring case; the code checks first, the database has the last word.
    op.create_index(
        "uq_pool_tokens_name_lower", "pool_tokens", [sa.text("lower(name)")], unique=True
    )
    op.add_column("followers", sa.Column("pool_token_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "followers_pool_token_id_fkey", "followers", "pool_tokens", ["pool_token_id"], ["id"]
    )
    op.create_index("ix_followers_pool_token", "followers", ["pool_token_id", "state"])
    op.add_column(
        "jobs", sa.Column("links_issued_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("jobs", "links_issued_at")
    op.drop_index("ix_followers_pool_token", table_name="followers")
    op.drop_constraint("followers_pool_token_id_fkey", "followers", type_="foreignkey")
    op.drop_column("followers", "pool_token_id")
    op.drop_table("pool_tokens")
