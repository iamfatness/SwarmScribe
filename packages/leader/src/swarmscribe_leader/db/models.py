import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
    }


class _Row:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class StorageLocation(_Row, Base):
    __tablename__ = "storage_locations"

    name: Mapped[str] = mapped_column(String(200), unique=True)
    backend: Mapped[str] = mapped_column(String(16))
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    secret_ref: Mapped[str | None] = mapped_column(String(200))
    input_prefix: Mapped[str] = mapped_column(Text, default="")
    output_location_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("storage_locations.id")
    )
    output_prefix: Mapped[str] = mapped_column(Text, default="transcripts/")
    pool: Mapped[str] = mapped_column(String(100), default="default")
    required_device: Mapped[str] = mapped_column(String(8), default="any")
    scan_interval_s: Mapped[int] = mapped_column(default=900)
    enabled: Mapped[bool] = mapped_column(default=True)
    vocabulary_version: Mapped[int] = mapped_column(default=0)
    vocabulary_hash: Mapped[str | None] = mapped_column(String(64))
    last_scan_at: Mapped[datetime | None]
    last_scan_error: Mapped[str | None] = mapped_column(Text)
    # Set by `ingest`; the scanner clears it once a scan that began after it has finished.
    scan_requested_at: Mapped[datetime | None]


class Recording(_Row, Base):
    __tablename__ = "recordings"
    __table_args__ = (UniqueConstraint("location_id", "key"),)

    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("storage_locations.id"))
    key: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger)
    source_version: Mapped[str] = mapped_column(String(200))
    consent: Mapped[str] = mapped_column(String(16))
    consent_pattern: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime]
    last_seen_at: Mapped[datetime]
    missing: Mapped[bool] = mapped_column(default=False)


class JoinToken(_Row, Base):
    __tablename__ = "join_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    pool: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime]
    max_uses: Mapped[int]
    uses: Mapped[int] = mapped_column(default=0)
    revoked: Mapped[bool] = mapped_column(default=False)
    created_by: Mapped[str] = mapped_column(Text)


class Follower(_Row, Base):
    __tablename__ = "followers"

    pool: Mapped[str] = mapped_column(String(100))
    capabilities: Mapped[dict[str, Any]] = mapped_column(default=dict)
    credential_hash: Mapped[str] = mapped_column(String(64), unique=True)
    state: Mapped[str] = mapped_column(String(16), default="active")
    last_seen_at: Mapped[datetime]
    join_token_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("join_tokens.id"))


class SettingsProfile(_Row, Base):
    __tablename__ = "settings_profiles"

    name: Mapped[str] = mapped_column(String(100), unique=True)
    device: Mapped[str] = mapped_column(String(8), unique=True)
    model: Mapped[str] = mapped_column(String(100))
    compute_type: Mapped[str] = mapped_column(String(32))
    temperatures: Mapped[list[Any]]


class Job(_Row, Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_recording_id", "recording_id"),)

    recording_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recordings.id"))
    source_version: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(16), default="queued")
    pool: Mapped[str] = mapped_column(String(100))
    required_device: Mapped[str] = mapped_column(String(8), default="any")
    priority: Mapped[int] = mapped_column(default=0)
    attempts: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int]
    lease_id: Mapped[uuid.UUID | None]
    leased_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("followers.id"))
    lease_expires_at: Mapped[datetime | None]
    vocabulary_version: Mapped[int | None]
    settings_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("settings_profiles.id")
    )
    failure_reason: Mapped[str | None] = mapped_column(Text)
    # The administrator who cancelled the job; None when the system cancelled it. Scanning
    # never recreates a job an administrator cancelled.
    cancelled_by: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None]
    outputs_flagged_for_deletion: Mapped[bool] = mapped_column(default=False)
    # Not before: a job whose claim could not be built is pushed back so others are reached.
    available_at: Mapped[datetime] = mapped_column(server_default=func.now())


# Serves the claim query: queued jobs of a pool, highest priority first, then oldest.
Index("ix_jobs_claim", Job.state, Job.pool, Job.priority.desc(), Job.created_at)


class JobAttempt(_Row, Base):
    __tablename__ = "job_attempts"
    __table_args__ = (Index("ix_job_attempts_lease", "job_id", "lease_id"),)

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"))
    follower_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("followers.id"))
    lease_id: Mapped[uuid.UUID]
    started_at: Mapped[datetime]
    ended_at: Mapped[datetime | None]
    outcome: Mapped[str | None] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text)


class JobResult(_Row, Base):
    __tablename__ = "job_results"

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), unique=True)
    source_sha256: Mapped[str] = mapped_column(String(64))
    txt_sha256: Mapped[str] = mapped_column(String(64))
    srt_sha256: Mapped[str] = mapped_column(String(64))
    segments_sha256: Mapped[str] = mapped_column(String(64))
    duration_s: Mapped[float | None]
    engine_version: Mapped[str | None] = mapped_column(String(50))
    vocabulary_terms_used: Mapped[list[Any]] = mapped_column(default=list)
    corrections_applied: Mapped[list[Any]] = mapped_column(default=list)
    low_confidence_words: Mapped[list[Any]] = mapped_column(default=list)
    # The recording has no speech: empty .txt and .srt, a segments.json without segments.
    no_speech: Mapped[bool] = mapped_column(default=False, server_default=false())


class VocabularyVersion(_Row, Base):
    __tablename__ = "vocabularies"
    __table_args__ = (UniqueConstraint("scope", "version"),)

    scope: Mapped[str] = mapped_column(String(64))
    version: Mapped[int]
    terms: Mapped[list[Any]] = mapped_column(default=list)
    corrections: Mapped[list[Any]] = mapped_column(default=list)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(Text)


class AuditEntry(_Row, Base):
    __tablename__ = "audit_log"

    actor: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String(100))
    subject_type: Mapped[str | None] = mapped_column(String(50))
    subject_id: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)
