from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from swarmscribe_protocol import ClaimResponse, JobSettings, UploadUrls, Vocabulary

from ..config import Settings
from ..db.models import Follower, Job, Recording, SettingsProfile, StorageLocation
from ..errors import LeaderError
from ..storage.base import StorageBackend

BackendFactory = Callable[[StorageLocation], StorageBackend]


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
            name: uploader.upload_link(key, upload_ttl)
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


async def outputs_present(
    session: AsyncSession, job: Job, *, backend_factory: BackendFactory
) -> bool:
    recording, source, target = await _places(session, job)
    backend = backend_factory(target)
    for key in output_keys(source.output_prefix, recording.key).values():
        info = await backend.stat(key)
        if info is None or info.size == 0:
            return False
    return True
