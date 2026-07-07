"""Connection events (accounting log) and admin audit log."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import accounting
from ..database import get_session
from ..deps import get_current_admin, require_superadmin
from ..models import Admin, AuditLog, User
from ..schemas import AuditEntry, EventOut

router = APIRouter(prefix="/api", tags=["events"])


@router.get("/events", response_model=list[EventOut])
async def events(
    limit: int = Query(200, ge=1, le=2000),
    admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    items = await accounting.read_events(db, limit)
    if not admin.is_superadmin:
        rows = await db.execute(select(User.username).where(User.created_by_admin_id == admin.id))
        owned = {u for (u,) in rows.all()}
        items = [e for e in items if e["username"] in owned]
    return items


@router.get("/audit", response_model=list[AuditEntry], dependencies=[Depends(require_superadmin)])
async def audit(limit: int = Query(200, ge=1, le=2000), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(AuditLog).order_by(AuditLog.ts.desc()).limit(limit))).scalars().all()
    return rows
