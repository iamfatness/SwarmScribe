"""claim back-off, claim/recording indexes, one settings profile per device

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-02

Hand-written.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.drop_index("ix_jobs_claim", table_name="jobs")
    op.create_index(
        "ix_jobs_claim", "jobs", ["state", "pool", sa.text("priority DESC"), "created_at"]
    )
    op.create_index("ix_jobs_recording_id", "jobs", ["recording_id"])
    op.create_unique_constraint("settings_profiles_device_key", "settings_profiles", ["device"])


def downgrade() -> None:
    op.drop_constraint("settings_profiles_device_key", "settings_profiles", type_="unique")
    op.drop_index("ix_jobs_recording_id", table_name="jobs")
    op.drop_index("ix_jobs_claim", table_name="jobs")
    op.create_index("ix_jobs_claim", "jobs", ["state", "pool", "priority", "created_at"])
    op.drop_column("jobs", "available_at")
