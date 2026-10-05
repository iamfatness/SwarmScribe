from typing import Annotated, Literal

from pydantic import Field

from .base import Sha256, WireModel
from .segments import Device, JobSettings
from .vocabulary import Vocabulary

Directive = Literal["continue", "cancel", "drain"]
DIRECTIVE_HEADER = "X-SwarmScribe-Directive"
"""Response header on a claim answered 204. Its value is a Directive; the leader sends only
`drain`, to a draining follower, which will be given nothing more. Absent otherwise."""
MODEL_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}(/[A-Za-z0-9][A-Za-z0-9._-]{0,63})?$"
MODEL_NAME_MAX_LENGTH = 100
"""A model is named, never located: followers load it by this name, and a path here would
make them read their own disks. `owner/name` is a Hugging Face repository. The leader
accepts a name by this rule and the follower checks it again before loading; one copy."""
FailureCode = Literal["source_changed", "undecodable", "engine_error", "out_of_resources", "other"]


class Link(WireModel):
    """A short-lived, single-object URL. Identical for every storage backend."""

    url: str = Field(repr=False)  # a capability: never in a log line
    method: Literal["GET", "PUT"]
    headers: dict[str, str] = Field(default_factory=dict)


CapabilityText = Annotated[str, Field(max_length=200)]
"""A follower-supplied capability string; bounded because the leader stores it."""


class Capabilities(WireModel):
    device: Device
    gpu_name: CapabilityText | None = None
    gpu_memory_mb: int | None = None
    models: list[CapabilityText] = Field(max_length=50)
    engine_version: CapabilityText
    pool: CapabilityText


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
    vocabulary: Vocabulary
    source_version: str = Field(min_length=1)


class HeartbeatRequest(WireModel):
    lease_id: str
    progress: float | None = Field(default=None, ge=0.0, le=1.0)


class HeartbeatResponse(WireModel):
    directive: Directive


class OutputChecksums(WireModel):
    source: Sha256
    txt: Sha256
    srt: Sha256
    segments_json: Sha256


class SubmitRequest(WireModel):
    lease_id: str
    checksums: OutputChecksums


class SubmitResponse(WireModel):
    accepted: bool


class FailRequest(WireModel):
    lease_id: str
    code: FailureCode
    reason: str = Field(max_length=2000)
    retryable: bool


class ReleaseRequest(WireModel):
    lease_id: str


class LinksRequest(WireModel):
    lease_id: str


class JobLinks(WireModel):
    """Fresh links for a job, given to the follower that holds its lease."""

    download_url: Link
    upload_urls: UploadUrls


class ErrorBody(WireModel):
    """Body of every error response from the leader."""

    code: str
    message: str
