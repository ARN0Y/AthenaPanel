"""Helper for recording admin actions to the audit log."""

from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditLog


async def record(
    db: AsyncSession,
    action: str,
    target: str = "",
    detail: str = "",
    actor: str = "admin",
) -> None:
    """Add an audit entry. Caller is responsible for committing the session."""
    db.add(AuditLog(actor=actor, action=action, target=target, detail=detail))
