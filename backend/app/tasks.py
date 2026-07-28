"""Background task: quota / expiry enforcement for the xl2tpd/pppd stack."""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

from sqlalchemy import delete, select

from . import (
    accel, accounting, appsettings, backups, chap_secrets, livecache, nodes,
    outbound, pppd, telegram, wireguard,
)
from .config import settings
from .database import AsyncSessionLocal
from .models import LOCAL_NODE_ID, Node, Session as SessionRow
from .models import UsageSample, User, WgPeer

log = logging.getLogger("vpn-panel.tasks")

# Previous cycle's absolute counters for node 1, held in memory rather than the
# database: it is a watermark for a delta, not state worth persisting, and the
# enforcer is the only reader. A restart just costs one interval of accounting
# on the node's capacity total (never on billing, which has its own path).
_LOCAL_COUNTERS: dict[str, tuple[int, int]] = {}

# A WireGuard peer counts as online while its last handshake is this recent.
# WireGuard rekeys roughly every 2 minutes, so the window must comfortably
# exceed that or a healthy peer would flap offline between rekeys.
WG_ONLINE_WINDOW = 180


def _aware(ts: datetime | None) -> datetime | None:
    """Treat a naive timestamp as UTC (SQLite round-trips lose the tzinfo)."""
    if ts is not None and ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


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
        node_id=row.node_id,
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
        await nodes.ensure_local(db)

        # Local interfaces are only the truth for LOCAL sessions; a remote
        # node's ppp0 is a different interface that happens to share a name.
        local_rows = (
            await db.execute(select(SessionRow).where(SessionRow.node_id == LOCAL_NODE_ID))
        ).scalars().all()
        rows_by_iface = {r.ifname: r for r in local_rows}
        active_rows_by_user: dict[str, list[SessionRow]] = {}

        scan_ok, live_ifaces = pppd.scan_ppp_ifaces()
        live_set = set(live_ifaces)
        # A failed scan is not "everybody disconnected" — see nodes.py.
        authoritative = await nodes.authoritative_ids(db, now, scan_ok)
        if not scan_ok:
            log.error("ppp interface scan failed; holding all local sessions this cycle")

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
                    node_id=LOCAL_NODE_ID,
                    username=uname, ifname=ifn, peer_ip="", pid=0,
                    proto="SSTP", base_rx=0, base_tx=0, last_rx=rx, last_tx=tx,
                )
                db.add(newrow)
                rows_by_iface[ifn] = newrow
                log.warning("recovered orphan iface %s -> %s (now accounted from full counter)", ifn, uname)
            else:
                db.add(UsageSample(ts=now, node_id=LOCAL_NODE_ID, ifname=ifn,
                                   username="", proto="", rx_bytes=rx, tx_bytes=tx))
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
                row.stale_since = None  # the node is talking about it again
                active_rows_by_user.setdefault(row.username, []).append(row)
                db.add(UsageSample(
                    ts=now, node_id=row.node_id, ifname=row.ifname, username=row.username,
                    proto=row.proto, rx_bytes=rx, tx_bytes=tx,
                ))
            elif row.node_id in authoritative:
                # The owning node is reporting and does not list this interface,
                # so it really is gone. Debounce a single missed read, then close.
                row.gone_polls = (row.gone_polls or 0) + 1
                if row.gone_polls >= 2:
                    user = (
                        await db.execute(select(User).where(User.username == row.username))
                    ).scalar_one_or_none()
                    await _finalize(db, row, user, now)
            elif row.stale_since is None:
                # The node went quiet. Hold the row untouched: its bytes stay
                # uncommitted and its base is preserved, so when the node comes
                # back the session simply resumes instead of being billed twice.
                row.stale_since = now
                log.warning("node %d silent; holding session %s/%s (not finalizing)",
                            row.node_id, row.username, row.ifname)

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

            # Detect the offline -> online edge and anchor a "session" to it.
            # The base is taken from last_rx/last_tx, NOT the counter we just
            # read: those are the values from the previous cycle, when the peer
            # was still offline and therefore not moving bytes. Using them keeps
            # the traffic from this cycle inside the session instead of
            # discarding up to one poll interval of it.
            hs = _aware(
                datetime.fromtimestamp(d["handshake"], timezone.utc) if d["handshake"] > 0 else None
            )
            prev_hs = _aware(peer.last_handshake)
            is_online = hs is not None and (now - hs).total_seconds() < WG_ONLINE_WINDOW
            was_online = prev_hs is not None and (now - prev_hs).total_seconds() < WG_ONLINE_WINDOW
            # `peer.online_since is None` also catches a peer that was already
            # connected when this code first ran (or when it was deployed):
            # such a peer never produces an offline->online edge, so without
            # adopting it here it would stay unanchored forever and keep
            # displaying its lifetime byte total. Its start time is then only
            # known to be "at least since the last handshake".
            if is_online and (not was_online or peer.online_since is None):
                peer.online_since = hs or now
                peer.session_base_rx, peer.session_base_tx = peer.last_rx, peer.last_tx
            elif not is_online and peer.online_since is not None:
                peer.online_since = None

            peer.last_rx, peer.last_tx = d["rx"], d["tx"]
            peer.last_handshake = hs
            db.add(UsageSample(
                ts=now, node_id=LOCAL_NODE_ID, ifname=f"wg:{peer.address}"[:32],
                username=(u.username if u else ""),
                proto="wg", rx_bytes=d["rx"], tx_bytes=d["tx"],
            ))

        # --- 1.6) Node 1's own traffic totals --------------------------------
        # The same accumulation the hub does for a remote node, applied to this
        # server's own interfaces. Both paths go through nodes.accumulate_traffic
        # so "traffic through this node" means the same thing on the Nodes page
        # whether or not an agent is involved.
        local_counters: dict[str, tuple[int, int]] = {
            f"ppp:{row.ifname}": (row.last_rx, row.last_tx)
            for rows in active_rows_by_user.values() for row in rows
        }
        local_counters.update({
            f"wg:{peer.public_key}": (peer.last_rx, peer.last_tx)
            for peer in wg_peers if wg_dump.get(peer.public_key)
        })
        local_node = await db.get(Node, LOCAL_NODE_ID)
        if local_node is not None:
            nodes.accumulate_traffic(local_node, local_counters, _LOCAL_COUNTERS, now)
            _LOCAL_COUNTERS.clear()
            _LOCAL_COUNTERS.update(local_counters)

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


# (node_id, ifname) -> (monotonic_at, rx, tx, rx_bps, tx_bps)
_remote_rate_cache: dict[tuple[int, str], tuple[float, int, int, int, int]] = {}

# A remote sample this old is not a rate any more. Comfortably above the agent's
# 15s report interval so an ordinary late report does not blank the column.
REMOTE_RATE_MAX_AGE = 45.0


def _remote_rate(key: tuple[int, str], rx: int, tx: int) -> tuple[int, int]:
    """Throughput for a session whose counters only arrive every ~15s.

    The snapshot refreshes faster than reports come in, so most calls see the
    SAME counters as the previous one. Returning zero for those would make every
    remote session's rate flicker between its real value and nothing, which
    reads as an unstable link rather than what it is — a slower sample rate. The
    last computed rate is held until a genuinely new sample arrives, and expires
    if one stops arriving at all.
    """
    now = time.monotonic()
    prev = _remote_rate_cache.get(key)
    if prev is None:
        _remote_rate_cache[key] = (now, rx, tx, 0, 0)
        return 0, 0
    at, prx, ptx, rx_bps, tx_bps = prev
    if rx == prx and tx == ptx:
        if now - at > REMOTE_RATE_MAX_AGE:
            _remote_rate_cache[key] = (at, rx, tx, 0, 0)
            return 0, 0
        return rx_bps, tx_bps
    dt = now - at
    if dt <= 0 or rx < prx or tx < ptx:
        # Counter restarted (the interface was reused): no rate can be derived
        # across the discontinuity, so start a fresh baseline instead of
        # reporting the whole counter as one interval's traffic.
        _remote_rate_cache[key] = (now, rx, tx, 0, 0)
        return 0, 0
    rx_bps = max(0, int((rx - prx) * 8 / dt))
    tx_bps = max(0, int((tx - ptx) * 8 / dt))
    _remote_rate_cache[key] = (now, rx, tx, rx_bps, tx_bps)
    return rx_bps, tx_bps


async def _remote_sessions(db, now: datetime) -> list:
    """Live sessions on other nodes, read from the rows the hub mirrors.

    Only nodes that are currently reporting contribute. A silent node's rows are
    deliberately kept in the table (they are held, not closed — see
    nodesessions), but showing them as live would claim a session is up when
    nobody can currently say so. They reappear, unchanged, when the node does.
    """
    from .schemas import SessionOut

    remote_nodes = {
        n.id: n
        for n in (
            await db.execute(select(Node).where(Node.is_local.is_(False)))
        ).scalars().all()
    }
    if not remote_nodes:
        _remote_rate_cache.clear()
        return []

    fresh: set[int] = set()
    for nid, node in remote_nodes.items():
        seen = _aware(node.last_seen_at)
        if seen is not None and (now - seen).total_seconds() < nodes.STALE_AFTER_SECONDS:
            fresh.add(nid)

    rows = (
        await db.execute(select(SessionRow).where(SessionRow.node_id != LOCAL_NODE_ID))
    ).scalars().all()

    out: list[SessionOut] = []
    live_keys: set[tuple[int, str]] = set()
    for row in rows:
        if row.node_id not in fresh:
            continue
        key = (row.node_id, row.ifname)
        live_keys.add(key)
        rx_bps, tx_bps = _remote_rate(key, row.last_rx, row.last_tx)
        # Same measurement as a local session: the counter since the billing
        # base, multiplier applied. A row cannot display more than the credit
        # loop has billed for it.
        in_b, out_b = pppd.session_usage(row.last_rx, row.last_tx, row.base_rx, row.base_tx)
        out.append(SessionOut(
            username=row.username,
            ifname=row.ifname,
            ip=row.peer_ip,
            node_id=row.node_id,
            node_name=remote_nodes[row.node_id].name,
            protocol=pppd.classify_proto(row.peer_ip, row.proto),
            uptime_seconds=_duration(row.started_at, now),
            rx_bytes=in_b,
            tx_bytes=out_b,
            rx_rate_bps=rx_bps,
            tx_rate_bps=tx_bps,
            state="active",
        ))
    for key in list(_remote_rate_cache.keys()):
        if key not in live_keys:
            _remote_rate_cache.pop(key, None)
    return out


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
            now_dt = datetime.now(timezone.utc)
            for peer in wg_peers:
                d = dump.get(peer.public_key)
                if not d or d["handshake"] <= 0 or (now_e - d["handshake"]) >= WG_ONLINE_WINDOW:
                    continue
                ifn = f"wg:{peer.address}"
                live_keys.add(ifn)
                rxr, txr = _wg_rate(ifn, d["rx"], d["tx"])
                # How long this peer has been online, NOT how long since its
                # last rekey — WireGuard re-handshakes every ~2 minutes, so the
                # handshake age is a sawtooth that never exceeds ~120s no matter
                # how long the user has actually been connected.
                started = _aware(peer.online_since)
                if started is not None:
                    uptime = max(0, int((now_dt - started).total_seconds()))
                else:
                    # The enforcer has not observed the edge yet (it runs every
                    # 30s). Fall back to handshake age rather than showing zero.
                    uptime = max(0, int(now_e - d["handshake"]))
                # Bytes for THIS online period, measured the same way a ppp
                # session is (counter minus its base, multiplier applied), so
                # the column means the same thing for every protocol instead of
                # showing WireGuard's lifetime total next to per-session values.
                in_b, out_b = pppd.session_usage(
                    d["rx"], d["tx"], peer.session_base_rx, peer.session_base_tx
                )
                sessions.append(SessionOut(
                    username=names.get(peer.user_id, ""), ifname=ifn, ip=peer.address,
                    protocol="WireGuard", uptime_seconds=uptime,
                    rx_bytes=in_b, tx_bytes=out_b, rx_rate_bps=rxr, tx_rate_bps=txr, state="active",
                ))
            for k in list(_wg_rate_cache.keys()):
                if k not in live_keys:
                    _wg_rate_cache.pop(k, None)

        # Name the local sessions after the local node so the Sessions page can
        # label every row from one field, instead of treating a blank as "here".
        local_node = await db.get(Node, LOCAL_NODE_ID)
        local_name = local_node.name if local_node is not None else "local"
        for s in sessions:
            s.node_name = local_name
        sessions.extend(await _remote_sessions(db, datetime.now(timezone.utc)))

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
