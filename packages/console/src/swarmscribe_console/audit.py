"""The console's audit log (fleet console spec 5.1): actor, action, leader, target, outcome,
time. Entries never hold credentials, cookies, tokens or request bodies."""

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .db.models import AuditEntry

logger = logging.getLogger(__name__)

# Entries are written from IdP-supplied and request-derived strings, so record() cleans them:
# C0/C1 control characters (CR, LF, NUL ...) and U+2028/U+2029 become U+FFFD (replaced, never
# escaped, so a stored value can never read as a second log line), and each field is cut to its
# column's length with a trailing "…". record() must never be the reason a change fails.
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_CUT = "\u2026"
LIMITS = {"actor": 400, "action": 64, "leader": 100, "target": 400, "outcome": 64}
DETAIL_MAX_BYTES = 4096


def _clean(value: str, limit: int) -> str:
    value = _CONTROL.sub("\ufffd", str(value))
    return value if len(value) <= limit else value[: limit - 1] + _CUT


def _clean_json(value: Any) -> Any:
    if isinstance(value, str):
        return _CONTROL.sub("\ufffd", value)
    if isinstance(value, dict):
        return {_CONTROL.sub("\ufffd", str(k)): _clean_json(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean_json(v) for v in value]
    return value


def _bounded_detail(detail: dict[str, Any] | None) -> dict[str, Any]:
    try:
        cleaned = _clean_json(detail or {})
        if len(json.dumps(cleaned, default=str).encode("utf-8")) <= DETAIL_MAX_BYTES:
            return json.loads(json.dumps(cleaned, default=str))
    except (TypeError, ValueError):
        pass
    return {"truncated": True}


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
            actor=_clean(actor, LIMITS["actor"]),
            action=_clean(action, LIMITS["action"]),
            leader=None if leader is None else _clean(leader, LIMITS["leader"]),
            target=None if target is None else _clean(target, LIMITS["target"]),
            outcome=_clean(outcome, LIMITS["outcome"]),
            detail=_bounded_detail(detail),
        )
    )


async def record_apart(sessionmaker: Callable[[], Any], **fields: Any) -> None:
    """An entry in its own session (for refusals, whose request rolled back). Never raises:
    a refusal must still be answered when the audit write fails. The log line names only the
    exception type, never the values."""
    try:
        async with sessionmaker() as session:
            record(session, **fields)
            await session.commit()
    except Exception as exc:
        logger.error("an audit entry could not be written: %s", type(exc).__name__)
