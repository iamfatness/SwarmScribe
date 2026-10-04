"""The poller (fleet console spec 5.3).

Each replica runs one loop. Every second it picks the enabled leaders not polled for 14 s and
whose credential is not revoked; for each it takes that leader's advisory lock, re-reads the
leader inside the lock (another replica may just have polled it), calls GET /v1/admin/status
as system:poller with role viewer (C1: one call carries the followers by pool too), and
records a snapshot and the leader's poll state. Three consecutive failures mean unreachable.
A 401 "credential_revoked" marks the leader revoked and stops polling it until a console
administrator replaces the credential (C1 follow-up). Snapshots older than 24 hours are pruned
hourly. An answer that arrives after the leader's credential or URL changed is dropped whole:
it describes a leader the row no longer points at."""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from . import audit
from .background import run_exclusive
from .config import Settings
from .crypto import ConsoleKeys, CredentialUnreadable
from .db.models import Leader, Snapshot
from .leader_client import (
    POLLER_ACTOR,
    LeaderBadAnswer,
    LeaderClient,
    LeaderReply,
    LeaderTarget,
    LeaderUnreachable,
    is_revoked,
)
from .leaders import sealing_context
from .logsafe import log_limited
from .sessions import prune_expired

logger = logging.getLogger(__name__)

POLL_LOCK_BASE = 0x53430001 << 32  # + leader id
PRUNE_LOCK = 0x53430002 << 32
STATUS_PATH = "/v1/admin/status"
DUE_SLACK = timedelta(seconds=1)
MAX_STATUS_BYTES = 1024 * 1024  # a stored status answer; a larger one is bad_response

Count = Annotated[int, Field(ge=0, le=10**12)]


class StatusPayload(BaseModel):
    """The shape of C1/C1b's Status, checked before a leader's answer is stored. Strict: a
    count must be a JSON integer (not "420", not true). Extra fields are kept, so a newer
    leader's additions reach the web app."""

    model_config = ConfigDict(extra="allow", strict=True)

    jobs: dict[str, Count]
    pools: list[dict[str, Any]]
    followers: dict[str, Count]
    follower_pools: list[dict[str, Any]]  # C1 amendment 2: required, may be empty
    completed_last_hour: Count
    completed_last_day: Count  # C1b; required (C1b is this plan's precondition)
    oldest_queued_age_s: Count | None  # C1b; None when nothing is queued, but present
    failed_attempts_last_day: Count
    locations: list[dict[str, Any]]


@dataclass(frozen=True)
class PollerConfig:
    interval: timedelta
    timeout: float
    unreachable_after: int
    history: timedelta
    concurrency: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "PollerConfig":
        return cls(
            interval=timedelta(seconds=settings.poll_interval_seconds),
            timeout=settings.poll_timeout_seconds,
            unreachable_after=settings.unreachable_after_failures,
            history=timedelta(hours=settings.history_hours),
            concurrency=settings.poll_concurrency,
        )


def classify(reply: LeaderReply) -> tuple[str, dict[str, Any] | None]:
    if 200 <= reply.status < 300:
        if reply.status != 200:
            return "bad_response", None
        try:
            # The client does not expose the wire size; UTF-8 bytes of the parsed answer
            # (ensure_ascii=False, so non-ASCII text is not inflated) stand in for it.
            if len(json.dumps(reply.body, ensure_ascii=False).encode()) > MAX_STATUS_BYTES:
                return "bad_response", None
            return "ok", StatusPayload.model_validate(reply.body).model_dump(mode="json")
        except (ValidationError, ValueError, TypeError, RecursionError):
            return "bad_response", None
    if is_revoked(reply):
        return "credential_revoked", None
    if reply.status == 401:
        return "credential_rejected", None
    if reply.status == 403:
        return "forbidden", None
    return f"http_{reply.status}", None


async def mark_revoked(
    session: AsyncSession, leader: Leader, sealed: bytes, *, now: datetime, actor: str
) -> bool:
    """Mark the leader's credential revoked, once, and only if it is still the credential the
    leader refused (one replaced meanwhile is never marked). The caller commits."""
    if leader.credential != sealed or leader.credential_revoked_at is not None:
        return False
    leader.credential_revoked_at = now
    leader.last_error = "credential_revoked"
    audit.record(
        session,
        actor=actor,
        action="leader.credential_revoked",
        leader=leader.name,
        outcome="credential_revoked",
    )
    return True


async def _record(
    sessionmaker: Any,
    leader_id: int,
    base_url: str,
    sealed: bytes,
    outcome: str,
    payload: dict[str, Any] | None,
    *,
    now: datetime,
) -> None:
    async with sessionmaker() as session:
        leader = await session.get(Leader, leader_id, with_for_update=True, populate_existing=True)
        if leader is None:  # removed while this poll was out
            return
        if leader.credential != sealed or leader.base_url != base_url:
            # Rotated or re-pointed while this poll was out: the answer was about the old
            # credential or URL. It changes nothing (the change's own reset stands).
            return
        session.add(
            Snapshot(
                leader_id=leader_id,
                taken_at=now,
                reachable=outcome == "ok",
                outcome=outcome,
                status=payload,
            )
        )
        leader.last_polled_at = now
        if outcome == "ok":
            leader.consecutive_failures = 0
            leader.last_error = None
            leader.last_success_at = now
        elif outcome == "credential_revoked":
            # Not a failure: polling stops, and the leader shows as revoked.
            await mark_revoked(session, leader, sealed, now=now, actor=POLLER_ACTOR)
        else:
            leader.consecutive_failures += 1
            leader.last_error = outcome
        await session.commit()


async def poll_leader(
    sessionmaker: Any,
    client: LeaderClient,
    keys: ConsoleKeys,
    leader_id: int,
    *,
    now: datetime,
    config: PollerConfig,
) -> str:
    """Poll one leader if it is still due. Call it holding the leader's lock."""
    async with sessionmaker() as session:
        leader = await session.get(Leader, leader_id)
        if leader is None or not leader.enabled or leader.credential_revoked_at is not None:
            return "skipped"
        if (
            leader.last_polled_at is not None
            and now - leader.last_polled_at < config.interval - DUE_SLACK
        ):
            return "skipped"
        name, base_url, sealed = leader.name, leader.base_url, leader.credential
    payload: dict[str, Any] | None = None
    try:
        # The credential is sealed bound to the name and URL (C2a `leaders.sealing_context`),
        # both read from the same row as `sealed`. A bare name never opens it.
        context = sealing_context(name, base_url)
        target = LeaderTarget(name, base_url, keys.open_credential(context, sealed))
        reply = await client.call(
            target, "GET", STATUS_PATH, actor=POLLER_ACTOR, role="viewer", timeout=config.timeout
        )
        outcome, payload = classify(reply)
    except CredentialUnreadable:
        outcome = "credential_unreadable"
    except LeaderUnreachable as exc:
        outcome = exc.reason
    except LeaderBadAnswer:
        outcome = "bad_response"
    except ValueError:  # the client's guards refused the stored URL: a failure, not a crash
        outcome = "invalid_target"
    except Exception as exc:
        # Anything else (a RecursionError from a deeply nested answer, a transport bug) still
        # counts as a failure and advances the cadence; only type and frames are logged.
        log_limited(
            logger, f"polling leader {name} failed", exc, key=f"poll:{name}:{type(exc).__name__}"
        )
        outcome = "error"
    await _record(sessionmaker, leader_id, base_url, sealed, outcome, payload, now=now)
    return outcome


async def poll_due_leaders(
    engine: AsyncEngine,
    sessionmaker: Any,
    client: LeaderClient,
    keys: ConsoleKeys,
    *,
    now: datetime,
    config: PollerConfig,
) -> dict[str, str]:
    """One round: every due leader, each under its own advisory lock, at most
    `config.concurrency` at a time."""
    due_before = now - (config.interval - DUE_SLACK)
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(Leader.id, Leader.name)
                .where(
                    Leader.enabled.is_(True),
                    Leader.credential_revoked_at.is_(None),
                    or_(Leader.last_polled_at.is_(None), Leader.last_polled_at <= due_before),
                )
                .order_by(Leader.last_polled_at.asc().nulls_first(), Leader.id)
            )
        ).all()
    limit = asyncio.Semaphore(config.concurrency)

    loop = asyncio.get_running_loop()
    started = loop.time()

    async def one(index: int, leader_id: int) -> str:
        async with limit:
            outcome = "locked"
            # The first batch is stamped `now`; a leader that waited for a slot is stamped
            # with the time it really started, so cadence does not drift by the round's length.
            batch_now = now if index < config.concurrency else now + timedelta(
                seconds=loop.time() - started
            )

            async def work() -> None:
                nonlocal outcome
                outcome = await poll_leader(
                    sessionmaker, client, keys, leader_id, now=batch_now, config=config
                )

            await run_exclusive(engine, POLL_LOCK_BASE + leader_id, work)
            return outcome

    results = await asyncio.gather(
        *(one(i, row.id) for i, row in enumerate(rows)), return_exceptions=True
    )
    outcomes: dict[str, str] = {}
    for row, result in zip(rows, results, strict=True):
        if isinstance(result, BaseException):
            # Type and frames only: the text of a database or transport error can hold values.
            log_limited(
                logger,
                f"polling leader {row.name} failed",
                result,
                key=f"poll:{row.name}:{type(result).__name__}",
            )
            outcomes[row.name] = "error"
        elif result != "skipped":
            outcomes[row.name] = result
    return outcomes


async def prune(
    sessionmaker: Any, *, now: datetime, history: timedelta, session_idle: timedelta
) -> int:
    """Delete snapshots older than `history`, ended sessions and abandoned sign-ins."""
    async with sessionmaker() as session:
        snapshots = await session.execute(delete(Snapshot).where(Snapshot.taken_at < now - history))
        removed = snapshots.rowcount + await prune_expired(session, now=now, idle=session_idle)
        await session.commit()
    return removed
