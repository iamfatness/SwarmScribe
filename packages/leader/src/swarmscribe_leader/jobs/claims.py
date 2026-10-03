import hashlib
from collections.abc import Callable
from datetime import timedelta
from typing import Literal

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from swarmscribe_protocol import (
    ClaimResponse,
    JobSettings,
    OutputChecksums,
    SegmentsDocument,
    UploadUrls,
    Vocabulary,
)

from ..config import Settings
from ..db.models import Follower, Job, Recording, SettingsProfile, StorageLocation
from ..errors import LeaderError
from ..storage.base import StorageBackend, StorageError, StorageUnavailable
from .store import OutputsCheck

BackendFactory = Callable[[StorageLocation], StorageBackend]
OutputProblem = Literal["outputs_missing", "checksum_mismatch", "outputs_inconsistent"]
NO_SPEECH_SEGMENTS_LIMIT = 1024 * 1024


def output_keys(prefix: str, key: str) -> dict[str, str]:
    base = prefix + key
    return {"txt": base + ".txt", "srt": base + ".srt", "segments_json": base + ".segments.json"}


async def _places(
    session: AsyncSession, job: Job
) -> tuple[Recording, StorageLocation, StorageLocation]:
    recording = await session.get(Recording, job.recording_id)
    source = await session.get(StorageLocation, recording.location_id)
    target = source
    if source.output_location_id is not None:
        target = await session.get(StorageLocation, source.output_location_id)
    return recording, source, target


def device_of(follower: Follower) -> str:
    return follower.capabilities.get("device", "cpu")


async def profile_for(session: AsyncSession, device: str) -> SettingsProfile | None:
    return await session.scalar(select(SettingsProfile).where(SettingsProfile.device == device))


async def build_claim(
    session: AsyncSession,
    job: Job,
    follower: Follower,
    *,
    settings: Settings,
    backend_factory: BackendFactory,
    profile: SettingsProfile | None = None,
) -> ClaimResponse:
    recording, source, target = await _places(session, job)
    download = backend_factory(source).download_link(
        recording.key, job.source_version, timedelta(seconds=settings.download_link_ttl_seconds)
    )
    uploader = backend_factory(target)
    upload_ttl = timedelta(seconds=settings.upload_link_ttl_seconds)
    uploads = UploadUrls(
        **{
            name: uploader.upload_link(
                key, upload_ttl, job_id=str(job.id), lease_id=str(job.lease_id)
            )
            for name, key in output_keys(source.output_prefix, recording.key).items()
        }
    )
    device = device_of(follower)
    if profile is None:
        profile = await profile_for(session, device)
    if profile is None:
        raise LeaderError(f"no settings profile for device {device!r}", code="no_settings_profile")
    job.settings_profile_id = profile.id
    job.vocabulary_version = 0
    return ClaimResponse(
        job_id=str(job.id),
        lease_id=str(job.lease_id),
        download_url=download,
        upload_urls=uploads,
        settings=JobSettings(
            model=profile.model,
            compute_type=profile.compute_type,
            temperatures=tuple(profile.temperatures),
        ),
        vocabulary=Vocabulary(version=0),
        source_version=job.source_version,
    )


EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


async def _has_no_segments(backend: StorageBackend, key: str) -> bool:
    """Whether segments.json is a valid, small document listing no segments: the only
    thing an empty .txt and .srt may sit beside."""
    info = await backend.stat(key)
    if info is None or info.size > NO_SPEECH_SEGMENTS_LIMIT:
        return False
    try:
        text = await backend.read_text(key)
        document = SegmentsDocument.model_validate_json(text or "")
    except StorageUnavailable:
        raise
    except (StorageError, ValidationError):
        return False
    return not document.segments


async def outputs_verified(
    session: AsyncSession,
    job: Job,
    checksums: OutputChecksums,
    *,
    backend_factory: BackendFactory,
) -> OutputsCheck:
    """Hash the three stored outputs and compare them with the submitted checksums.

    Each output's version is noted before it is hashed, so the caller can confirm later,
    cheaply, that nothing was replaced. A missing output, or an empty segments.json, is
    `outputs_missing`. An empty .txt and .srt are a no-speech result, accepted only together
    and only beside a segments.json with no segments.
    """
    recording, source, target = await _places(session, job)
    # Hashing reads every byte: do not sit "idle in transaction" meanwhile. The objects stay
    # usable (sessions here never expire on commit); submit locks and reloads the job after.
    await session.commit()
    backend = backend_factory(target)
    keys = output_keys(source.output_prefix, recording.key)
    digests: dict[str, str] = {}
    versions: list[tuple[str, str]] = []
    for name, key in keys.items():
        before = await backend.stat(key)
        digest = await backend.sha256(key)
        if before is None or digest is None:
            return OutputsCheck(problem="outputs_missing")
        digests[name] = digest
        versions.append((key, before.version))
    if digests["segments_json"] == EMPTY_SHA256:
        return OutputsCheck(problem="outputs_missing")
    if any(digests[name] != getattr(checksums, name) for name in keys):
        return OutputsCheck(problem="checksum_mismatch")
    empty = {name for name in ("txt", "srt") if digests[name] == EMPTY_SHA256}
    if empty and (
        empty != {"txt", "srt"} or not await _has_no_segments(backend, keys["segments_json"])
    ):
        return OutputsCheck(problem="outputs_inconsistent")
    return OutputsCheck(no_speech=bool(empty), versions=tuple(versions))


async def outputs_unchanged(
    session: AsyncSession,
    job: Job,
    check: OutputsCheck,
    *,
    backend_factory: BackendFactory,
) -> bool:
    """Whether every output still has the version it had when it was hashed."""
    _recording, _source, target = await _places(session, job)
    backend = backend_factory(target)
    for key, version in check.versions:
        info = await backend.stat(key)
        if info is None or info.version != version:
            return False
    return True
