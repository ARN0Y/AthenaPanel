"""User CRUD endpoints — scoped per admin (RBAC)."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import appsettings, audit, chap_secrets, livecache, nodes as nodes_mod, outbound, pppd
from ..subtoken import make_token
from ..database import get_session
from ..deps import get_current_admin
from ..models import LOCAL_NODE_ID, Admin, Node, User
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
    "node_id": "node",
    "owner_admin_id": "owner",
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
    counter or disconnecting the session).

    A local session is rebaselined against this host's sysfs. A remote one is
    rebaselined against the counter its node last reported, which is at most one
    report interval old — good enough precisely because a remote session row is
    a display, not a ledger: its billing lives in used_bytes, which the caller
    has just zeroed. Skipping them instead would leave the reset user staring at
    the pre-reset total until they happened to reconnect.
    """
    rows = (
        await db.execute(select(SessionRow).where(SessionRow.username == username))
    ).scalars().all()
    for r in rows:
        if r.node_id == LOCAL_NODE_ID:
            if not pppd.iface_exists(r.ifname):
                continue
            rx, tx = pppd.read_iface_bytes(r.ifname)
            r.last_rx, r.last_tx = rx, tx
        r.base_rx, r.base_tx = r.last_rx, r.last_tx


def _validate_outbound(value: str | None) -> str:
    """Resolve a submitted outbound, refusing one that does not exist.

    outbound.normalize() degrades an unknown name to direct, which is right for
    a value already in the database — an outbound deleted underneath a user must
    not break them. It is wrong for an explicit request: the operator picked a
    location, got a 200 back, and their user quietly egressed somewhere else.
    An outbound still awaiting its server counts as unknown here, because
    assigning to it would do nothing.
    """
    if value is None or not str(value).strip():
        return outbound.DIRECT
    name = str(value).strip().lower()
    if name not in outbound.known_names():
        raise HTTPException(
            status_code=400,
            detail=f"No outbound named '{name}' is available. Add it under "
                   "Settings → Outbounds and finish its registration first.",
        )
    return name


def _norm_mode(value: str | None) -> str:
    """L2TP mode: 'raw' (no IPsec) — anything else falls back to 'ipsec'."""
    return "raw" if str(value or "").strip().lower() == "raw" else "ipsec"


class _NodeCtx:
    """Node rows plus panel-wide settings, fetched once per request.

    _to_out runs for every user, and resolving endpoints needs both. Loading
    them per user would turn one list call into ~200 queries.
    """

    __slots__ = ("by_id", "app_settings")

    def __init__(self, by_id: dict[int, Node], app_settings: dict):
        self.by_id = by_id
        self.app_settings = app_settings

    def endpoints(self, node_id: int) -> dict[str, str]:
        return nodes_mod.effective_endpoints(self.by_id.get(node_id), self.app_settings)

    def name(self, node_id: int) -> str:
        node = self.by_id.get(node_id)
        return node.name if node else f"#{node_id}"


async def _node_ctx(db: AsyncSession) -> _NodeCtx:
    rows = (await db.execute(select(Node))).scalars().all()
    return _NodeCtx({n.id: n for n in rows}, await appsettings.get_all(db))


def _to_out(
    user: User,
    online: set[str],
    names: dict[int, str],
    live_bytes: int = 0,
    ctx: _NodeCtx | None = None,
) -> UserOut:
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
    out.node_id = user.node_id or LOCAL_NODE_ID
    if ctx is not None:
        out.node_name = ctx.name(out.node_id)
        ep = ctx.endpoints(out.node_id)
        out.endpoint_l2tp = ep["l2tp"]
        out.endpoint_l2tp_raw = ep["l2tp_raw"]
        out.endpoint_sstp = ep["sstp"]
        out.endpoint_wg = ep["wg"]
    return out


async def _validate_node(db: AsyncSession, node_id: int | None) -> int | None:
    """Reject an assignment to a node that does not exist or is switched off.

    Silently accepting it would strand the account: chap-secrets is only pushed
    to real nodes, so the user would authenticate nowhere and the panel would
    show nothing wrong.
    """
    if node_id is None:
        return None
    node = (await db.execute(select(Node).where(Node.id == node_id))).scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=400, detail="No such node")
    if not node.enabled:
        raise HTTPException(status_code=400, detail=f"Node '{node.name}' is disabled")
    return node_id


async def _resolve_owner(
    db: AsyncSession, actor: Admin, owner_admin_id: int | None, incoming: int
) -> Admin:
    """Validate a transfer target and prove it has room for `incoming` accounts.

    Only a superadmin may set an owner: a reseller who could would either hand
    their accounts away to escape their own cap, or take someone else's.

    The cap matters here specifically. `max_users` is checked when a reseller
    creates an account, so without the same check on transfer the operator could
    push a reseller far past the limit they were given and nobody would notice
    until the reseller tried to create their next account and was refused while
    already over.
    """
    if not actor.is_superadmin:
        raise HTTPException(status_code=403, detail="Only a superadmin can change an account's owner")
    target = await db.get(Admin, owner_admin_id)
    if not target:
        raise HTTPException(status_code=404, detail="No such admin")
    if not target.is_active:
        raise HTTPException(status_code=400, detail=f"{target.username} is disabled and cannot own accounts")
    if not target.is_superadmin and target.max_users > 0:
        held = (await db.execute(
            select(func.count(User.id)).where(User.created_by_admin_id == target.id)
        )).scalar_one()
        if held + incoming > target.max_users:
            room = max(0, target.max_users - held)
            raise HTTPException(
                status_code=403,
                detail=(f"{target.username} may hold {target.max_users} accounts and already has "
                        f"{held} — room for {room}, not {incoming}"),
            )
    return target


def _owns(admin: Admin, user: User) -> bool:
    return admin.is_superadmin or user.created_by_admin_id == admin.id


async def _require_owned(db: AsyncSession, admin: Admin, user_id: int) -> User:
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not _owns(admin, user):
        # Deliberately indistinguishable from "no such id" — a 403 here would
        # confirm the account exists to an admin who may not see it.
        raise HTTPException(status_code=404, detail="User not found")
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
    ctx = await _node_ctx(db)
    return [_to_out(u, online, names, live.get(u.username, 0), ctx) for u in users]


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

    owner = admin
    if payload.owner_admin_id is not None and payload.owner_admin_id != admin.id:
        owner = await _resolve_owner(db, admin, payload.owner_admin_id, 1)

    user = User(
        username=payload.username,
        password_hash=payload.password,
        quota_bytes=payload.quota_bytes,
        rate_up_kbps=payload.rate_up_kbps,
        rate_down_kbps=payload.rate_down_kbps,
        is_active=payload.is_active,
        expires_at=payload.expires_at,
        note=payload.note or "",
        outbound=_validate_outbound(payload.outbound),
        l2tp_mode=_norm_mode(payload.l2tp_mode),
        created_by_admin_id=owner.id,
    )
    db.add(user)
    detail = _describe_create(user)
    if owner.id != admin.id:
        detail += f", owner={owner.username}"
    await audit.record(db, "create_user", payload.username, detail, actor=admin.username)
    await db.commit()
    await db.refresh(user)
    await chap_secrets.rewrite(db)
    online = livecache.snapshot()["online"]
    names = await _admin_names(db)
    return _to_out(user, online, names, 0, await _node_ctx(db))


@router.get("/{user_id}", response_model=UserOut)
async def get_user(user_id: int, admin: Admin = Depends(get_current_admin), db: AsyncSession = Depends(get_session)):
    user = await _require_owned(db, admin, user_id)
    online = livecache.snapshot()["online"]
    names = await _admin_names(db)
    live = await _live_by_user(db)
    return _to_out(user, online, names, live.get(user.username, 0), await _node_ctx(db))


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
        data["outbound"] = _validate_outbound(data["outbound"])
    if "l2tp_mode" in data:
        data["l2tp_mode"] = _norm_mode(data["l2tp_mode"])
    if "node_id" in data:
        await _validate_node(db, data["node_id"])
    changes: list[str] = []
    new_password = data.pop("password", None)
    if new_password:
        user.password_hash = new_password
        changes.append("password (changed)")

    # Ownership is handled apart from the generic loop below: the API field and
    # the column have different names, and the move has to be authorised and
    # capacity-checked before anything else on the account is touched.
    new_owner_id = data.pop("owner_admin_id", None)
    if new_owner_id is not None and new_owner_id != user.created_by_admin_id:
        target = await _resolve_owner(db, admin, new_owner_id, 1)
        names = await _admin_names(db)
        was = names.get(user.created_by_admin_id or -1, "—")
        changes.append(f"owner: {was} → {target.username}")
        user.created_by_admin_id = target.id
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
        await nodes_mod.terminate_user(db, user)
    await outbound.reconcile(db)  # apply outbound change to an already-online user
    online = livecache.snapshot()["online"]
    names = await _admin_names(db)
    return _to_out(user, online, names, 0, await _node_ctx(db))


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
    return _to_out(user, online, names, 0, await _node_ctx(db))


@router.post("/{user_id}/toggle", response_model=UserOut)
async def toggle_user(user_id: int, admin: Admin = Depends(get_current_admin), db: AsyncSession = Depends(get_session)):
    user = await _require_owned(db, admin, user_id)
    user.is_active = not user.is_active
    await audit.record(db, "enable_user" if user.is_active else "disable_user", user.username, actor=admin.username)
    await db.commit()
    await db.refresh(user)
    await chap_secrets.rewrite(db)
    if not user.enabled_for_auth:
        await nodes_mod.terminate_user(db, user)
    online = livecache.snapshot()["online"]
    names = await _admin_names(db)
    return _to_out(user, online, names, 0, await _node_ctx(db))


@router.post("/bulk")
async def bulk_action(
    payload: BulkAction,
    admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    if payload.action not in {"enable", "disable", "delete", "reset-quota", "assign"}:
        raise HTTPException(status_code=400, detail="Unknown action")

    stmt = select(User).where(User.id.in_(payload.ids))
    if not admin.is_superadmin:
        stmt = stmt.where(User.created_by_admin_id == admin.id)
    users = (await db.execute(stmt)).scalars().all()

    target: Admin | None = None
    if payload.action == "assign":
        if payload.owner_admin_id is None:
            raise HTTPException(status_code=400, detail="assign needs owner_admin_id")
        # Count only the accounts that would actually move, so re-running an
        # assign that is already partly done cannot fail against the cap.
        moving = sum(1 for u in users if u.created_by_admin_id != payload.owner_admin_id)
        target = await _resolve_owner(db, admin, payload.owner_admin_id, moving)

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
        elif payload.action == "assign":
            user.created_by_admin_id = target.id
        elif payload.action == "delete":
            await nodes_mod.terminate_user(db, user)
            await db.delete(user)

    detail = ", ".join(affected[:20])
    if target is not None:
        detail = f"→ {target.username}: {detail}"
    await audit.record(db, f"bulk_{payload.action}", f"{len(affected)} users", detail, actor=admin.username)
    await db.commit()
    await chap_secrets.rewrite(db)
    if payload.action == "disable":
        for username in affected:
            await pppd.terminate_user(db, username)
    return {"action": payload.action, "affected": affected}
