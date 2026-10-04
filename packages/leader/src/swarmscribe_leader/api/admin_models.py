"""Request and response bodies of the /v1/admin API."""

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from swarmscribe_protocol import DEFAULT_CHANNEL_LABELS, ChannelLabels, ChannelMode

from ..storage.base import StorageError
from ..storage.local import validate_key

JobState = Literal["queued", "leased", "completed", "failed", "cancelled"]
FollowerState = Literal["active", "draining", "revoked", "gone"]
RequiredDevice = Literal["any", "cuda", "cpu"]
NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"


class WhoAmI(BaseModel):
    provider: str  # "entra", "google", or "console" for a console's delegated request
    issuer: str | None  # None only for a console's poller
    subject: str | None
    email: str | None
    role: str
    console: str | None = None


class LoginProvider(BaseModel):
    name: str
    client_id: str
    device_authorization_endpoint: str
    token_endpoint: str
    scope: str
    client_secret: str | None = None


class LoginConfig(BaseModel):
    providers: list[LoginProvider]


class PoolQueue(BaseModel):
    pool: str
    queued: int
    leased: int


class LocationStatus(BaseModel):
    name: str
    backend: str
    enabled: bool
    last_scan_at: datetime | None
    last_scan_error: str | None
    scan_requested: bool
    recordings: int
    consented: int


class PoolFollowers(BaseModel):
    pool: str
    active: int = 0
    draining: int = 0
    revoked: int = 0
    gone: int = 0


class Status(BaseModel):
    jobs: dict[str, int]
    pools: list[PoolQueue]
    followers: dict[str, int]
    # Added for the fleet console's poller; additive, so older clients ignore it.
    follower_pools: list[PoolFollowers] = Field(default_factory=list)
    completed_last_hour: int
    failed_attempts_last_day: int
    locations: list[LocationStatus]


class LocationOut(BaseModel):
    id: str
    name: str
    backend: str
    root: str | None
    input_prefix: str
    output_prefix: str
    pool: str
    required_device: str
    scan_interval_s: int
    enabled: bool
    last_scan_at: datetime | None
    last_scan_error: str | None
    scan_requested: bool
    channel_mode: str
    channel_labels: list[str]


class JobOut(BaseModel):
    id: str
    state: str
    location: str
    key: str
    priority: int
    attempts: int
    max_attempts: int
    pool: str
    leased_by: str | None
    failure_reason: str | None
    cancelled_by: str | None
    no_speech: bool | None
    created_at: datetime
    completed_at: datetime | None


class FollowerOut(BaseModel):
    id: str
    pool: str
    state: str
    device: str | None
    last_seen_at: datetime
    created_at: datetime
    leases: int


class TokenOut(BaseModel):
    id: str
    pool: str
    expires_at: datetime
    max_uses: int
    uses: int
    revoked: bool
    created_by: str
    created_at: datetime


class ConsentCounts(BaseModel):
    name: str
    consented: int
    not_consented: int
    withdrawn: int
    missing: int


class FlaggedOutputs(BaseModel):
    job_id: str
    location: str
    key: str
    completed_at: datetime | None
    output_location: str
    outputs: list[str]


class ConsentReport(BaseModel):
    locations: list[ConsentCounts]
    flagged: list[FlaggedOutputs]
    truncated: bool


class LocationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=NAME_PATTERN)
    root: str = Field(min_length=1, max_length=1000)
    input_prefix: str = Field(default="", max_length=1000)
    output_prefix: str = Field(default="transcripts/", max_length=1000)
    pool: str = Field(default="default", pattern=NAME_PATTERN)
    required_device: RequiredDevice = "any"
    scan_interval_s: int = Field(default=900, ge=30, le=7 * 86400)
    channel_mode: ChannelMode = "mono"
    channel_labels: ChannelLabels = DEFAULT_CHANNEL_LABELS

    @field_validator("root")
    @classmethod
    def _absolute_folder(cls, value: str) -> str:
        if any(ord(ch) < 0x20 for ch in value) or not Path(value).is_absolute():
            raise ValueError("root must be an absolute folder path")
        return value

    @field_validator("input_prefix", "output_prefix")
    @classmethod
    def _relative_prefix(cls, value: str) -> str:
        if value:
            try:
                if not value.endswith("/"):
                    raise StorageError("no trailing slash")
                validate_key(value[:-1])
            except StorageError:
                raise ValueError(
                    "must be a relative folder ending in one /, such as incoming/"
                ) from None
        return value

    @model_validator(mode="after")
    def _labels_need_a_split_mode(self) -> "LocationIn":
        if "channel_labels" in self.model_fields_set and self.channel_mode == "mono":
            raise ValueError("channel_labels needs channel_mode stereo_split or auto")
        return self


class PriorityIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: int = Field(ge=-1000, le=1000, strict=True)


class TokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pool: str = Field(default="default", pattern=NAME_PATTERN)
    expires_in_seconds: int = Field(default=7 * 86400, ge=60, le=90 * 86400)
    max_uses: int = Field(default=1, ge=1, le=10_000)


class TokenCreated(BaseModel):
    id: str
    token: str
    pool: str
    expires_at: datetime
    max_uses: int


class ScanRequested(BaseModel):
    name: str
    requested_at: datetime


class FollowerRevoked(BaseModel):
    id: str
    state: str
    released: int


ConsoleRole = Literal["viewer", "operator", "admin"]


class ConsoleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=NAME_PATTERN)
    max_role: ConsoleRole  # no default: the cap is the administrator's decision


class ConsoleCreated(BaseModel):
    id: str
    name: str
    max_role: str
    credential: str


class ConsoleOut(BaseModel):
    id: str
    name: str
    max_role: str
    revoked: bool
    revoked_at: datetime | None
    revoked_by: str | None
    created_by: str
    created_at: datetime
