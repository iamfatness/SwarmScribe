"""The console's own database (fleet console spec 5.1). Leaders never share it.

Case-insensitive uniqueness of leader names is the functional index `uq_leaders_name_lower`,
declared here and created by migration 0001 so the two agree."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ROLE_CHECK = "role IN ('viewer','operator','admin')"
KIND_CHECK = "principal_kind IN ('entra_group','google_group','email','domain')"
SCOPE_CHECK = "scope = 'all' OR scope LIKE 'leader:_%' OR scope LIKE 'label:_%=_%'"
NAME_CHECK = "name ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$'"


class Base(DeclarativeBase):
    type_annotation_map = {
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
    }


class Leader(Base):
    """A registered leader. `credential` is its console credential sealed by
    crypto.ConsoleKeys; the last five columns are the poller's state (C2b)."""

    __tablename__ = "leaders"
    __table_args__ = (
        CheckConstraint(NAME_CHECK, name="ck_leaders_name"),
        CheckConstraint("base_url LIKE 'https://%'", name="ck_leaders_base_url_https"),
        CheckConstraint("jsonb_typeof(labels) = 'object'", name="ck_leaders_labels_object"),
        Index("uq_leaders_name_lower", func.lower(text("name")), unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    base_url: Mapped[str] = mapped_column(Text)
    labels: Mapped[dict[str, Any]] = mapped_column(
        default=dict, server_default=text("'{}'::jsonb")
    )
    enabled: Mapped[bool] = mapped_column(default=True, server_default=true())
    credential: Mapped[bytes] = mapped_column(LargeBinary)
    credential_updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    credential_updated_by: Mapped[str] = mapped_column(Text)
    credential_revoked_at: Mapped[datetime | None]
    added_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_polled_at: Mapped[datetime | None]
    last_success_at: Mapped[datetime | None]
    consecutive_failures: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(String(64))


class RoleGrant(Base):
    __tablename__ = "role_grants"
    __table_args__ = (
        CheckConstraint(ROLE_CHECK, name="ck_role_grants_role"),
        CheckConstraint(KIND_CHECK, name="ck_role_grants_principal_kind"),
        CheckConstraint(SCOPE_CHECK, name="ck_role_grants_scope"),
        UniqueConstraint(
            "scope", "principal_kind", "principal", name="uq_role_grants_scope_principal"
        ),
        Index("ix_role_grants_principal", "principal_kind", "principal"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    role: Mapped[str] = mapped_column(String(16))
    scope: Mapped[str] = mapped_column(String(400))
    principal_kind: Mapped[str] = mapped_column(String(16))
    principal: Mapped[str] = mapped_column(String(320))
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ConsoleAdmin(Base):
    __tablename__ = "console_admins"
    __table_args__ = (
        CheckConstraint(KIND_CHECK, name="ck_console_admins_principal_kind"),
        UniqueConstraint("principal_kind", "principal", name="uq_console_admins_principal"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    principal_kind: Mapped[str] = mapped_column(String(16))
    principal: Mapped[str] = mapped_column(String(320))
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Snapshot(Base):
    """One poll of one leader. `status` is the leader's /v1/admin/status answer when the
    poll succeeded, else None; `outcome` is "ok" or the failure's code."""

    __tablename__ = "snapshots"
    __table_args__ = (
        Index("ix_snapshots_leader_taken", "leader_id", "taken_at"),
        Index("ix_snapshots_taken", "taken_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    leader_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("leaders.id", ondelete="CASCADE")
    )
    taken_at: Mapped[datetime]
    reachable: Mapped[bool]
    outcome: Mapped[str] = mapped_column(String(64))
    status: Mapped[dict[str, Any] | None]


class AuditEntry(Base):
    """The console's own audit log. `leader` is a name, not a key, so entries outlive the
    leader's registration."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_at", "at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[datetime] = mapped_column(server_default=func.now())
    actor: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String(64))
    leader: Mapped[str | None] = mapped_column(String(100))
    target: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(
        default=dict, server_default=text("'{}'::jsonb")
    )


class ConsoleSession(Base):
    """A signed-in browser. The cookie holds the session id; only its SHA-256 is here."""

    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_expires_at", "expires_at"),)

    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(16))
    issuer: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    principals: Mapped[list[Any]] = mapped_column(default=list)
    created_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]
    expires_at: Mapped[datetime]


class LoginAttempt(Base):
    """A sign-in that went to the identity provider and has not come back yet. Single use;
    the state and the browser binding are kept only as SHA-256."""

    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_expires_at", "expires_at"),)

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    browser_hash: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(16))
    nonce: Mapped[str] = mapped_column(Text)
    code_verifier: Mapped[str] = mapped_column(Text)
    return_to: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime]
