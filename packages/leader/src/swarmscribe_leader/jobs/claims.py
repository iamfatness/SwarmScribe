import hashlib
from collections.abc import Callable
from datetime import timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from swarmscribe_protocol import (
    ClaimResponse,
    JobSettings,
    OutputChecksums,
    UploadUrls,
    Vocabulary,
)

from ..config import Settings
from ..db.models import Follower, Job, Recording, SettingsProfile, StorageLocation
from ..errors import LeaderError
from ..storage.base import StorageBackend

BackendFactory = Callable[[StorageLocation], StorageBackend]
OutputProblem = Literal["outputs_missing", "checksum_mismatch"]


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


async def outputs_verified(
    session: AsyncSession,
    job: Job,
    checksums: OutputChecksums,
    *,
    backend_factory: BackendFactory,
) -> OutputProblem | None:
    """Check the three stored outputs against the submitted checksums by hashing them
    through the backend. None when they match; otherwise what is wrong."""
    recording, source, target = await _places(session, job)
    backend = backend_factory(target)
    mismatch = False
    for name, key in output_keys(source.output_prefix, recording.key).items():
        digest = await backend.sha256(key)
        if digest is None or digest == EMPTY_SHA256:
            return "outputs_missing"
        mismatch = mismatch or digest != getattr(checksums, name)
    return "checksum_mismatch" if mismatch else None
