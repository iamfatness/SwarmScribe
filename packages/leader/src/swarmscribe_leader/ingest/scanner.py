import logging
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .. import audit
from ..db.models import Job, Recording, StorageLocation
from ..jobs.store import OPEN_STATES, cancel
from ..storage.base import StorageBackend
from .consent import compile_consent, first_match, is_recording

logger = logging.getLogger(__name__)


@dataclass
class ScanSummary:
    listed: int = 0
    consented: int = 0
    not_consented: int = 0
    withdrawn: int = 0
    missing: int = 0
    jobs_created: int = 0
    jobs_cancelled: int = 0
    flags_cleared: int = 0


def _consent_state(previous: str | None, pattern: str | None) -> str:
    if pattern is not None:
        return "consented"
    if previous in ("consented", "withdrawn"):
        return "withdrawn"
    return "not_consented"


async def scan_location(
    session: AsyncSession,
    location: StorageLocation,
    backend: StorageBackend,
    *,
    now: datetime,
    max_attempts: int,
) -> ScanSummary:
    summary = ScanSummary()
    patterns = compile_consent(await backend.read_text("consent.txt"))
    existing = {
        r.key: r
        for r in (
            await session.scalars(select(Recording).where(Recording.location_id == location.id))
        ).all()
    }
    seen: set[str] = set()
    async for obj in backend.list(location.input_prefix):
        if not obj.key.startswith(location.input_prefix) or not is_recording(obj.key):
            continue
        seen.add(obj.key)
        summary.listed += 1
        pattern = first_match(patterns, obj.key[len(location.input_prefix) :])
        recording = existing.get(obj.key)
        if recording is None:
            recording = Recording(
                id=uuid.uuid4(),
                location_id=location.id,
                key=obj.key,
                size=obj.size,
                source_version=obj.version,
                consent=_consent_state(None, pattern),
                consent_pattern=pattern,
                first_seen_at=now,
                last_seen_at=now,
                missing=False,
            )
            session.add(recording)
            existing[obj.key] = recording
        else:
            recording.size = obj.size
            recording.source_version = obj.version
            recording.consent = _consent_state(recording.consent, pattern)
            recording.consent_pattern = pattern
            recording.last_seen_at = now
            recording.missing = False
    for key, recording in existing.items():
        if key not in seen:
            if key.startswith(location.input_prefix):
                pattern = first_match(patterns, key[len(location.input_prefix) :])
                recording.consent = _consent_state(recording.consent, pattern)
                recording.consent_pattern = pattern
            recording.missing = True
            summary.missing += 1
        elif recording.consent == "consented":
            summary.consented += 1
        elif recording.consent == "withdrawn":
            summary.withdrawn += 1
        else:
            summary.not_consented += 1
    await session.flush()

    by_id = {r.id: r for r in existing.values()}
    open_jobs = (
        await session.scalars(
            select(Job)
            .where(Job.recording_id.in_(list(by_id)), Job.state.in_(OPEN_STATES))
            .order_by(Job.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    for job in open_jobs:
        recording = by_id[job.recording_id]
        if recording.missing:
            reason = "recording missing"
        elif recording.consent != "consented":
            reason = "consent withdrawn"
        elif job.source_version != recording.source_version:
            reason = "recording changed"
        else:
            continue
        await cancel(session, job, now=now, reason=reason)
        summary.jobs_cancelled += 1

    withdrawn = [r.id for r in by_id.values() if r.consent == "withdrawn"]
    if withdrawn:
        await session.execute(
            update(Job)
            .where(Job.recording_id.in_(withdrawn), Job.state == "completed")
            .values(outputs_flagged_for_deletion=True)
        )
    # Consent restored: the finished outputs are wanted again and must not be deleted.
    consented = select(Recording.id).where(
        Recording.location_id == location.id, Recording.consent == "consented"
    )
    cleared = await session.execute(
        update(Job)
        .where(
            Job.recording_id.in_(consented),
            Job.state == "completed",
            Job.outputs_flagged_for_deletion.is_(True),
        )
        .values(outputs_flagged_for_deletion=False)
        .execution_options(synchronize_session=False)
    )
    summary.flags_cleared = cleared.rowcount

    wanted = [r for r in by_id.values() if r.consent == "consented" and not r.missing]
    have = {
        (row[0], row[1])
        for row in (
            await session.execute(
                select(Job.recording_id, Job.source_version).where(
                    Job.recording_id.in_([r.id for r in wanted]), Job.state != "cancelled"
                )
            )
        ).all()
    }
    for recording in wanted:
        if (recording.id, recording.source_version) in have:
            continue
        session.add(
            Job(
                id=uuid.uuid4(),
                recording_id=recording.id,
                source_version=recording.source_version,
                state="queued",
                pool=location.pool,
                required_device=location.required_device,
                priority=0,
                attempts=0,
                max_attempts=max_attempts,
            )
        )
        summary.jobs_created += 1

    location.last_scan_at = now
    location.last_scan_error = None
    audit.record(
        session,
        actor="system",
        action="location.scan",
        subject_type="location",
        subject_id=location.id,
        detail=asdict(summary),
    )
    return summary


async def scan_due_locations(
    sessionmaker: async_sessionmaker[AsyncSession],
    backend_factory: Callable[[StorageLocation], StorageBackend],
    *,
    now: datetime,
    max_attempts: int,
) -> dict[str, ScanSummary | str]:
    async with sessionmaker() as session:
        locations = (
            await session.scalars(select(StorageLocation).where(StorageLocation.enabled.is_(True)))
        ).all()
    due = [
        loc
        for loc in locations
        if loc.last_scan_at is None
        or loc.last_scan_at + timedelta(seconds=loc.scan_interval_s) <= now
    ]
    results: dict[str, ScanSummary | str] = {}
    for loc in due:
        try:
            async with sessionmaker() as session:
                location = await session.get(StorageLocation, loc.id, with_for_update=True)
                results[loc.name] = await scan_location(
                    session, location, backend_factory(location), now=now, max_attempts=max_attempts
                )
                await session.commit()
        except Exception as exc:
            logger.exception("scanning location %s failed", loc.name)
            results[loc.name] = str(exc)
            async with sessionmaker() as session:
                await session.execute(
                    update(StorageLocation)
                    .where(StorageLocation.id == loc.id)
                    .values(last_scan_at=now, last_scan_error=str(exc)[:2000])
                )
                await session.commit()
    return results
