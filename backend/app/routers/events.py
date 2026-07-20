"""Connection events (accounting log) and admin audit log."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import accounting, rbac
from ..database import get_session
from ..deps import get_current_admin, require_superadmin
from ..models import Admin, AuditLog
from ..schemas import AuditEntry, EventOut

router = APIRouter(prefix="/api", tags=["events"])


@router.get("/events", response_model=list[EventOut])
async def events(
    limit: int = Query(200, ge=1, le=2000),
    admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    return await accounting.read_events(db, limit, await rbac.owned_usernames(db, admin))


@router.get("/audit", response_model=list[AuditEntry], dependencies=[Depends(require_superadmin)])
async def audit(limit: int = Query(200, ge=1, le=2000), db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(AuditLog).order_by(AuditLog.ts.desc()).limit(limit))).scalars().all()
    return rows
