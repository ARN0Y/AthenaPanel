"""Live session endpoints — scoped per admin."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, livecache, pppd
from ..database import get_session
from ..deps import get_current_admin
from ..models import Admin, User
from ..schemas import SessionOut

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


async def _owned_usernames(db: AsyncSession, admin: Admin) -> set[str] | None:
    """Return the set of usernames this admin may see, or None for all."""
    if admin.is_superadmin:
        return None
    rows = await db.execute(select(User.username).where(User.created_by_admin_id == admin.id))
    return {u for (u,) in rows.all()}


@router.get("", response_model=list[SessionOut])
async def list_sessions(admin: Admin = Depends(get_current_admin), db: AsyncSession = Depends(get_session)):
    # Served from the shared live snapshot (refreshed every ~10s by one task),
    # so this endpoint never scans sysfs per request.
    sessions = livecache.snapshot()["sessions"]
    allowed = await _owned_usernames(db, admin)
    if allowed is None:
        return sessions
    return [s for s in sessions if s.username in allowed]


@router.delete("/{username}")
async def disconnect(username: str, admin: Admin = Depends(get_current_admin), db: AsyncSession = Depends(get_session)):
    allowed = await _owned_usernames(db, admin)
    if allowed is not None and username not in allowed:
        raise HTTPException(status_code=403, detail="Not your user")
    ok = await pppd.terminate_user(db, username)
    if not ok:
        raise HTTPException(status_code=400, detail="Could not terminate session")
    await audit.record(db, "disconnect", username, actor=admin.username)
    await db.commit()
    return {"detail": f"Disconnected {username}"}
