"""User CRUD endpoints — scoped per admin (RBAC)."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, chap_secrets, livecache, outbound, pppd
from ..subtoken import make_token
from ..database import get_session
from ..deps import get_current_admin
from ..models import Admin, User
from ..models import Session as SessionRow
from ..schemas import BulkAction, UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/api/users", tags=["users"])


# --- audit detail formatting ------------------------------------------------
# Human-readable, field-aware values so the audit log says exactly WHAT changed
# (e.g. "quota: 50 GB → 100 GB") instead of just the field name.
_FIELD_LABELS = {
    "quota_bytes": "quota",
    "rate_up_kbps": "rate↑",
    "rate_down_kbps": "rate↓",
    "expires_at": "expires",
    "is_active": "status",
    "note": "note",
    "outbound": "outbound",
    "l2tp_mode": "L2TP mode",
    "username": "username",
    "password": "password",
}


def _human_bytes(n: int) -> str:
    if not n or n <= 0:
        return "unlimited"
    units = ("B", "KB", "MB", "GB", "TB")
    f = float(n)
    i = 0
    while f >= 1024 and i < len(units) - 1:
        f /= 1024
        i += 1
    return f"{f:.2f}".rstrip("0").rstrip(".") + " " + units[i]


def _fmt_audit_value(field: str, value) -> str:
    if value is None:
        return "never" if field == "expires_at" else "—"
    if field == "quota_bytes":
        return _human_bytes(value)
    if field in ("rate_up_kbps", "rate_down_kbps"):
        return "unlimited" if not value else f"{value} kbps"
    if field == "expires_at":
        return value.strftime("%Y-%m-%d %H:%M") if hasattr(value, "strftime") else str(value)
    if field == "is_active":
        return "active" if value else "disabled"
    if field == "password":
        return "(changed)"
    s = str(value)
    return s if s.strip() else "—"


def _describe_create(user: User) -> str:
    """One-line summary of a newly created account for the audit log."""
    parts = [
        f"quota={_human_bytes(user.quota_bytes)}",
        f"expires={_fmt_audit_value('expires_at', user.expires_at)}",
        f"status={_fmt_audit_value('is_active', user.is_active)}",
    ]
    if user.rate_up_kbps or user.rate_down_kbps:
        parts.append(
            f"rate=↑{_fmt_audit_value('rate_up_kbps', user.rate_up_kbps)}"
            f"/↓{_fmt_audit_value('rate_down_kbps', user.rate_down_kbps)}"
        )
    if user.note:
        parts.append(f"note={user.note}")
    return ", ".join(parts)


async def _admin_names(db: AsyncSession) -> dict[int, str]:
    rows = await db.execute(select(Admin.id, Admin.username))
    return {aid: name for aid, name in rows.all()}


async def _live_by_user(db: AsyncSession) -> dict[str, int]:
    """Uncommitted (since last enforcer poll) bytes per username.

    The enforcer commits per-poll deltas to used_bytes and advances the baseline
    (row.last_rx/last_tx). So used_bytes is current up to the last poll; here we
    add only the slice SINCE that baseline for instant, real-time display
    (used_bytes + this = effective usage). On disconnect this -> 0 and the slice
    is already committed, so the number never drops.
    """
    # Served from the shared live snapshot (one task refreshes it every ~10s),
    # so listing users never scans sysfs per request.
    return dict(livecache.snapshot()["live_by_user"])


async def _rebaseline_open_sessions(db: AsyncSession, username: str) -> None:
    """On quota reset, move each open session's billing base up to the current
    counter so the live overlay restarts from zero (without losing the iface
    counter or disconnecting the session)."""
    rows = (
        await db.execute(select(SessionRow).where(SessionRow.username == username))
    ).scalars().all()
    for r in rows:
        if pppd.iface_exists(r.ifname):
            rx, tx = pppd.read_iface_bytes(r.ifname)
            r.base_rx, r.base_tx, r.last_rx, r.last_tx = rx, tx, rx, tx


def _norm_mode(value: str | None) -> str:
    """L2TP mode: 'raw' (no IPsec) — anything else falls back to 'ipsec'."""
    return "raw" if str(value or "").strip().lower() == "raw" else "ipsec"


def _to_out(user: User, online: set[str], names: dict[int, str], live_bytes: int = 0) -> UserOut:
    out = UserOut.model_validate(user)
    out.password = user.password_hash  # plaintext, for the copy-able profile
    # Effective usage = committed used_bytes + this session's live bytes.
    effective = user.used_bytes + max(0, live_bytes)
    out.used_bytes = effective
    out.is_expired = user.is_expired
    out.quota_exceeded = user.quota_bytes > 0 and effective >= user.quota_bytes
    out.online = user.username in online
    out.created_by_username = names.get(user.created_by_admin_id or -1, "—")
    out.sub_token = make_token(user.id)
    out.outbound = outbound.normalize(user.outbound)
    out.l2tp_mode = _norm_mode(user.l2tp_mode)
    return out


def _owns(admin: Admin, user: User) -> bool:
    return admin.is_superadmin or user.created_by_admin_id == admin.id


async def _require_owned(db: AsyncSession, admin: Admin, user_id: int) -> User:
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not _owns(admin, user):
        raise HTTPException(status_code=403, detail="Not your user")
    return user


@router.get("", response_model=list[UserOut])
async def list_users(admin: Admin = Depends(get_current_admin), db: AsyncSession = Depends(get_session)):
    stmt = select(User).order_by(User.created_at.desc())
    if not admin.is_superadmin:
        stmt = stmt.where(User.created_by_admin_id == admin.id)
    users = (await db.execute(stmt)).scalars().all()
    online = livecache.snapshot()["online"]
    names = await _admin_names(db)
    live = await _live_by_user(db)
    return [_to_out(u, online, names, live.get(u.username, 0)) for u in users]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    if not admin.can_create_users:
        raise HTTPException(status_code=403, detail="You don't have permission to create users")
    if not admin.is_superadmin and admin.max_users > 0:
        owned = (await db.execute(
            select(func.count(User.id)).where(User.created_by_admin_id == admin.id)
        )).scalar_one()
        if owned >= admin.max_users:
            raise HTTPException(status_code=403, detail=f"User limit reached ({admin.max_users})")

    exists = await db.execute(select(User).where(User.username == payload.username))
    if exists.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username already exists")

    user = User(
        username=payload.username,
        password_hash=payload.password,
        quota_bytes=payload.quota_bytes,
        rate_up_kbps=payload.rate_up_kbps,
        rate_down_kbps=payload.rate_down_kbps,
        is_active=payload.is_active,
        expires_at=payload.expires_at,
        note=payload.note or "",
        outbound=outbound.normalize(payload.outbound),
        l2tp_mode=_norm_mode(payload.l2tp_mode),
        created_by_admin_id=admin.id,
    )
    db.add(user)
    await audit.record(db, "create_user", payload.username, _describe_create(user), actor=admin.username)
    await db.commit()
    await db.refresh(user)
    await chap_secrets.rewrite(db)
    online = livecache.snapshot()["online"]
    names = await _admin_names(db)
    return _to_out(user, online, names)


@router.get("/{user_id}", response_model=UserOut)
async def get_user(user_id: int, admin: Admin = Depends(get_current_admin), db: AsyncSession = Depends(get_session)):
    user = await _require_owned(db, admin, user_id)
    online = livecache.snapshot()["online"]
    names = await _admin_names(db)
    live = await _live_by_user(db)
    return _to_out(user, online, names, live.get(user.username, 0))


@router.put("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int,
    payload: UserUpdate,
    admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    user = await _require_owned(db, admin, user_id)
    data = payload.model_dump(exclude_unset=True)
    if "outbound" in data:
        data["outbound"] = outbound.normalize(data["outbound"])
    if "l2tp_mode" in data:
        data["l2tp_mode"] = _norm_mode(data["l2tp_mode"])
    changes: list[str] = []
    new_password = data.pop("password", None)
    if new_password:
        user.password_hash = new_password
        changes.append("password (changed)")
    for field, new_value in data.items():
        old_value = getattr(user, field, None)
        if old_value == new_value:
            continue  # field sent but unchanged — don't log noise
        label = _FIELD_LABELS.get(field, field)
        changes.append(f"{label}: {_fmt_audit_value(field, old_value)} → {_fmt_audit_value(field, new_value)}")
        setattr(user, field, new_value)
    detail = "; ".join(changes) if changes else "no changes"
    await audit.record(db, "update_user", user.username, detail, actor=admin.username)
    await db.commit()
    await db.refresh(user)
    await chap_secrets.rewrite(db)
    if not user.enabled_for_auth:
        await pppd.terminate_user(db, user.username)
    await outbound.reconcile(db)  # apply outbound change to an already-online user
    online = livecache.snapshot()["online"]
    names = await _admin_names(db)
    return _to_out(user, online, names)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: int, admin: Admin = Depends(get_current_admin), db: AsyncSession = Depends(get_session)):
    user = await _require_owned(db, admin, user_id)
    username = user.username
    detail = f"quota={_human_bytes(user.quota_bytes)}, used={_human_bytes(user.used_bytes)}"
    await pppd.terminate_user(db, username)
    await db.delete(user)
    await audit.record(db, "delete_user", username, detail, actor=admin.username)
    await db.commit()
    await chap_secrets.rewrite(db)
    return None


@router.post("/{user_id}/reset-quota", response_model=UserOut)
async def reset_quota(user_id: int, admin: Admin = Depends(get_current_admin), db: AsyncSession = Depends(get_session)):
    user = await _require_owned(db, admin, user_id)
    detail = f"was {_human_bytes(user.used_bytes)}"
    user.used_bytes = 0
    await _rebaseline_open_sessions(db, user.username)
    await audit.record(db, "reset_quota", user.username, detail, actor=admin.username)
    await db.commit()
    await db.refresh(user)
    await chap_secrets.rewrite(db)
    online = livecache.snapshot()["online"]
    names = await _admin_names(db)
    return _to_out(user, online, names)


@router.post("/{user_id}/toggle", response_model=UserOut)
async def toggle_user(user_id: int, admin: Admin = Depends(get_current_admin), db: AsyncSession = Depends(get_session)):
    user = await _require_owned(db, admin, user_id)
    user.is_active = not user.is_active
    await audit.record(db, "enable_user" if user.is_active else "disable_user", user.username, actor=admin.username)
    await db.commit()
    await db.refresh(user)
    await chap_secrets.rewrite(db)
    if not user.enabled_for_auth:
        await pppd.terminate_user(db, user.username)
    online = livecache.snapshot()["online"]
    names = await _admin_names(db)
    return _to_out(user, online, names)


@router.post("/bulk")
async def bulk_action(
    payload: BulkAction,
    admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    if payload.action not in {"enable", "disable", "delete", "reset-quota"}:
        raise HTTPException(status_code=400, detail="Unknown action")

    stmt = select(User).where(User.id.in_(payload.ids))
    if not admin.is_superadmin:
        stmt = stmt.where(User.created_by_admin_id == admin.id)
    users = (await db.execute(stmt)).scalars().all()

    affected = []
    for user in users:
        affected.append(user.username)
        if payload.action == "enable":
            user.is_active = True
        elif payload.action == "disable":
            user.is_active = False
        elif payload.action == "reset-quota":
            user.used_bytes = 0
            await _rebaseline_open_sessions(db, user.username)
        elif payload.action == "delete":
            await pppd.terminate_user(db, user.username)
            await db.delete(user)

    await audit.record(db, f"bulk_{payload.action}", f"{len(affected)} users", ", ".join(affected[:20]), actor=admin.username)
    await db.commit()
    await chap_secrets.rewrite(db)
    if payload.action == "disable":
        for username in affected:
            await pppd.terminate_user(db, username)
    return {"action": payload.action, "affected": affected}
