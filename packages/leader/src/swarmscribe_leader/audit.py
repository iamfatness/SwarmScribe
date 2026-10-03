from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .db.models import AuditEntry


def record(
    session: AsyncSession,
    *,
    actor: str,
    action: str,
    subject_type: str | None = None,
    subject_id: object | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Add an audit entry to the session; it commits with the change it describes."""
    session.add(
        AuditEntry(
            actor=actor,
            action=action,
            subject_type=subject_type,
            subject_id=None if subject_id is None else str(subject_id),
            detail=detail or {},
        )
    )
