"""The fleet view (fleet console spec 5.4 and 6): every leader the person holds a role on,
with its health and latest successful snapshot, and one leader's history for the 24-hour
chart. A leader the person holds no role on is answered exactly like an unknown one."""

from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from sqlalchemy import BigInteger, Boolean, DateTime, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from swarmscribe_leader.clock import utcnow

from .. import grants, leaders
from ..db.models import Leader
from ..errors import NotFound
from ..sessions import SignedIn
from .deps import Person, Session

router = APIRouter(prefix="/api")

HISTORY_BUCKET = timedelta(minutes=5)
MAX_HISTORY_HOURS = 24

_LATEST = text(
    """
    SELECT s.leader_id, s.taken_at, s.status
    FROM unnest(CAST(:ids AS bigint[])) AS l(id)
    CROSS JOIN LATERAL (
        SELECT leader_id, taken_at, status FROM snapshots
        WHERE leader_id = l.id AND reachable
        ORDER BY taken_at DESC
        LIMIT 1
    ) AS s
    """
).columns(leader_id=BigInteger, taken_at=DateTime(timezone=True), status=JSONB)

_HISTORY = text(
    """
    SELECT DISTINCT ON (bucket)
        date_bin(CAST(:bucket AS interval), taken_at, TIMESTAMPTZ '2000-01-01 00:00:00+00')
            AS bucket,
        reachable,
        (status->'jobs'->>'queued')::bigint AS queued,
        (status->'jobs'->>'leased')::bigint AS leased,
        (status->>'completed_last_hour')::bigint AS completed_last_hour,
        (status->>'completed_last_day')::bigint AS completed_last_day,
        (status->>'failed_attempts_last_day')::bigint AS failed_attempts_last_day,
        (status->>'oldest_queued_age_s')::bigint AS oldest_queued_age_s,
        (status->'followers'->>'active')::bigint AS followers_active
    FROM snapshots
    WHERE leader_id = :leader_id AND taken_at >= :since
    ORDER BY bucket, taken_at DESC
    """
).columns(
    bucket=DateTime(timezone=True),
    reachable=Boolean,
    queued=BigInteger,
    leased=BigInteger,
    completed_last_hour=BigInteger,
    completed_last_day=BigInteger,
    failed_attempts_last_day=BigInteger,
    oldest_queued_age_s=BigInteger,
    followers_active=BigInteger,
)


class SnapshotOut(BaseModel):
    taken_at: datetime
    status: dict[str, Any]


class ScanError(BaseModel):
    location: str
    error: str


class FleetSummary(BaseModel):
    """Spec 6's overview row, from one status payload (C1 + C1b). `oldest_queued_age_s` is
    the age of the oldest queued job, counted from when that job was created, as of the
    snapshot's taken_at (None when nothing was queued)."""

    queued: int
    completed_last_hour: int
    completed_last_day: int
    failed_attempts_last_day: int
    oldest_queued_age_s: int | None  # from the oldest queued job's creation time
    followers_active_by_pool: dict[str, int]
    scan_errors: list[ScanError]


class FleetLeader(BaseModel):
    name: str
    labels: dict[str, str]
    role: str
    health: str
    enabled: bool
    last_polled_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None
    consecutive_failures: int
    summary: FleetSummary | None
    snapshot: SnapshotOut | None


class HistoryPoint(BaseModel):
    at: datetime
    reachable: bool
    queued: int | None
    leased: int | None
    completed_last_hour: int | None
    completed_last_day: int | None
    failed_attempts_last_day: int | None
    oldest_queued_age_s: int | None  # from the oldest queued job's creation time
    followers_active: int | None


def summary_of(status: dict[str, Any]) -> dict[str, Any]:
    """The overview figures from a stored status (validated by the poller's StatusPayload).
    Lookups stay defensive: a stored row is data, not a promise."""
    pools = status.get("follower_pools") or []
    locations = status.get("locations") or []
    return {
        "queued": int((status.get("jobs") or {}).get("queued", 0)),
        "completed_last_hour": int(status.get("completed_last_hour", 0)),
        "completed_last_day": int(status.get("completed_last_day", 0)),
        "failed_attempts_last_day": int(status.get("failed_attempts_last_day", 0)),
        "oldest_queued_age_s": status.get("oldest_queued_age_s"),
        "followers_active_by_pool": {
            str(pool.get("pool")): int(pool.get("active", 0))
            for pool in pools
            if isinstance(pool, dict) and pool.get("pool") is not None
        },
        "scan_errors": [
            {"location": str(loc.get("name")), "error": str(loc["last_scan_error"])}
            for loc in locations
            if isinstance(loc, dict) and loc.get("last_scan_error")
        ],
    }


def health_of(leader: Leader, unreachable_after: int) -> str:
    if not leader.enabled:
        return "disabled"
    if leader.credential_revoked_at is not None:
        return "credential_revoked"
    if leader.last_polled_at is None:
        # Never polled, or the poller's state was cleared by a rotation or URL change: an
        # older last_success_at must not make the leader look reachable (ruling R5).
        return "pending"
    if leader.consecutive_failures >= unreachable_after:
        return "unreachable"
    if leader.last_success_at is None:
        return "pending"
    return "reachable"


async def leader_for(
    session: AsyncSession, person: SignedIn, name: str
) -> tuple[Leader, str]:
    """The named leader and the person's role on it."""
    try:
        leader = await leaders.find_leader(session, name)
    except NotFound:
        leader = None
    role = None
    if leader is not None:
        held = await grants.grants_held(session, person.principals)
        role = grants.role_for(held, leader.name, leader.labels)
    if leader is None or role is None:
        raise NotFound("no leader with that name", code="leader_not_found")
    return leader, role


@router.get("/fleet", response_model=list[FleetLeader])
async def fleet(request: Request, person: Person, session: Session) -> list[FleetLeader]:
    held = await grants.grants_held(session, person.principals)
    visible: list[tuple[Leader, str]] = []
    if held:
        for leader in await leaders.list_leaders(session):
            role = grants.role_for(held, leader.name, leader.labels)
            if role is not None:
                visible.append((leader, role))
    latest: dict[int, Any] = {}
    if visible:
        rows = await session.execute(_LATEST, {"ids": [leader.id for leader, _ in visible]})
        latest = {row.leader_id: row for row in rows}
    after = request.app.state.poller_config.unreachable_after
    answer = []
    for leader, role in visible:
        snapshot = latest.get(leader.id)
        answer.append(
            FleetLeader(
                name=leader.name,
                labels=leader.labels,
                role=role,
                health=health_of(leader, after),
                enabled=leader.enabled,
                last_polled_at=leader.last_polled_at,
                last_success_at=leader.last_success_at,
                last_error=leader.last_error,
                consecutive_failures=leader.consecutive_failures,
                summary=(
                    FleetSummary.model_validate(summary_of(snapshot.status))
                    if snapshot is not None
                    else None
                ),
                snapshot=(
                    SnapshotOut(taken_at=snapshot.taken_at, status=snapshot.status)
                    if snapshot is not None
                    else None
                ),
            )
        )
    return answer


@router.get("/leaders/{name}/history", response_model=list[HistoryPoint])
async def history(
    name: str,
    person: Person,
    session: Session,
    hours: Annotated[int, Query(ge=1, le=MAX_HISTORY_HOURS)] = MAX_HISTORY_HOURS,
) -> list[HistoryPoint]:
    leader, _role = await leader_for(session, person, name)
    rows = await session.execute(
        _HISTORY,
        {
            "leader_id": leader.id,
            "since": utcnow() - timedelta(hours=hours),
            "bucket": HISTORY_BUCKET,
        },
    )
    return [
        HistoryPoint(
            at=row.bucket,
            reachable=row.reachable,
            queued=row.queued,
            leased=row.leased,
            completed_last_hour=row.completed_last_hour,
            completed_last_day=row.completed_last_day,
            failed_attempts_last_day=row.failed_attempts_last_day,
            oldest_queued_age_s=row.oldest_queued_age_s,
            followers_active=row.followers_active,
        )
        for row in rows
    ]