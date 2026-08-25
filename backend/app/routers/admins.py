"""Admin (operator) management + invite links — superadmin only."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..database import get_session
from ..deps import require_superadmin
from ..models import Admin, AdminInvite, User
from ..schemas import (
    AdminCreate,
    AdminOut,
    AdminUpdate,
    InviteCreate,
    InviteOut,
)
from ..security import hash_password

router = APIRouter(prefix="/api/admins", tags=["admins"], dependencies=[Depends(require_superadmin)])


async def _user_counts(db: AsyncSession) -> dict[int, int]:
    rows = await db.execute(
        select(User.created_by_admin_id, func.count(User.id)).group_by(User.created_by_admin_id)
    )
    return {aid: cnt for aid, cnt in rows.all() if aid is not None}


def _to_out(a: Admin, counts: dict[int, int]) -> AdminOut:
    out = AdminOut.model_validate(a)
    out.user_count = counts.get(a.id, 0)
    return out


@router.get("", response_model=list[AdminOut])
async def list_admins(db: AsyncSession = Depends(get_session)):
    admins = (await db.execute(select(Admin).order_by(Admin.created_at))).scalars().all()
    counts = await _user_counts(db)
    return [_to_out(a, counts) for a in admins]


@router.post("", response_model=AdminOut, status_code=status.HTTP_201_CREATED)
async def create_admin(
    payload: AdminCreate,
    me: Admin = Depends(require_superadmin),
    db: AsyncSession = Depends(get_session),
):
    if payload.role not in {"admin", "superadmin"}:
        raise HTTPException(status_code=400, detail="Invalid role")
    exists = (await db.execute(select(Admin).where(Admin.username == payload.username))).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail="Username already exists")
    admin = Admin(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=payload.role,
        can_create_users=payload.can_create_users,
        max_users=payload.max_users,
        created_by=me.id,
        note=payload.note or "",
    )
    db.add(admin)
    await audit.record(db, "create_admin", payload.username, actor=me.username)
    await db.commit()
    await db.refresh(admin)
    return _to_out(admin, {})


@router.put("/{admin_id}", response_model=AdminOut)
async def update_admin(
    admin_id: int,
    payload: AdminUpdate,
    me: Admin = Depends(require_superadmin),
    db: AsyncSession = Depends(get_session),
):
    admin = await db.get(Admin, admin_id)
    if not admin:
        raise HTTPException(status_code=404, detail="Admin not found")
    data = payload.model_dump(exclude_unset=True)

    # Deleting the last superadmin was already refused; disabling one was not,
    # and it has the same end state — an panel whose admin, settings and node
    # surfaces nobody can reach again, with no way back in through the UI.
    if data.get("is_active") is False:
        if admin.id == me.id:
            raise HTTPException(status_code=400, detail="You cannot disable your own account")
        if admin.is_superadmin:
            others = (await db.execute(
                select(func.count(Admin.id)).where(
                    Admin.role == "superadmin", Admin.is_active.is_(True), Admin.id != admin.id
                )
            )).scalar_one()
            if others == 0:
                raise HTTPException(status_code=400, detail="Cannot disable the last active superadmin")
    if data.get("password"):
        admin.password_hash = hash_password(data.pop("password"))
    else:
        data.pop("password", None)
    for field, value in data.items():
        setattr(admin, field, value)
    await audit.record(db, "update_admin", admin.username, ", ".join(data.keys()), actor=me.username)
    await db.commit()
    counts = await _user_counts(db)
    return _to_out(admin, counts)


@router.delete("/{admin_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_admin(
    admin_id: int,
    reassign_to: int | None = None,
    me: Admin = Depends(require_superadmin),
    db: AsyncSession = Depends(get_session),
):
    admin = await db.get(Admin, admin_id)
    if not admin:
        raise HTTPException(status_code=404, detail="Admin not found")
    if admin.id == me.id:
        raise HTTPException(status_code=400, detail="You cannot delete yourself")
    if admin.is_superadmin:
        supers = (await db.execute(select(func.count(Admin.id)).where(Admin.role == "superadmin"))).scalar_one()
        if supers <= 1:
            raise HTTPException(status_code=400, detail="Cannot delete the last superadmin")
    # Their VPN accounts are KEPT — deleting an operator must never disconnect
    # a paying customer. They are handed to a real owner rather than left
    # pointing at an id that no longer exists: an orphaned account is invisible
    # to every reseller, counts toward nobody's cap, and silently rejoins the
    # ownership model if the id is ever reused.
    new_owner = me
    if reassign_to is not None and reassign_to != me.id:
        candidate = await db.get(Admin, reassign_to)
        if not candidate or not candidate.is_active:
            raise HTTPException(status_code=400, detail="Reassignment target is unknown or disabled")
        if candidate.id == admin.id:
            raise HTTPException(status_code=400, detail="Cannot reassign to the admin being deleted")
        new_owner = candidate

    moved = (await db.execute(
        update(User)
        .where(User.created_by_admin_id == admin.id)
        .values(created_by_admin_id=new_owner.id)
    )).rowcount or 0

    username = admin.username
    await db.delete(admin)
    await audit.record(
        db, "delete_admin", username,
        f"{moved} account(s) reassigned to {new_owner.username}", actor=me.username,
    )
    await db.commit()
    return None


# ---- Invite links ----
@router.get("/invites", response_model=list[InviteOut])
async def list_invites(db: AsyncSession = Depends(get_session)):
    rows = (await db.execute(select(AdminInvite).order_by(AdminInvite.created_at.desc()))).scalars().all()
    return rows


@router.post("/invites", response_model=InviteOut, status_code=status.HTTP_201_CREATED)
async def create_invite(
    payload: InviteCreate,
    me: Admin = Depends(require_superadmin),
    db: AsyncSession = Depends(get_session),
):
    if payload.role not in {"admin", "superadmin"}:
        raise HTTPException(status_code=400, detail="Invalid role")
    expires = None
    if payload.expires_in_hours and payload.expires_in_hours > 0:
        expires = datetime.now(timezone.utc) + timedelta(hours=payload.expires_in_hours)
    inv = AdminInvite(
        role=payload.role,
        can_create_users=payload.can_create_users,
        max_users=payload.max_users,
        note=payload.note or "",
        created_by=me.id,
        expires_at=expires,
    )
    db.add(inv)
    await audit.record(db, "create_invite", inv.role, actor=me.username)
    await db.commit()
    await db.refresh(inv)
    return inv


@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(invite_id: int, db: AsyncSession = Depends(get_session)):
    inv = await db.get(AdminInvite, invite_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invite not found")
    await db.delete(inv)
    await db.commit()
    return None
