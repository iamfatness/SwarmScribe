"""Request and response bodies of the /v1/admin API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

JobState = Literal["queued", "leased", "completed", "failed", "cancelled"]
FollowerState = Literal["active", "draining", "revoked", "gone"]
RequiredDevice = Literal["any", "cuda", "cpu"]
NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"


class WhoAmI(BaseModel):
    provider: str
    issuer: str
    subject: str
    email: str | None
    role: str


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


class Status(BaseModel):
    jobs: dict[str, int]
    pools: list[PoolQueue]
    followers: dict[str, int]
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
