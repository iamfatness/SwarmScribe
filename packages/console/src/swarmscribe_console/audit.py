"""The console's audit log (fleet console spec 5.1): actor, action, leader, target, outcome,
time. Entries never hold credentials, cookies, tokens or request bodies."""

import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .db.models import AuditEntry

logger = logging.getLogger(__name__)


def record(
    session: AsyncSession,
    *,
    actor: str,
    action: str,
    leader: str | None = None,
    target: str | None = None,
    outcome: str = "ok",
    detail: dict[str, Any] | None = None,
) -> None:
    """Add an entry to the session; it commits with the change it describes."""
    session.add(
        AuditEntry(
            actor=actor,
            action=action,
            leader=leader,
            target=target,
            outcome=outcome,
            detail=detail or {},
        )
    )


async def record_apart(sessionmaker: Callable[[], Any], **fields: Any) -> None:
    """An entry in its own session (for refusals, whose request rolled back). Never raises:
    a refusal must still be answered when the audit write fails."""
    try:
        async with sessionmaker() as session:
            record(session, **fields)
            await session.commit()
    except Exception as exc:
        logger.error("an audit entry could not be written: %s", type(exc).__name__)
