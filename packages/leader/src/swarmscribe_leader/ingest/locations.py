"""Storage locations as administrators change them. Each function audits its change; the
caller commits."""

import asyncio
import os
import stat
import uuid
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from swarmscribe_protocol import DEFAULT_CHANNEL_LABELS

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


class InvalidRoot(LeaderError):
    status = 422
    code = "invalid_root"


# Serialises every addition of a location, so that two at once cannot both pass the
# name and overlap checks below.
_ADD_LOCK = 7_204_511_001


def _is_junction(path: Path) -> bool:
    """A Windows junction (a reparse point that is not a symlink). Path.is_junction exists
    only from Python 3.12; elsewhere junctions do not exist."""
    if hasattr(path, "is_junction"):
        return path.is_junction()
    if os.name != "nt":
        return False
    try:
        attributes = os.lstat(path).st_file_attributes
    except OSError:
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _is_link(path: Path) -> bool:
    return path.is_symlink() or _is_junction(path)


def canonical_root(root: str) -> Path:
    """The real folder `root` names. Refused when it, or any folder on the way to it, is a
    symlink or junction (the consent and storage rules assume a root cannot be redirected),
    or when it is not a folder. A missing folder is `root_unavailable`."""
    path = Path(root)
    walked = Path(path.anchor)
    for part in path.parts[1:] if path.anchor else path.parts:
        walked = walked / part
        if part != ".." and _is_link(walked):
            raise InvalidRoot(f"{root!r} goes through a symlink or junction; give the real folder")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise LeaderError(
            f"{root!r} is not a folder this leader can see; every replica must mount it there",
            code="root_unavailable",
        ) from exc
    if not resolved.is_dir():
        raise InvalidRoot(f"{root!r} is not a folder")
    return resolved


def _same_or_nested(a: Path, b: Path) -> bool:
    x, y = Path(os.path.normcase(a)), Path(os.path.normcase(b))
    return x == y or x.is_relative_to(y) or y.is_relative_to(x)


async def _refuse_overlap(session: AsyncSession, root: Path) -> None:
    """Two locations on the same folder, or one inside another, would let one location's
    consent.txt (anchored at its input prefix) reach files the other refuses."""
    rows = (await session.execute(select(StorageLocation.name, StorageLocation.config))).all()
    for other_name, config in rows:
        other_root = (config or {}).get("root")
        if not other_root:
            continue
        other = await asyncio.to_thread(lambda r=other_root: Path(r).resolve())
        if _same_or_nested(root, other):
            raise Conflict(
                f"the folder overlaps location {other_name!r} ({other_root}); locations must "
                "not share or nest folders",
                code="overlaps",
            )


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
    channel_mode: str = "mono",
    channel_labels: Sequence[str] = DEFAULT_CHANNEL_LABELS,
) -> StorageLocation:
    """A local-folder location (Azure and GCS arrive with Plan B). The folder must be
    visible to this replica, which suggests every replica mounts it at the same path. The
    stored root is the canonical path; names are unique ignoring case."""
    canonical = await asyncio.to_thread(canonical_root, root)
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _ADD_LOCK})
    taken = await session.scalar(
        select(StorageLocation.name).where(func.lower(StorageLocation.name) == name.lower())
    )
    if taken is not None:
        raise Conflict(f"a location named {taken!r} already exists", code="exists")
    await _refuse_overlap(session, canonical)
    location = StorageLocation(
        id=uuid.uuid4(),
        name=name,
        backend="local",
        config={"root": str(canonical)},
        input_prefix=input_prefix,
        output_prefix=output_prefix,
        pool=pool,
        required_device=required_device,
        scan_interval_s=scan_interval_s,
        enabled=True,
        vocabulary_version=0,
        channel_mode=channel_mode,
        channel_labels=list(channel_labels),
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
        detail={"name": name, "backend": "local", "root": str(canonical), "pool": pool},
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
