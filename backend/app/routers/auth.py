"""Authentication + invite acceptance."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..database import get_session
from ..deps import get_current_admin
from ..models import Admin, AdminInvite
from ..schemas import (
    AdminPasswordChange,
    InviteAccept,
    InviteInfo,
    LoginRequest,
    TokenResponse,
)
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_session)):
    result = await db.execute(select(Admin).where(Admin.username == payload.username))
    admin = result.scalar_one_or_none()
    if not admin or not admin.is_active or not verify_password(payload.password, admin.password_hash):
        # Record the attempt: a burst of these against one account is the only
        # in-panel signal of someone probing an operator's credentials. The
        # reason is deliberately coarse so the log never reveals which accounts
        # exist. Committed even though the request fails.
        why = "unknown user" if not admin else ("disabled account" if not admin.is_active else "bad password")
        await audit.record(db, "login_failed", payload.username, why, actor=payload.username)
        await db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    admin.last_login = datetime.now(timezone.utc)
    await audit.record(db, "login", admin.username, f"role={admin.role}", actor=admin.username)
    await db.commit()
    token, expires_in = create_access_token(admin.id, admin.username, admin.role)
    return TokenResponse(access_token=token, expires_in=expires_in, username=admin.username, role=admin.role)


@router.get("/me")
async def me(admin: Admin = Depends(get_current_admin)):
    return {
        "id": admin.id,
        "username": admin.username,
        "role": admin.role,
        "can_create_users": admin.can_create_users,
        "max_users": admin.max_users,
    }


@router.post("/change-password")
async def change_password(
    payload: AdminPasswordChange,
    admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    if not verify_password(payload.current_password, admin.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    admin.password_hash = hash_password(payload.new_password)
    await audit.record(db, "change_password", admin.username, actor=admin.username)
    await db.commit()
    return {"detail": "Password updated"}


# ---- Public invite endpoints (no auth) ----
@router.get("/invite/{token}", response_model=InviteInfo)
async def invite_info(token: str, db: AsyncSession = Depends(get_session)):
    inv = (await db.execute(select(AdminInvite).where(AdminInvite.token == token))).scalar_one_or_none()
    if not inv or inv.used or inv.is_expired:
        return InviteInfo(role="", valid=False)
    return InviteInfo(role=inv.role, valid=True, note=inv.note)


@router.post("/invite/accept", response_model=TokenResponse)
async def invite_accept(payload: InviteAccept, db: AsyncSession = Depends(get_session)):
    inv = (await db.execute(select(AdminInvite).where(AdminInvite.token == payload.token))).scalar_one_or_none()
    if not inv or inv.used or inv.is_expired:
        raise HTTPException(status_code=400, detail="Invite is invalid or expired")

    exists = (await db.execute(select(func.count(Admin.id)).where(Admin.username == payload.username))).scalar_one()
    if exists:
        raise HTTPException(status_code=409, detail="Username already taken")

    admin = Admin(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=inv.role,
        can_create_users=inv.can_create_users,
        max_users=inv.max_users,
        created_by=inv.created_by,
        note=inv.note,
    )
    db.add(admin)
    inv.used = True
    await db.flush()
    inv.used_by = admin.id
    admin.last_login = datetime.now(timezone.utc)
    # An invite link creating an operator is the single highest-privilege event
    # in the panel and had no audit trail at all.
    await audit.record(
        db, "invite_accept", admin.username,
        f"role={admin.role}, can_create_users={admin.can_create_users}, max_users={admin.max_users}",
        actor=admin.username,
    )
    await db.commit()

    token, expires_in = create_access_token(admin.id, admin.username, admin.role)
    return TokenResponse(access_token=token, expires_in=expires_in, username=admin.username, role=admin.role)
