"""Dashboard stats + health endpoints."""

import asyncio
import os
import time

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import accounting, livecache, pppd
from ..config import settings
from ..database import get_session
from ..deps import get_current_admin, require_admin
from ..models import Admin, User
from ..schemas import HealthOut, QuotaUser, StatsOut, TopUser

router = APIRouter(prefix="/api", tags=["stats"])

_START = time.monotonic()


def _port_bound(port: int) -> bool:
    """True if any socket is bound to `port` (IPv4 or IPv6).

    Reads /proc/net/{udp,udp6,tcp,tcp6} directly — dependency-free and correct
    regardless of WHICH daemon/service provides it (Libreswan pluto vs strongSwan
    charon; xl2tpd vs accel-ppp). Service-name checks (`systemctl is-active`)
    broke when the daemon ran under a different unit, falsely showing "Down".
    """
    hexport = f"{port:04X}"
    for fn in ("/proc/net/udp", "/proc/net/udp6", "/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(fn) as fh:
                next(fh, None)  # skip header
                for line in fh:
                    parts = line.split()
                    if len(parts) > 1 and parts[1].rsplit(":", 1)[-1].upper() == hexport:
                        return True
        except OSError:
            continue
    return False


@router.get("/stats", response_model=StatsOut)
async def stats(admin: Admin = Depends(get_current_admin), db: AsyncSession = Depends(get_session)):
    scoped = not admin.is_superadmin

    def scope(stmt):
        return stmt.where(User.created_by_admin_id == admin.id) if scoped else stmt

    total = (await db.execute(scope(select(func.count(User.id))))).scalar_one()
    active = (
        await db.execute(scope(select(func.count(User.id)).where(User.is_active.is_(True))))
    ).scalar_one()

    users = (await db.execute(scope(select(User)))).scalars().all()
    owned = {u.username for u in users} if scoped else None

    snap = livecache.snapshot()  # from the shared snapshot, no per-request sysfs scan
    all_sessions = snap["sessions"]
    # Live overlay = billing bytes of currently-open PPP sessions. WireGuard is
    # already committed to used_bytes continuously, so livecache excludes it from
    # the overlay (no double-count). Reuse those values instead of recomputing.
    all_live = snap["live_total"]
    sessions = all_sessions
    if owned is not None:
        sessions = [s for s in all_sessions if s.username in owned]
    online = {s.username for s in sessions}
    rx_rate = sum(s.rx_rate_bps for s in sessions)
    tx_rate = sum(s.tx_rate_bps for s in sessions)

    # Effective per-user usage = committed (closed) + live overlay of open sessions.
    live_by_user = snap["live_by_user"]

    quota_warnings = 0
    expired = 0
    near: list[QuotaUser] = []
    for u in users:
        if u.is_expired:
            expired += 1
        effective = u.used_bytes + live_by_user.get(u.username, 0)
        if u.quota_bytes > 0:
            if effective >= 0.8 * u.quota_bytes:
                quota_warnings += 1
            near.append(QuotaUser(
                username=u.username,
                used_bytes=effective,
                quota_bytes=u.quota_bytes,
                percent=round(min(9999.0, effective / u.quota_bytes * 100), 1),
                online=u.username in online,
            ))
    # Users closest to exhausting their data first (over-quota ones lead).
    near_quota = sorted(near, key=lambda q: q.percent, reverse=True)[:10]

    top_rows = (
        await db.execute(scope(select(User)).order_by(User.used_bytes.desc()).limit(5))
    ).scalars().all()
    top_users = [
        TopUser(
            username=u.username,
            used_bytes=u.used_bytes + live_by_user.get(u.username, 0),
            quota_bytes=u.quota_bytes,
            online=u.username in online,
        )
        for u in top_rows
    ]

    # Total ever = committed billing across all users + current live overlay
    # (uses preserved used_bytes; immune to log rotation). Today = closed-session
    # ledger for today + the live (ongoing) overlay.
    total_used = (await db.execute(select(func.coalesce(func.sum(User.used_bytes), 0)))).scalar_one()
    traffic_total = int(total_used or 0) + all_live
    traffic_today = await accounting.ledger_today_bytes(db) + all_live

    return StatsOut(
        total_users=total,
        active_users=active,
        online_count=len(online),
        traffic_today_bytes=traffic_today,
        traffic_total_bytes=traffic_total,
        quota_warnings=quota_warnings,
        expired_users=expired,
        rx_rate_bps=rx_rate,
        tx_rate_bps=tx_rate,
        top_users=top_users,
        near_quota=near_quota,
    )


@router.get("/health", response_model=HealthOut)
async def health(db: AsyncSession = Depends(get_session)):
    db_ok = True
    try:
        await db.execute(select(func.count(User.id)))
    except Exception:  # noqa: BLE001
        db_ok = False

    # Detect by listening port (works for pluto/charon and xl2tpd/accel alike).
    ipsec_ok = _port_bound(500) or _port_bound(4500)
    l2tp_ok = _port_bound(1701)

    return HealthOut(
        status="ok" if db_ok else "degraded",
        xl2tpd=l2tp_ok,
        ipsec=ipsec_ok,
        db=db_ok,
        accounting_log=os.path.exists(settings.acct_log),
        uptime_seconds=time.monotonic() - _START,
    )
