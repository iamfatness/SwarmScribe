from typing import Literal

from pydantic import Field

from .base import WireModel
from .segments import Device, JobSettings

Directive = Literal["continue", "cancel", "drain"]


class Link(WireModel):
    """A short-lived, single-object URL. Identical for every storage backend."""

    url: str
    method: Literal["GET", "PUT"]
    headers: dict[str, str] = Field(default_factory=dict)


class Capabilities(WireModel):
    device: Device
    gpu_name: str | None = None
    gpu_memory_mb: int | None = None
    models: list[str]
    engine_version: str
    pool: str


class RegisterRequest(WireModel):
    join_token: str
    protocol_version: int
    capabilities: Capabilities


class RegisterResponse(WireModel):
    follower_id: str
    credential: str
    heartbeat_interval: int = Field(gt=0)
    lease_seconds: int = Field(gt=0)


class UploadUrls(WireModel):
    txt: Link
    srt: Link
    segments_json: Link


class ClaimResponse(WireModel):
    job_id: str
    lease_id: str
    download_url: Link
    upload_urls: UploadUrls
    settings: JobSettings
    glossary: list[str]
    source_checksum: str


class HeartbeatRequest(WireModel):
    lease_id: str
    progress: float | None = Field(default=None, ge=0.0, le=1.0)


class HeartbeatResponse(WireModel):
    directive: Directive


class OutputChecksums(WireModel):
    txt: str
    srt: str
    segments_json: str


class SubmitRequest(WireModel):
    lease_id: str
    checksums: OutputChecksums


class SubmitResponse(WireModel):
    accepted: bool


class FailRequest(WireModel):
    lease_id: str
    reason: str
    retryable: bool


class ReleaseRequest(WireModel):
    lease_id: str
