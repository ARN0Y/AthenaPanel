"""Internal endpoints consumed by the ppp ip-up.d / ip-down.d hooks.

Localhost only. Never proxied by nginx (it returns 404 for /api/internal),
and uvicorn binds 127.0.0.1, so these are unreachable from outside.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import accounting, audit, outbound, pppd, tasks
from ..database import get_session
from ..models import LOCAL_NODE_ID, Session as SessionRow
from ..models import User
from ..schemas import RateOut, SessionDownIn, SessionUpIn, SessionUpOut

log = logging.getLogger("vpn-panel.internal")

router = APIRouter(prefix="/api/internal", tags=["internal"])


async def _register_session(db: AsyncSession, **values) -> None:
    """INSERT ... ON CONFLICT (node_id, ifname) DO UPDATE.

    The kernel hands out ppp interface names again as soon as they are free, so
    the natural key of a live session is reused constantly and two hooks can
    genuinely collide on it. An upsert is the only registration that is correct
    under that collision; a delete-then-insert is two statements with a gap in
    the middle, and the gap is where the 72 rejected sessions a day came from.

    Both dialects this runs on support ON CONFLICT, they just spell the
    construct differently — Postgres in production, SQLite under test.
    """
    values = dict(values, last_rx=values["base_rx"], last_tx=values["base_tx"])
    dialect = db.bind.dialect.name if db.bind is not None else "postgresql"
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as _insert
    else:
        from sqlalchemy.dialects.postgresql import insert as _insert

    stmt = _insert(SessionRow).values(**values)
    # started_at / gone_polls / stale_since come from the column defaults on a
    # fresh insert; on a conflict they must be reset explicitly, or the new
    # session would inherit the old one's start time and its gone-poll count.
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=[SessionRow.node_id, SessionRow.ifname],
            set_={
                "username": stmt.excluded.username,
                "peer_ip": stmt.excluded.peer_ip,
                "pid": stmt.excluded.pid,
                "proto": stmt.excluded.proto,
                "base_rx": stmt.excluded.base_rx,
                "base_tx": stmt.excluded.base_tx,
                "last_rx": stmt.excluded.last_rx,
                "last_tx": stmt.excluded.last_tx,
                "started_at": datetime.now(timezone.utc),
                "gone_polls": 0,
                "stale_since": None,
            },
        )
    )


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
    # Take over the interface name, finalizing whatever held it. Scoped to this
    # node: these hooks only ever run on the box that owns the interface, and a
    # remote node's identically-named ppp0 is a different session entirely.
    #
    # The kernel recycles ppp names, so a customer who drops and redials within
    # a second lands on the very interface number they just left. That used to
    # be a plain DELETE followed by an INSERT, which is not atomic: two hooks
    # arriving together each deleted what they could see and each inserted, and
    # the second lost to the (node_id, ifname) unique index — 72 rejected
    # session registrations a day, each one a session the panel did not know
    # about until the enforcer picked it up a cycle later.
    #
    # It also threw the displaced row away without billing it. If ip-down never
    # arrived for that session — which is exactly the case where its name gets
    # recycled this fast — its traffic was written off. Finalizing it here
    # closes that hole; _finalize claims the row before billing, so if ip-down
    # or the enforcer already handled it, this bills nothing.
    stale = (
        await db.execute(
            select(SessionRow).where(
                SessionRow.node_id == LOCAL_NODE_ID,
                SessionRow.ifname == payload.ifname,
            )
        )
    ).scalar_one_or_none()
    if stale is not None:
        prev_user = (
            await db.execute(select(User).where(User.username == stale.username))
        ).scalar_one_or_none()
        db.expunge(stale)
        await tasks.finalize_session(db, stale, prev_user, datetime.now(timezone.utc))
        log.info("iface %s reused by %s; finalized the previous session (%s)",
                 payload.ifname, payload.username, stale.username)
    # Classify from the address pool (shared helper) so a raw, no-IPsec session
    # is labelled L2TP-RAW in the ledger too, not just in the live view.
    proto = pppd.classify_proto(payload.peer_ip)
    # Anchor the billing baseline to the interface counter at registration so
    # this session's usage is measured from zero (ignores pre-registration bytes
    # and any counter the iface number carried from a prior session).
    base_rx, base_tx = (
        pppd.read_iface_bytes(payload.ifname) if pppd.iface_exists(payload.ifname) else (0, 0)
    )
    # Registered as an UPSERT on (node_id, ifname), not an INSERT.
    #
    # Finalizing the previous holder above is not enough on its own: two hooks
    # for the same recycled name can both get past it, and the loser of the
    # unique index would have its session rejected outright. Letting the
    # conflict resolve to "the later hook wins" is right — ip-up runs after the
    # interface exists, so the newest registration is the one describing the
    # session that is actually up.
    await _register_session(
        db,
        node_id=LOCAL_NODE_ID,
        username=payload.username,
        ifname=payload.ifname,
        peer_ip=payload.peer_ip,
        pid=payload.pid,
        proto=proto,
        base_rx=base_rx,
        base_tx=base_tx,
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
        await db.execute(
            select(SessionRow).where(
                SessionRow.node_id == LOCAL_NODE_ID,
                SessionRow.ifname == payload.ifname,
            )
        )
    ).scalar_one_or_none()
    user = (
        await db.execute(select(User).where(User.username == payload.username))
    ).scalar_one_or_none()

    # Primary finalize path (fast, on disconnect). The enforcer's debounced
    # iface-gone check is the fallback if this hook never arrives (crash). The
    # first to finalize deletes the row; the other matches nothing and skips, so
    # bytes are committed exactly once.
    #
    # CLAIM FIRST, THEN BILL — the delete's row count is what decides ownership,
    # not the SELECT above, which the enforcer can invalidate in the microseconds
    # between the two.
    claimed = False
    if row is not None:
        claimed = (
            await db.execute(delete(SessionRow).where(SessionRow.id == row.id))
        ).rowcount == 1
        if not claimed:
            log.debug("session %s/%s was finalized by the enforcer first",
                      payload.username, payload.ifname)

    if claimed and user is not None and row.username == payload.username:
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
            node_id=row.node_id,
            username=row.username,
            proto=row.proto,
            ifname=row.ifname,
            started_at=row.started_at,
            bytes_in=in_b,
            bytes_out=out_b,
            duration=duration,
        )
    elif user is not None:
        # Row already finalized (enforcer), or the username on it does not match
        # this hook -> nothing to bill, just touch the account.
        user.last_seen = now

    await db.commit()
    await outbound.reconcile(db)  # drop this client's WARP mapping on disconnect
    return {"detail": "recorded"}
