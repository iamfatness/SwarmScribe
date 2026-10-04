"""fleet console: leaders, grants, console admins, snapshots, audit, sessions, sign-ins

Revision ID: 0001
Revises:
Create Date: 2026-10-03

Hand-written.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KIND_CHECK = "principal_kind IN ('entra_group','google_group','email','domain')"


def _now() -> sa.TextClause:
    return sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "leaders",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column(
            "labels",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("credential", sa.LargeBinary(), nullable=False),
        sa.Column(
            "credential_updated_at",
            sa.DateTime(timezone=True),
            server_default=_now(),
            nullable=False,
        ),
        sa.Column("credential_updated_by", sa.Text(), nullable=False),
        sa.Column("credential_revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("added_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False
        ),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "consecutive_failures", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("last_error", sa.String(length=64), nullable=True),
        sa.CheckConstraint("name ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$'", name="ck_leaders_name"),
        sa.CheckConstraint("base_url LIKE 'https://%'", name="ck_leaders_base_url_https"),
        sa.CheckConstraint("jsonb_typeof(labels) = 'object'", name="ck_leaders_labels_object"),
        sa.CheckConstraint(
            "get_byte(credential, 0) = 1 AND length(credential) > 29",
            name="ck_leaders_credential_sealed",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_leaders_name_lower", "leaders", [sa.text("lower(name)")], unique=True)

    op.create_table(
        "role_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("scope", sa.String(length=400), nullable=False),
        sa.Column("principal_kind", sa.String(length=16), nullable=False),
        sa.Column("principal", sa.String(length=320), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False
        ),
        sa.CheckConstraint("role IN ('viewer','operator','admin')", name="ck_role_grants_role"),
        sa.CheckConstraint(KIND_CHECK, name="ck_role_grants_principal_kind"),
        sa.CheckConstraint(
            "scope = 'all' OR scope LIKE 'leader:_%' OR scope LIKE 'label:_%=_%'",
            name="ck_role_grants_scope",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scope", "principal_kind", "principal", name="uq_role_grants_scope_principal"
        ),
    )
    op.create_index(
        "ix_role_grants_principal", "role_grants", ["principal_kind", "principal"]
    )

    op.create_table(
        "console_admins",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("principal_kind", sa.String(length=16), nullable=False),
        sa.Column("principal", sa.String(length=320), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False
        ),
        sa.CheckConstraint(KIND_CHECK, name="ck_console_admins_principal_kind"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("principal_kind", "principal", name="uq_console_admins_principal"),
    )

    op.create_table(
        "snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("leader_id", sa.BigInteger(), nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reachable", sa.Boolean(), nullable=False),
        sa.Column("outcome", sa.String(length=64), nullable=False),
        sa.Column("status", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["leader_id"], ["leaders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_snapshots_leader_taken", "snapshots", ["leader_id", "taken_at"])
    op.create_index("ix_snapshots_taken", "snapshots", ["taken_at"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("leader", sa.String(length=100), nullable=True),
        sa.Column("target", sa.Text(), nullable=True),
        sa.Column("outcome", sa.String(length=64), nullable=False),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_log_at", "audit_log", ["at"])

    op.create_table(
        "sessions",
        sa.Column("id_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("principals", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id_hash"),
    )
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])

    op.create_table(
        "login_attempts",
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("browser_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("nonce", sa.Text(), nullable=False),
        sa.Column("code_verifier", sa.Text(), nullable=False),
        sa.Column("return_to", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("state_hash"),
    )
    op.create_index("ix_login_attempts_expires_at", "login_attempts", ["expires_at"])


def downgrade() -> None:
    op.drop_table("login_attempts")
    op.drop_table("sessions")
    op.drop_table("audit_log")
    op.drop_table("snapshots")
    op.drop_table("console_admins")
    op.drop_table("role_grants")
    op.drop_table("leaders")
