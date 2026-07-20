"""Background task: quota / expiry enforcement for the xl2tpd/pppd stack."""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

from sqlalchemy import delete, select

from . import accel, accounting, appsettings, backups, chap_secrets, livecache, outbound, pppd, telegram, wireguard
from .config import settings
from .database import AsyncSessionLocal
from .models import Session as SessionRow
from .models import UsageSample, User, WgPeer

log = logging.getLogger("vpn-panel.tasks")


def _duration(started, now) -> int:
    if started is None:
        return 0
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return max(0, int((now - started).total_seconds()))


async def _finalize(db, row: SessionRow, user: User | None, now: datetime) -> None:
    """Close a session: commit its bytes to the user (once) and write the ledger
    row, then drop the open-session row. Bytes come from the last authoritative
    sysfs counter relative to the billing base -> no counter mixing."""
    in_b, out_b = pppd.session_usage(row.last_rx, row.last_tx, row.base_rx, row.base_tx)
    if user is not None:
        user.used_bytes += in_b + out_b
        user.last_seen = now
    await accounting.record_session(
        db,
        username=row.username,
        proto=row.proto,
        ifname=row.ifname,
        started_at=row.started_at,
        bytes_in=in_b,
        bytes_out=out_b,
        duration=_duration(row.started_at, now),
    )
    await db.delete(row)


async def _enforce_once() -> None:
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(SessionRow))).scalars().all()
        rows_by_iface = {r.ifname: r for r in rows}
        active_rows_by_user: dict[str, list[SessionRow]] = {}

        live_ifaces = pppd.list_ppp_ifaces()
        live_set = set(live_ifaces)

        # --- 1a) Reconcile ORPHANS: live ifaces with no session row (e.g. ones
        # that came up during a panel-down window). Name SSTP orphans via
        # accel-cmd and register them with base 0 so their FULL counter gets
        # accounted -> recovers the traffic the old design silently lost
        # ("user used more than shown"). Unnameable L2TP orphans are still
        # sampled + flagged (username='') so nothing ever vanishes silently.
        orphans = [i for i in live_ifaces if i not in rows_by_iface]
        smap = await accel.session_map() if orphans else {}
        for ifn in orphans:
            rx, tx = pppd.read_iface_bytes(ifn)
            uname = smap.get(ifn, "")
            if uname:
                newrow = SessionRow(
                    username=uname, ifname=ifn, peer_ip="", pid=0,
                    proto="SSTP", base_rx=0, base_tx=0, last_rx=rx, last_tx=tx,
                )
                db.add(newrow)
                rows_by_iface[ifn] = newrow
                log.warning("recovered orphan iface %s -> %s (now accounted from full counter)", ifn, uname)
            else:
                db.add(UsageSample(ts=now, ifname=ifn, username="", proto="", rx_bytes=rx, tx_bytes=tx))
                log.warning("orphan iface %s unmapped (rx=%d tx=%d) -> flagged in usage_samples", ifn, rx, tx)

        # --- 1b) Track live counters, emit the usage_samples time series, and
        # finalize sessions whose iface has been gone for >=2 polls (debounced).
        # used_bytes is NOT advanced per poll; a live session's usage is the
        # overlay (counter - base), committed to used_bytes exactly once at
        # finalize -> self-healing, never drifts.
        for row in list(rows_by_iface.values()):
            if row.ifname in live_set:
                rx, tx = pppd.read_iface_bytes(row.ifname)
                row.last_rx, row.last_tx = rx, tx
                row.gone_polls = 0
                active_rows_by_user.setdefault(row.username, []).append(row)
                db.add(UsageSample(
                    ts=now, ifname=row.ifname, username=row.username,
                    proto=row.proto, rx_bytes=rx, tx_bytes=tx,
                ))
            else:
                row.gone_polls = (row.gone_polls or 0) + 1
                if row.gone_polls >= 2:
                    user = (
                        await db.execute(select(User).where(User.username == row.username))
                    ).scalar_one_or_none()
                    await _finalize(db, row, user, now)

        # --- 1.5) WireGuard accounting --------------------------------------
        # Peers are perpetual (no connect/disconnect), so WG bytes flow
        # CONTINUOUSLY into used_bytes (usage_delta guards counter resets on peer
        # re-add / iface restart). effective = used_bytes + ppp overlay then
        # already includes WG with no double-count.
        wg_peers = (await db.execute(select(WgPeer))).scalars().all()
        wg_dump = await wireguard.show_dump() if (wg_peers and wireguard.iface_up()) else {}
        wg_users: dict[int, User] = {}
        if wg_peers:
            wg_users = {
                u.id: u for u in (await db.execute(
                    select(User).where(User.id.in_([p.user_id for p in wg_peers]))
                )).scalars().all()
            }
        for peer in wg_peers:
            d = wg_dump.get(peer.public_key)
            if not d:
                continue
            delta = pppd.usage_delta(d["rx"], peer.last_rx) + pppd.usage_delta(d["tx"], peer.last_tx)
            u = wg_users.get(peer.user_id)
            if u and delta > 0:
                u.used_bytes += delta
            peer.last_rx, peer.last_tx = d["rx"], d["tx"]
            peer.last_handshake = (
                datetime.fromtimestamp(d["handshake"], timezone.utc) if d["handshake"] > 0 else None
            )
            db.add(UsageSample(
                ts=now, ifname=f"wg:{peer.address}"[:32], username=(u.username if u else ""),
                proto="wg", rx_bytes=d["rx"], tx_bytes=d["tx"],
            ))

        await db.commit()

        # --- 2) Enforce quota / expiry / disable on EFFECTIVE usage ------------
        users_by_name = {u.username: u for u in (await db.execute(select(User))).scalars().all()}
        secrets_dirty = False

        # 2a) L2TP/SSTP (ppp) sessions
        for username, urows in active_rows_by_user.items():
            user = users_by_name.get(username)
            if user is None:
                continue
            live = 0
            for r in urows:
                i, o = pppd.session_usage(r.last_rx, r.last_tx, r.base_rx, r.base_tx)
                live += i + o
            effective = user.used_bytes + live
            over_quota = user.quota_bytes > 0 and effective >= user.quota_bytes
            disabled = (not user.is_active) or user.is_expired
            if over_quota or disabled:
                # Terminate via BOTH engines: kill_pid for xl2tpd/pppd L2TP
                # (real PID) AND `accel-cmd terminate` for accel-ppp SSTP
                # (pid=0). Then finalize so the bytes are committed and counted.
                await pppd.terminate_user(db, username)
                for r in urows:
                    await _finalize(db, r, user, now)
                secrets_dirty = True
                why = "quota" if over_quota else f"active={user.is_active} expired={user.is_expired}"
                log.info("terminated %s: %s (effective=%d quota=%d)",
                         username, why, effective, user.quota_bytes)
                continue

            # Safety net for l2tp_mode. ip-up drops a wrong-endpoint session in
            # well under a second, but if that hook ever fails (panel restarting,
            # curl timeout, pid file not written yet) the session would otherwise
            # live on — re-checking here bounds that to one poll interval.
            # Only the OFFENDING interface is dropped, never the whole account:
            # the same user's SSTP/WireGuard sessions are perfectly legitimate,
            # so terminate_user() would be far too blunt here.
            for r in urows:
                reason = pppd.mode_conflict(user.l2tp_mode, r.peer_ip)
                if not reason:
                    continue
                pppd.kill_pid(r.pid if r.pid and r.pid > 0 else pppd.pid_from_ifname(r.ifname))
                await _finalize(db, r, user, now)
                log.warning("dropped %s on %s (%s): %s", username, r.ifname, r.peer_ip, reason)

        # 2b) WireGuard peers: kick over-quota/expired/disabled, re-add healthy
        for peer in wg_peers:
            user = wg_users.get(peer.user_id)
            if user is None:
                continue
            ppp_live = sum(
                sum(pppd.session_usage(r.last_rx, r.last_tx, r.base_rx, r.base_tx))
                for r in active_rows_by_user.get(user.username, [])
            )
            effective = user.used_bytes + ppp_live
            blocked = ((user.quota_bytes > 0 and effective >= user.quota_bytes)
                       or (not user.is_active) or user.is_expired)
            on_iface = peer.public_key in wg_dump
            if blocked and on_iface:
                await wireguard.remove_peer(peer.public_key)
                log.info("wg kicked %s (effective=%d quota=%d)", user.username, effective, user.quota_bytes)
            elif (not blocked) and (not on_iface) and wireguard.iface_up():
                await wireguard.add_peer(peer.public_key, peer.preshared_key, peer.address)
                log.info("wg re-added %s", user.username)

        await db.commit()

        if secrets_dirty:
            await chap_secrets.rewrite(db)

        # self-healing: converge the WARP outbound ipset to the live truth
        await outbound.reconcile(db)


async def quota_enforcer() -> None:
    interval = max(5, settings.quota_poll_seconds)
    log.info("quota_enforcer started (interval=%ss)", interval)
    while True:
        try:
            await _enforce_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("quota_enforcer cycle failed")
        await asyncio.sleep(interval)


# Dedicated WG throughput cache — pppd._rate_cache is pruned by list_sessions
# every call (it only keeps live ppp ifaces), which would wipe WG keys and zero
# their rate. Keep WG rates here instead.
_wg_rate_cache: dict[str, tuple[float, int, int]] = {}


def _wg_rate(key: str, rx: int, tx: int) -> tuple[int, int]:
    now = time.monotonic()
    prev = _wg_rate_cache.get(key)
    _wg_rate_cache[key] = (now, rx, tx)
    if not prev:
        return 0, 0
    pt, prx, ptx = prev
    dt = now - pt
    if dt <= 0 or rx < prx or tx < ptx:
        return 0, 0
    return max(0, int((rx - prx) * 8 / dt)), max(0, int((tx - ptx) * 8 / dt))


async def _snapshot_once() -> None:
    """Refresh the shared live snapshot. This is the SINGLE place that reads
    per-interface sysfs / `wg show` for display, so API requests never do."""
    from .schemas import SessionOut

    async with AsyncSessionLocal() as db:
        sessions = await pppd.list_sessions(db)
        # WireGuard "sessions" = peers with a recent handshake (online).
        wg_peers = (await db.execute(select(WgPeer))).scalars().all()
        if wg_peers and wireguard.iface_up():
            dump = await wireguard.show_dump()
            names = dict((await db.execute(select(User.id, User.username))).all())
            now_e = time.time()
            live_keys = set()
            for peer in wg_peers:
                d = dump.get(peer.public_key)
                if not d or d["handshake"] <= 0 or (now_e - d["handshake"]) >= 180:
                    continue
                ifn = f"wg:{peer.address}"
                live_keys.add(ifn)
                rxr, txr = _wg_rate(ifn, d["rx"], d["tx"])
                sessions.append(SessionOut(
                    username=names.get(peer.user_id, ""), ifname=ifn, ip=peer.address,
                    protocol="WireGuard", uptime_seconds=max(0, int(now_e - d["handshake"])),
                    rx_bytes=d["rx"], tx_bytes=d["tx"], rx_rate_bps=rxr, tx_rate_bps=txr, state="active",
                ))
            for k in list(_wg_rate_cache.keys()):
                if k not in live_keys:
                    _wg_rate_cache.pop(k, None)
    rx = sum(s.rx_rate_bps for s in sessions)
    tx = sum(s.tx_rate_bps for s in sessions)
    livecache.update(sessions, rx, tx)


async def snapshot_sampler() -> None:
    interval = 10
    log.info("snapshot_sampler started (interval=%ss)", interval)
    while True:
        try:
            await _snapshot_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("snapshot_sampler cycle failed")
        await asyncio.sleep(interval)


async def _sample_once() -> None:
    """Record an aggregate traffic sample for dashboard charts (from the live
    snapshot — no extra sysfs scan)."""
    import datetime as dt

    from .models import TrafficSample

    snap = livecache.snapshot()
    async with AsyncSessionLocal() as db:
        db.add(TrafficSample(
            online_count=len(snap["online"]),
            rx_bps=snap["rx_rate_bps"],
            tx_bps=snap["tx_rate_bps"],
        ))
        # Retain ~24h of samples
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)
        await db.execute(delete(TrafficSample).where(TrafficSample.ts < cutoff))
        await db.commit()


async def traffic_sampler() -> None:
    interval = 15
    log.info("traffic_sampler started (interval=%ss)", interval)
    while True:
        try:
            await _sample_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("traffic_sampler cycle failed")
        await asyncio.sleep(interval)


async def _telegram_deliver(info: dict) -> None:
    """Deliver the daily backup off-site to Telegram if enabled + configured. If
    the full dump exceeds Telegram's ~50MB cap, fall back to an essential dump
    (everything except the huge, reconstructible usage_samples row data) so the
    off-site copy never silently stops."""
    async with AsyncSessionLocal() as db:
        aps = await appsettings.get_all(db)
    if aps.get("tg_backup_enabled") != "1" or not aps.get("tg_bot_token"):
        return
    token = aps["tg_bot_token"]
    chat = aps.get("tg_chat_id") or await telegram.resolve_chat_id(token)
    if not chat:
        log.warning("telegram backup: no chat_id (operator must /start the bot)")
        return

    tmp = None
    if info["size"] <= telegram.TG_DOC_LIMIT:
        path = backups.safe_path(info["name"])
        caption = f"🔐 Daily VPN panel backup\n{info['name']}"
    else:
        tmp = await backups.create_essential_dump()
        path = tmp
        caption = (f"🔐 Daily VPN panel backup — essential (excludes usage_samples)\n"
                   f"{os.path.basename(tmp)}")
        log.info("telegram backup: full dump %d bytes > cap, sending essential copy", info["size"])
    try:
        if not path:
            return
        ok, _out = await telegram.send_document(token, chat, path, caption=caption)
        log.info("telegram backup delivery: %s", "ok" if ok else "FAILED")
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


async def backup_scheduler() -> None:
    """Take a daily pg_dump (checks every 30 min; runs if the newest backup is
    older than 24h). pg_dump only reads -> never disrupts the live panel."""
    interval = 1800
    log.info("backup_scheduler started")
    while True:
        try:
            if backups.newest_age_hours() >= 24:
                info = await backups.create_backup()
                log.info("scheduled backup: %s (%d bytes)", info["name"], info["size"])
                await _telegram_deliver(info)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("backup_scheduler cycle failed")
        await asyncio.sleep(interval)
