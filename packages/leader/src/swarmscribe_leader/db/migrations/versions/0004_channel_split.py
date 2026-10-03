"""per-channel transcription: channel mode and labels per storage location

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-03

Hand-written. Existing locations become mono with labels Left and Right.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "storage_locations",
        sa.Column("channel_mode", sa.String(length=16), server_default="mono", nullable=False),
    )
    op.add_column(
        "storage_locations",
        sa.Column(
            "channel_labels",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("""'["Left", "Right"]'::jsonb"""),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_storage_locations_channel_mode",
        "storage_locations",
        "channel_mode IN ('mono','stereo_split','auto')",
    )
    op.create_check_constraint(
        "ck_storage_locations_channel_labels",
        "storage_locations",
        "jsonb_typeof(channel_labels) = 'array' AND jsonb_array_length(channel_labels) = 2"
        " AND jsonb_typeof(channel_labels->0) = 'string'"
        " AND jsonb_typeof(channel_labels->1) = 'string'",
    )


def downgrade() -> None:
    op.drop_constraint("ck_storage_locations_channel_labels", "storage_locations", type_="check")
    op.drop_constraint("ck_storage_locations_channel_mode", "storage_locations", type_="check")
    op.drop_column("storage_locations", "channel_labels")
    op.drop_column("storage_locations", "channel_mode")
