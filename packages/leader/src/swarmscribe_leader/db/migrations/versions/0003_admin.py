"""administration: who cancelled a job, no-speech results, on-demand scan requests

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-03

Hand-written.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("cancelled_by", sa.Text(), nullable=True))
    op.add_column(
        "job_results",
        sa.Column("no_speech", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "storage_locations",
        sa.Column("scan_requested_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("storage_locations", "scan_requested_at")
    op.drop_column("job_results", "no_speech")
    op.drop_column("jobs", "cancelled_by")
