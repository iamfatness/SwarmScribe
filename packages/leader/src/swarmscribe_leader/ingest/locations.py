"""Storage locations as administrators change them. Each function audits its change; the
caller commits."""

import asyncio
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..db.models import StorageLocation
from ..errors import Conflict, LeaderError, NotFound


async def _by_name(session: AsyncSession, name: str) -> StorageLocation:
    location = await session.scalar(
        select(StorageLocation)
        .where(StorageLocation.name == name)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if location is None:
        raise NotFound(f"no location named {name!r}")
    return location


async def add_location(
    session: AsyncSession,
    *,
    name: str,
    root: str,
    input_prefix: str,
    output_prefix: str,
    pool: str,
    required_device: str,
    scan_interval_s: int,
    actor: str,
) -> StorageLocation:
    """A local-folder location (Azure and GCS arrive with Plan B). The folder must be
    visible to this replica, which suggests every replica mounts it at the same path."""
    if not await asyncio.to_thread(Path(root).is_dir):
        raise LeaderError(
            f"{root!r} is not a folder this leader can see; every replica must mount it there",
            code="root_unavailable",
        )
    location = StorageLocation(
        id=uuid.uuid4(),
        name=name,
        backend="local",
        config={"root": root},
        input_prefix=input_prefix,
        output_prefix=output_prefix,
        pool=pool,
        required_device=required_device,
        scan_interval_s=scan_interval_s,
        enabled=True,
        vocabulary_version=0,
    )
    session.add(location)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise Conflict(f"a location named {name!r} already exists", code="exists") from exc
    await session.refresh(location)
    audit.record(
        session,
        actor=actor,
        action="location.add",
        subject_type="location",
        subject_id=location.id,
        detail={"name": name, "backend": "local", "root": root, "pool": pool},
    )
    return location


async def set_enabled(
    session: AsyncSession, name: str, enabled: bool, *, actor: str
) -> StorageLocation:
    """Disabling stops scanning; jobs already queued stay claimable."""
    location = await _by_name(session, name)
    location.enabled = enabled
    audit.record(
        session,
        actor=actor,
        action="location.enable" if enabled else "location.disable",
        subject_type="location",
        subject_id=location.id,
    )
    return location


async def request_scan(
    session: AsyncSession, name: str, *, now: datetime, actor: str
) -> StorageLocation:
    """Ask the scanner to scan this location on its next tick (see scan_due_locations)."""
    location = await _by_name(session, name)
    if not location.enabled:
        raise Conflict(f"location {name!r} is disabled; enable it first", code="disabled")
    location.scan_requested_at = now
    audit.record(
        session,
        actor=actor,
        action="location.ingest",
        subject_type="location",
        subject_id=location.id,
    )
    return location
