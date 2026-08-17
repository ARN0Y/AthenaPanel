"""Public API v1 — the stable surface third-party code is allowed to build on.

Why this exists alongside the endpoints the panel's own UI uses: those ship in
the same commit as the UI and are free to change shape whenever the UI does. A
Telegram bot, a billing integration or a reseller's script cannot be redeployed
in that commit. So this is a deliberately narrow, deliberately dull facade over
the same service layer, with its own schemas and its own compatibility promise.

Three rules hold everywhere in here:

  * A caller is an ADMIN, whether it presented a session token or an API key.
    Every query goes through rbac.py, so a reseller's key sees the reseller's
    accounts and a superadmin's key sees everything, and neither this module
    nor the bot author has to know that rule exists.
  * Accounts are addressed by USERNAME, not by database id. A bot stores what
    its customer typed; making it store a surrogate key would mean it has to
    keep a mapping in sync with a system it does not own.
  * Renewals ADD. `POST /users/{u}/extend` with days=30 is the operation a
    renewal actually is; computing an absolute new expiry client-side races
    with the customer's clock and with any other bot doing the same thing.
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import appsettings, audit, chap_secrets, livecache
from .. import nodes as nodesvc
from .. import outbound, rbac
from ..subtoken import make_token
from ..database import get_session
from ..deps import Principal, get_principal, require_scope
from ..models import Admin, LOCAL_NODE_ID, Node, Session as SessionRow, User
from ..schemas import (
    V1Extend,
    V1Page,
    V1SessionOut,
    V1UserCreate,
    V1UserOut,
    V1UserUpdate,
)

log = logging.getLogger("vpn-panel.api.v1")

router = APIRouter(prefix="/api/v1", tags=["public-api-v1"])

GB = 1024 ** 3


def _gb(b: int) -> float:
    return round((b or 0) / GB, 3)


def _aware(ts: datetime | None) -> datetime | None:
    if ts is not None and ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


async def _serialize(
    db: AsyncSession, u: User, *, ctx: "_Ctx",
) -> V1UserOut:
    node_names, owners, online, live, node_rows, app_settings = ctx
    node_id = u.node_id or LOCAL_NODE_ID
    ep = nodesvc.effective_endpoints(node_rows.get(node_id), app_settings)
    endpoints = {
        "l2tp": ep["l2tp"], "l2tp_raw": ep["l2tp_raw"],
        "sstp": ep["sstp"], "wireguard": ep["wg"],
    }
    base = (app_settings.get("sub_address") or "").strip().rstrip("/")
    token = make_token(u.id)
    sub_url = f"{base}/sub/{token}" if base else f"/sub/{token}"
    # Effective usage = committed bytes plus whatever the live session has moved
    # since. The same sum the panel shows, from the same cache.
    used = (u.used_bytes or 0) + max(0, live.get(u.username, 0))
    limit = u.quota_bytes or 0
    remaining = max(0, limit - used) if limit else None
    expires = _aware(u.expires_at)
    days = None
    if expires is not None:
        days = max(0, (expires - datetime.now(timezone.utc)).days)
    return V1UserOut(
        username=u.username,
        password=u.password_hash,
        enabled=bool(u.is_active),
        online=u.username in online,
        limit_bytes=limit,
        limit_gb=_gb(limit),
        used_bytes=used,
        used_gb=_gb(used),
        remaining_bytes=remaining,
        remaining_gb=_gb(remaining) if remaining is not None else None,
        usage_percent=round(used / limit * 100, 2) if limit else None,
        quota_exceeded=bool(limit and used >= limit),
        expires_at=expires,
        days_remaining=days,
        expired=bool(expires and expires < datetime.now(timezone.utc)),
        created_at=_aware(u.created_at),
        last_seen=_aware(u.last_seen),
        total_sessions=u.total_sessions or 0,
        node_id=node_id,
        node_name=node_names.get(node_id, ""),
        outbound=u.outbound or "direct",
        l2tp_mode=u.l2tp_mode or "ipsec",
        rate_up_kbps=u.rate_up_kbps or 0,
        rate_down_kbps=u.rate_down_kbps or 0,
        note=u.note or "",
        owner=owners.get(u.created_by_admin_id or 0, ""),
        endpoints=endpoints,
        subscription_url=sub_url,
    )


_Ctx = tuple


async def _context(db: AsyncSession) -> _Ctx:
    """Everything _serialize needs, fetched once per request.

    Serializing runs per user, and resolving a node's endpoints needs both the
    node rows and the panel-wide settings. Loading them per user would turn one
    list call into a few hundred queries — the same reason users.py has _NodeCtx.
    """
    node_rows = {n.id: n for n in (await db.execute(select(Node))).scalars().all()}
    owners = {a.id: a.username for a in (await db.execute(select(Admin))).scalars().all()}
    snap = livecache.snapshot()
    return (
        {i: n.name for i, n in node_rows.items()},
        owners,
        set(snap["online"]),
        dict(snap["live_by_user"]),
        node_rows,
        await appsettings.get_all(db),
    )


async def _get_owned(db: AsyncSession, principal: Principal, username: str) -> User:
    stmt = rbac.scope_users(select(User).where(User.username == username), principal.admin)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user is None:
        # Deliberately the same 404 whether the account does not exist or
        # belongs to another reseller: distinguishing them would let one
        # reseller enumerate another's customer list.
        raise HTTPException(status_code=404, detail=f"No account named '{username}'")
    return user


# ---- identity --------------------------------------------------------------


@router.get("/me")
async def whoami(principal: Principal = Depends(get_principal)):
    """Who this credential is, and what it may do.

    Guarded by authentication only, never by a scope. This is how a caller
    DISCOVERS its scopes; requiring one to ask would mean a narrowly scoped key
    cannot find out why it is being refused elsewhere — the one question it
    most needs answered.
    """
    key = principal.key
    return {
        "admin": principal.admin.username,
        "role": principal.admin.role,
        "auth": "api_key" if key else "session",
        "key_prefix": key.prefix if key else None,
        "scopes": sorted(key.scope_set) if key else [],
        "unrestricted_scopes": bool(key and not key.scope_set) or not key,
        "rate_limit_per_minute": (key.rate_limit or 120) if key else None,
    }


# ---- accounts --------------------------------------------------------------


@router.get("/users", response_model=V1Page)
async def list_users(
    search: str = Query("", description="Substring match on username or note"),
    status: str = Query("", description="active | disabled | online | expired | exceeded"),
    node_id: int | None = None,
    outbound_name: str | None = Query(None, alias="outbound"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sort: str = Query("username", description="username | created_at | used_bytes | expires_at"),
    order: str = Query("asc", description="asc | desc"),
    principal: Principal = Depends(require_scope("users:read")),
    db: AsyncSession = Depends(get_session),
):
    ctx = await _context(db)
    _names, _owners, online, live, _rows, _cfg = ctx
    stmt = rbac.scope_users(select(User), principal.admin)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(User.username.ilike(like) | User.note.ilike(like))
    if node_id is not None:
        stmt = stmt.where(User.node_id == node_id)
    if outbound_name:
        stmt = stmt.where(User.outbound == outbound_name)
    now = datetime.now(timezone.utc)
    if status == "active":
        stmt = stmt.where(User.is_active.is_(True))
    elif status == "disabled":
        stmt = stmt.where(User.is_active.is_(False))
    elif status == "expired":
        stmt = stmt.where(User.expires_at.is_not(None), User.expires_at < now)

    rows = (await db.execute(stmt)).scalars().all()
    # online/exceeded are live facts, not columns, so they filter in memory —
    # after the database has already narrowed everything it can.
    if status == "online":
        rows = [u for u in rows if u.username in online]
    elif status == "exceeded":
        rows = [u for u in rows
                if u.quota_bytes and (u.used_bytes or 0) + max(0, live.get(u.username, 0)) >= u.quota_bytes]

    keyfn = {
        "username": lambda u: u.username.lower(),
        "created_at": lambda u: _aware(u.created_at) or datetime.min.replace(tzinfo=timezone.utc),
        "used_bytes": lambda u: (u.used_bytes or 0) + max(0, live.get(u.username, 0)),
        "expires_at": lambda u: _aware(u.expires_at) or datetime.max.replace(tzinfo=timezone.utc),
    }.get(sort)
    if keyfn is None:
        raise HTTPException(status_code=400, detail=f"Cannot sort by '{sort}'")
    rows.sort(key=keyfn, reverse=(order == "desc"))

    total = len(rows)
    start = (page - 1) * page_size
    window = rows[start:start + page_size]
    items = [await _serialize(db, u, ctx=ctx) for u in window]
    return V1Page(
        items=[i.model_dump() for i in items],
        total=total, page=page, page_size=page_size,
        pages=max(1, -(-total // page_size)),
    )


@router.get("/users/{username}", response_model=V1UserOut)
async def get_user(
    username: str,
    principal: Principal = Depends(require_scope("users:read")),
    db: AsyncSession = Depends(get_session),
):
    user = await _get_owned(db, principal, username)
    ctx = await _context(db)
    return await _serialize(db, user, ctx=ctx)


@router.post("/users", response_model=V1UserOut, status_code=201)
async def create_user(
    payload: V1UserCreate,
    principal: Principal = Depends(require_scope("users:write")),
    db: AsyncSession = Depends(get_session),
):
    admin = principal.admin
    if not admin.is_superadmin and not admin.can_create_users:
        raise HTTPException(status_code=403, detail="This account may not create users")

    existing = (
        await db.execute(select(User).where(User.username == payload.username))
    ).scalar_one_or_none()
    if existing is not None:
        # 409 rather than a silent overwrite: a bot retrying a timed-out
        # request must be able to tell "already created" from "created twice".
        raise HTTPException(status_code=409, detail=f"'{payload.username}' already exists")

    if not admin.is_superadmin and admin.max_users:
        owned = (await db.execute(
            select(func.count()).select_from(User).where(User.created_by_admin_id == admin.id)
        )).scalar_one()
        if owned >= admin.max_users:
            raise HTTPException(
                status_code=403,
                detail=f"Account limit reached ({owned}/{admin.max_users})",
            )

    expires = payload.expires_at
    if expires is None and payload.duration_days:
        expires = datetime.now(timezone.utc) + timedelta(days=payload.duration_days)

    user = User(
        username=payload.username,
        password_hash=payload.password or secrets.token_urlsafe(9),
        quota_bytes=int(payload.limit_gb * GB),
        rate_up_kbps=payload.rate_up_kbps,
        rate_down_kbps=payload.rate_down_kbps,
        is_active=payload.enabled,
        expires_at=expires,
        note=payload.note or "",
        outbound=outbound.normalize(payload.outbound),
        l2tp_mode="raw" if (payload.l2tp_mode or "").lower() == "raw" else "ipsec",
        node_id=payload.node_id or LOCAL_NODE_ID,
        created_by_admin_id=admin.id,
    )
    if payload.node_id is not None and await db.get(Node, payload.node_id) is None:
        raise HTTPException(status_code=400, detail=f"No node with id {payload.node_id}")
    db.add(user)
    await audit.record(db, "create_user", user.username, "via api v1", actor=principal.label)
    await db.commit()
    await db.refresh(user)
    await chap_secrets.rewrite(db)

    ctx = await _context(db)
    return await _serialize(db, user, ctx=ctx)


@router.patch("/users/{username}", response_model=V1UserOut)
async def update_user(
    username: str,
    payload: V1UserUpdate,
    principal: Principal = Depends(require_scope("users:write")),
    db: AsyncSession = Depends(get_session),
):
    user = await _get_owned(db, principal, username)
    data = payload.model_dump(exclude_unset=True)
    changes: list[str] = []

    if "limit_gb" in data and data["limit_gb"] is not None:
        user.quota_bytes = int(data["limit_gb"] * GB)
        changes.append(f"limit={data['limit_gb']}GB")
    for field, attr in (("enabled", "is_active"), ("expires_at", "expires_at"),
                        ("rate_up_kbps", "rate_up_kbps"), ("rate_down_kbps", "rate_down_kbps"),
                        ("note", "note"), ("password", "password_hash")):
        if field in data and data[field] is not None:
            setattr(user, attr, data[field])
            changes.append(field if field == "password" else f"{field}={data[field]}")
    if data.get("outbound") is not None:
        name = data["outbound"].strip().lower()
        if name not in outbound.known_names():
            raise HTTPException(status_code=400, detail=f"No outbound named '{name}'")
        user.outbound = name
        changes.append(f"outbound={name}")
    if data.get("l2tp_mode") is not None:
        user.l2tp_mode = "raw" if data["l2tp_mode"].lower() == "raw" else "ipsec"
        changes.append(f"l2tp_mode={user.l2tp_mode}")
    if data.get("node_id") is not None:
        if await db.get(Node, data["node_id"]) is None:
            raise HTTPException(status_code=400, detail=f"No node with id {data['node_id']}")
        user.node_id = data["node_id"]
        changes.append(f"node={data['node_id']}")

    await audit.record(db, "update_user", user.username, ", ".join(changes) or "no change",
                       actor=principal.label)
    await db.commit()
    await db.refresh(user)
    await chap_secrets.rewrite(db)
    await outbound.reconcile(db)

    ctx = await _context(db)
    return await _serialize(db, user, ctx=ctx)


@router.post("/users/{username}/extend", response_model=V1UserOut)
async def extend_user(
    username: str,
    payload: V1Extend,
    principal: Principal = Depends(require_scope("users:write")),
    db: AsyncSession = Depends(get_session),
):
    """Renew: add days and/or gigabytes, optionally zeroing usage.

    Adding is relative to what the account HAS, and an expiry already in the
    past is extended from now rather than from then — otherwise renewing a
    customer who let their account lapse for a week would silently give them
    seven days less than they paid for.
    """
    user = await _get_owned(db, principal, username)
    now = datetime.now(timezone.utc)
    parts: list[str] = []

    if payload.days:
        base = _aware(user.expires_at)
        if base is None or base < now:
            base = now
        user.expires_at = base + timedelta(days=payload.days)
        parts.append(f"+{payload.days}d")
    if payload.gb:
        user.quota_bytes = (user.quota_bytes or 0) + int(payload.gb * GB)
        parts.append(f"+{payload.gb}GB")
    if payload.reset_usage:
        user.used_bytes = 0
        parts.append("usage reset")
    if not parts:
        raise HTTPException(status_code=400, detail="Nothing to do — pass days, gb or reset_usage")

    await audit.record(db, "extend_user", user.username, ", ".join(parts), actor=principal.label)
    await db.commit()
    await db.refresh(user)
    await chap_secrets.rewrite(db)

    ctx = await _context(db)
    return await _serialize(db, user, ctx=ctx)


@router.post("/users/{username}/enable", response_model=V1UserOut)
async def enable_user(
    username: str,
    principal: Principal = Depends(require_scope("users:write")),
    db: AsyncSession = Depends(get_session),
):
    return await _set_enabled(db, principal, username, True)


@router.post("/users/{username}/disable", response_model=V1UserOut)
async def disable_user(
    username: str,
    principal: Principal = Depends(require_scope("users:write")),
    db: AsyncSession = Depends(get_session),
):
    return await _set_enabled(db, principal, username, False)


async def _set_enabled(db: AsyncSession, principal: Principal, username: str, on: bool) -> V1UserOut:
    user = await _get_owned(db, principal, username)
    user.is_active = on
    await audit.record(db, "enable_user" if on else "disable_user", user.username,
                       "via api v1", actor=principal.label)
    await db.commit()
    await db.refresh(user)
    await chap_secrets.rewrite(db)
    ctx = await _context(db)
    return await _serialize(db, user, ctx=ctx)


@router.delete("/users/{username}")
async def delete_user(
    username: str,
    principal: Principal = Depends(require_scope("users:write")),
    db: AsyncSession = Depends(get_session),
):
    user = await _get_owned(db, principal, username)
    await db.delete(user)
    await audit.record(db, "delete_user", username, "via api v1", actor=principal.label)
    await db.commit()
    await chap_secrets.rewrite(db)
    await outbound.reconcile(db)
    return {"deleted": username}


@router.get("/users/{username}/sessions", response_model=list[V1SessionOut])
async def user_sessions(
    username: str,
    principal: Principal = Depends(require_scope("sessions:read")),
    db: AsyncSession = Depends(get_session),
):
    await _get_owned(db, principal, username)
    return await _sessions(db, principal, username=username)


# ---- live sessions ---------------------------------------------------------


async def _sessions(db: AsyncSession, principal: Principal, username: str = "") -> list[V1SessionOut]:
    allowed = await rbac.owned_usernames(db, principal.admin)
    node_names = {n.id: n.name for n in (await db.execute(select(Node))).scalars().all()}
    stmt = select(SessionRow)
    if username:
        stmt = stmt.where(SessionRow.username == username)
    rows = (await db.execute(stmt)).scalars().all()
    now = datetime.now(timezone.utc)
    out: list[V1SessionOut] = []
    for r in rows:
        if not rbac.visible(allowed, r.username):
            continue
        started = _aware(r.started_at)
        rx = max(0, (r.last_rx or 0) - (r.base_rx or 0))
        tx = max(0, (r.last_tx or 0) - (r.base_tx or 0))
        out.append(V1SessionOut(
            username=r.username,
            node_id=r.node_id,
            node_name=node_names.get(r.node_id, ""),
            ifname=r.ifname,
            protocol=r.proto or "",
            peer_ip=r.peer_ip or "",
            started_at=started,
            duration_seconds=int((now - started).total_seconds()) if started else 0,
            bytes_in=rx, bytes_out=tx, bytes_total=rx + tx,
        ))
    out.sort(key=lambda s: s.username.lower())
    return out


@router.get("/sessions", response_model=list[V1SessionOut])
async def list_sessions(
    principal: Principal = Depends(require_scope("sessions:read")),
    db: AsyncSession = Depends(get_session),
):
    return await _sessions(db, principal)


@router.post("/users/{username}/disconnect")
async def disconnect_user(
    username: str,
    principal: Principal = Depends(require_scope("sessions:write")),
    db: AsyncSession = Depends(get_session),
):
    """Drop this account's live sessions.

    Returns queued=true when the account lives on a remote node: the panel
    cannot signal a node directly, so the request is queued and delivered on
    that node's next report, normally within a few seconds.
    """
    user = await _get_owned(db, principal, username)
    queued = not await nodesvc.terminate_user(db, user)
    await audit.record(db, "disconnect_user", username,
                       "queued for node" if queued else "terminated", actor=principal.label)
    await db.commit()
    return {"username": username, "queued": queued}


# ---- infrastructure --------------------------------------------------------


@router.get("/nodes")
async def list_nodes(
    principal: Principal = Depends(require_scope("system:read")),
    db: AsyncSession = Depends(get_session),
):
    """Where accounts can be placed. Resellers get id and name only — the
    address, token and health of the operator's servers are not theirs."""
    rows = (await db.execute(select(Node).order_by(Node.id))).scalars().all()
    if not principal.is_superadmin:
        return [{"id": n.id, "name": n.name} for n in rows if n.enabled]
    return [
        {
            "id": n.id, "name": n.name, "enabled": n.enabled, "is_local": n.is_local,
            "agent_version": n.agent_version or "",
            "last_seen_at": _aware(n.last_seen_at),
            "rx_rate_bps": n.rx_rate_bps or 0, "tx_rate_bps": n.tx_rate_bps or 0,
        }
        for n in rows
    ]


@router.get("/outbounds")
async def list_outbounds(
    principal: Principal = Depends(require_scope("system:read")),
    db: AsyncSession = Depends(get_session),
):
    """Egress locations an account can be assigned to."""
    rows = await outbound.status(db)
    return [
        {"name": o["id"], "label": o["name"], "country": o.get("country", ""),
         "status": o["status"], "users": o["users"] if principal.is_superadmin else None}
        for o in rows
    ]


@router.get("/stats")
async def stats(
    principal: Principal = Depends(require_scope("system:read")),
    db: AsyncSession = Depends(get_session),
):
    """Totals for whatever this caller can see. A reseller gets their own
    numbers, not the platform's."""
    _names, _owners, online, live, _rows, _cfg = await _context(db)
    rows = (await db.execute(rbac.scope_users(select(User), principal.admin))).scalars().all()
    now = datetime.now(timezone.utc)
    used = sum((u.used_bytes or 0) + max(0, live.get(u.username, 0)) for u in rows)
    return {
        "users_total": len(rows),
        "users_enabled": sum(1 for u in rows if u.is_active),
        "users_online": sum(1 for u in rows if u.username in online),
        "users_expired": sum(1 for u in rows
                             if u.expires_at and _aware(u.expires_at) < now),
        "users_quota_exceeded": sum(
            1 for u in rows
            if u.quota_bytes and (u.used_bytes or 0) + max(0, live.get(u.username, 0)) >= u.quota_bytes
        ),
        "used_bytes_total": used,
        "used_gb_total": _gb(used),
    }
