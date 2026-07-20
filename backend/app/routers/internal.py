"""Internal endpoints consumed by the ppp ip-up.d / ip-down.d hooks.

Localhost only. Never proxied by nginx (it returns 404 for /api/internal),
and uvicorn binds 127.0.0.1, so these are unreachable from outside.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import accounting, audit, outbound, pppd
from ..database import get_session
from ..models import Session as SessionRow
from ..models import User
from ..schemas import RateOut, SessionDownIn, SessionUpIn, SessionUpOut

log = logging.getLogger("vpn-panel.internal")

router = APIRouter(prefix="/api/internal", tags=["internal"])


def _local_only(request: Request) -> None:
    client = request.client.host if request.client else ""
    if client not in {"127.0.0.1", "::1", "localhost"}:
        raise HTTPException(status_code=403, detail="Local access only")


@router.get("/rate/{username}", response_model=RateOut, dependencies=[Depends(_local_only)])
async def get_rate(username: str, db: AsyncSession = Depends(get_session)):
    user = (
        await db.execute(select(User).where(User.username == username))
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Unknown user")
    return RateOut(
        username=user.username,
        rate_up_kbps=user.rate_up_kbps,
        rate_down_kbps=user.rate_down_kbps,
        allowed=user.enabled_for_auth,
    )


@router.post("/session-up", response_model=SessionUpOut, dependencies=[Depends(_local_only)])
async def session_up(payload: SessionUpIn, db: AsyncSession = Depends(get_session)):
    # Remove any stale row for the same interface, then register.
    await db.execute(delete(SessionRow).where(SessionRow.ifname == payload.ifname))
    # Classify from the address pool (shared helper) so a raw, no-IPsec session
    # is labelled L2TP-RAW in the ledger too, not just in the live view.
    proto = pppd.classify_proto(payload.peer_ip)
    # Anchor the billing baseline to the interface counter at registration so
    # this session's usage is measured from zero (ignores pre-registration bytes
    # and any counter the iface number carried from a prior session).
    base_rx, base_tx = (
        pppd.read_iface_bytes(payload.ifname) if pppd.iface_exists(payload.ifname) else (0, 0)
    )
    db.add(
        SessionRow(
            username=payload.username,
            ifname=payload.ifname,
            peer_ip=payload.peer_ip,
            pid=payload.pid,
            proto=proto,
            base_rx=base_rx,
            base_tx=base_tx,
            last_rx=base_rx,
            last_tx=base_tx,
        )
    )
    user = (
        await db.execute(select(User).where(User.username == payload.username))
    ).scalar_one_or_none()
    if user:
        user.last_seen = datetime.now(timezone.utc)
        user.total_sessions = (user.total_sessions or 0) + 1

    # Refuse a session that arrived on the endpoint this account is NOT set to
    # (see pppd.mode_conflict). The row is registered ANYWAY and only then
    # refused: ip-up drops the link within a fraction of a second, but whatever
    # bytes did flow are still finalized against the user's quota, so the reject
    # path can never become a way to get free traffic. The enforcer re-checks
    # every cycle, so a session survives even a total ip-up failure by at most
    # one poll interval.
    reason = pppd.mode_conflict(user.l2tp_mode, payload.peer_ip) if user else ""
    if reason:
        log.warning("refusing %s on %s (%s): %s", payload.username, payload.ifname, payload.peer_ip, reason)
        await audit.record(
            db, "reject_session", payload.username,
            f"{reason} (iface={payload.ifname}, ip={payload.peer_ip})", actor="system",
        )

    await db.commit()
    await outbound.reconcile(db)  # route this client via WARP if its user opted in
    return SessionUpOut(detail="registered", allowed=not reason, reason=reason)


@router.post("/session-down", dependencies=[Depends(_local_only)])
async def session_down(payload: SessionDownIn, db: AsyncSession = Depends(get_session)):
    now = datetime.now(timezone.utc)
    row = (
        await db.execute(select(SessionRow).where(SessionRow.ifname == payload.ifname))
    ).scalar_one_or_none()
    user = (
        await db.execute(select(User).where(User.username == payload.username))
    ).scalar_one_or_none()

    # Primary finalize path (fast, on disconnect). The enforcer's debounced
    # iface-gone check is the fallback if this hook never arrives (crash). The
    # first to finalize deletes the row; the other sees row=None and skips, so
    # bytes are committed exactly once.
    if row is not None and user is not None and row.username == payload.username:
        # Final counters since the billing base. Prefer the freshest sysfs read;
        # for a fresh session (base 0) also take pppd's authoritative this-session
        # totals as a floor — both are measured from session start, so they are
        # directly comparable (no sysfs/pppd absolute-value mixing).
        eff_rx, eff_tx = row.last_rx, row.last_tx
        if pppd.iface_exists(payload.ifname):
            rx, tx = pppd.read_iface_bytes(payload.ifname)
            eff_rx, eff_tx = max(eff_rx, rx), max(eff_tx, tx)
        if row.base_rx == 0 and payload.in_octets > eff_rx:
            eff_rx = payload.in_octets
        if row.base_tx == 0 and payload.out_octets > eff_tx:
            eff_tx = payload.out_octets
        in_b = pppd.usage_delta(eff_rx, row.base_rx)
        out_b = pppd.usage_delta(eff_tx, row.base_tx)
        user.used_bytes += in_b + out_b
        user.last_seen = now

        duration = payload.session_time
        if duration <= 0 and row.started_at is not None:
            started = row.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            duration = max(0, int((now - started).total_seconds()))
        await accounting.record_session(
            db,
            username=row.username,
            proto=row.proto,
            ifname=row.ifname,
            started_at=row.started_at,
            bytes_in=in_b,
            bytes_out=out_b,
            duration=duration,
        )
        await db.delete(row)
    elif user is not None:
        # Row already finalized (enforcer) or user/ifname mismatch -> just touch.
        user.last_seen = now

    await db.commit()
    await outbound.reconcile(db)  # drop this client's WARP mapping on disconnect
    return {"detail": "recorded"}
