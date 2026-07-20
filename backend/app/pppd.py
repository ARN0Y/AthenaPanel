"""Interface to the xl2tpd / pppd stack.

Live sessions come from the `sessions` table (populated by ip-up.d / ip-down.d).
Byte counters are read from /sys/class/net/<ifname>/statistics. Live throughput
(bits/s) is derived from the delta between consecutive polls using an in-memory
sample cache, so the UI can show real-time rates and react quickly to changes.
"""

import datetime as _dt
import os
import signal
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DBSession

from . import accel
from .config import settings
from .models import Session as SessionRow
from .schemas import SessionOut

# ifname -> (timestamp, rx_bytes, tx_bytes)
_rate_cache: dict[str, tuple[float, int, int]] = {}


def iface_exists(ifname: str) -> bool:
    return bool(ifname) and os.path.isdir(f"/sys/class/net/{ifname}")


def list_ppp_ifaces() -> list[str]:
    """Every live ppp* interface on the host (the ground truth for accounting —
    used to discover sessions that were never registered, e.g. ones that came up
    during a panel-down window, so their traffic is never silently lost)."""
    try:
        return sorted(n for n in os.listdir("/sys/class/net") if n.startswith("ppp"))
    except OSError:
        return []


def read_iface_bytes(ifname: str) -> tuple[int, int]:
    """(rx, tx): rx = from client (upload), tx = to client (download)."""
    base = f"/sys/class/net/{ifname}/statistics"
    try:
        with open(f"{base}/rx_bytes") as fh:
            rx = int(fh.read().strip() or 0)
        with open(f"{base}/tx_bytes") as fh:
            tx = int(fh.read().strip() or 0)
        return rx, tx
    except (OSError, ValueError):
        return 0, 0


def _compute_rate(ifname: str, rx: int, tx: int) -> tuple[int, int]:
    """Return (rx_bps, tx_bps) in bits/s based on the previous sample."""
    now = time.monotonic()
    prev = _rate_cache.get(ifname)
    _rate_cache[ifname] = (now, rx, tx)
    if not prev:
        return 0, 0
    pt, prx, ptx = prev
    dt = now - pt
    if dt <= 0:
        return 0, 0
    rx_bps = max(0, int((rx - prx) * 8 / dt))
    tx_bps = max(0, int((tx - ptx) * 8 / dt))
    # Guard against counter resets
    if rx < prx or tx < ptx:
        return 0, 0
    return rx_bps, tx_bps


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def kill_pid(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


def usage_delta(current: int, last_committed: int) -> int:
    """Billed bytes since the last commit, guarding against counter resets, with
    the configured accounting multiplier applied.

    This is the SINGLE choke point every billed byte passes through exactly once
    — session_usage() (L2TP/SSTP display + finalize + enforcement), the ip-down
    finalize, and the WireGuard delta all route through here — so
    `settings.usage_multiplier` (1.0 = exact) stays perfectly consistent across
    the displayed usage, quota enforcement and the accounting ledger. Live
    throughput rates are computed separately (_compute_rate) and are NOT scaled.

    Interface byte counters only ever increase; if `current` < `last_committed`
    the counter (or interface) was reset, so the new traffic == current.
    """
    raw = (current - last_committed) if current >= last_committed else max(0, current)
    return int(raw * settings.usage_multiplier)


def session_usage(rx: int, tx: int, base_rx: int, base_tx: int) -> tuple[int, int]:
    """This session's billing bytes (in, out): the counter since the billing
    base. base is 0 for a fresh session and bumped to the live counter on a
    quota reset, so the overlay restarts from zero without losing the counter."""
    return usage_delta(rx, base_rx), usage_delta(tx, base_tx)


def l2tp_engine(peer_ip: str) -> str:
    """Which L2TP engine served this client: "ipsec", "raw", or "" (neither).

    "" covers SSTP, WireGuard and — deliberately — any address we don't
    recognise. Mode enforcement is gated on this, so an unknown pool can never
    cause a session to be refused on a guess.
    """
    peer = peer_ip or ""
    if peer.startswith(settings.l2tp_raw_subnet or "192.168.45."):
        return "raw"
    if peer.startswith(settings.l2tp_subnet or "192.168.42."):
        return "ipsec"
    return ""


def classify_proto(peer_ip: str, stored: str = "") -> str:
    """Protocol label for a session, derived from the client's address pool.

    The pool is AUTHORITATIVE: every engine hands out from its own range
    (xl2tpd 192.168.42.x, accel-ppp SSTP 192.168.44.x, the raw no-IPsec xl2tpd
    192.168.45.x), so the peer IP identifies the engine even when the stored
    label is stale or predates the raw instance. `stored` is only the fallback
    for sessions with no peer IP (e.g. an SSTP orphan recovered via accel-cmd).

    This is the SINGLE place the pool prefixes are interpreted — session_up
    persists this label and list_sessions displays it, so the live table, the
    accounting ledger and the events view can never disagree.
    """
    engine = l2tp_engine(peer_ip)
    if engine == "raw":
        return "L2TP-RAW"
    if engine == "ipsec":
        return "L2TP"
    if (peer_ip or "").startswith(settings.sstp_subnet or "192.168.44."):
        return "SSTP"
    return stored or "L2TP"


def mode_conflict(l2tp_mode: str, peer_ip: str) -> str:
    """Why this session contradicts the account's L2TP mode ("" = it doesn't).

    Both xl2tpd instances authenticate from the SAME /etc/ppp/chap-secrets, so
    credentials alone cannot keep an IPsec account off the raw (unencrypted)
    endpoint — this check is what makes `users.l2tp_mode` an actual control
    rather than a label. Only the two L2TP engines are gated: SSTP and
    WireGuard have no IPsec relationship, so they are always allowed.

    Fails OPEN by design: disabled via settings, an unrecognised pool, or a
    missing user all return "" — a misconfiguration must never mass-disconnect.
    """
    if not settings.l2tp_enforce_mode:
        return ""
    engine = l2tp_engine(peer_ip)
    if not engine:
        return ""
    want = "raw" if (l2tp_mode or "ipsec").strip().lower() == "raw" else "ipsec"
    if engine == want:
        return ""
    if engine == "raw":
        return "account is L2TP/IPsec but connected to the raw (no-IPsec) endpoint"
    return "account is L2TP raw but connected to the L2TP/IPsec endpoint"


def pid_from_ifname(ifname: str) -> int:
    """Resolve the pppd PID from /var/run/<ifname>.pid.

    Session rows sometimes store pid=0 (the pid file wasn't ready when ip-up
    ran), which made disconnect/quota-kill unable to terminate them. Reading the
    pid file at terminate time recovers the real pid.
    """
    if not ifname:
        return 0
    try:
        with open(f"/var/run/{ifname}.pid") as fh:
            return int(fh.read().strip() or 0)
    except (OSError, ValueError):
        return 0


async def list_sessions(db: DBSession) -> list[SessionOut]:
    rows = (await db.execute(select(SessionRow))).scalars().all()
    now = _dt.datetime.now(_dt.timezone.utc)
    live_ifaces = set()
    out: list[SessionOut] = []
    for r in rows:
        if not iface_exists(r.ifname):
            continue
        live_ifaces.add(r.ifname)
        rx, tx = read_iface_bytes(r.ifname)
        rx_bps, tx_bps = _compute_rate(r.ifname, rx, tx)
        # Billing-consistent session bytes (counter since the billing base), so
        # a session can never display more than what is credited to its user.
        in_bytes, out_bytes = session_usage(rx, tx, r.base_rx, r.base_tx)
        started = r.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=_dt.timezone.utc)
        uptime = max(0, int((now - started).total_seconds()))
        protocol = classify_proto(r.peer_ip, r.proto)
        out.append(
            SessionOut(
                username=r.username,
                ifname=r.ifname,
                ip=r.peer_ip,
                protocol=protocol,
                uptime_seconds=uptime,
                rx_bytes=in_bytes,
                tx_bytes=out_bytes,
                rx_rate_bps=rx_bps,
                tx_rate_bps=tx_bps,
                state="active",
            )
        )
    # Drop cache entries for interfaces that are gone
    for ifname in list(_rate_cache.keys()):
        if ifname not in live_ifaces:
            _rate_cache.pop(ifname, None)
    return out


async def online_usernames(db: DBSession) -> set[str]:
    rows = (await db.execute(select(SessionRow))).scalars().all()
    return {r.username for r in rows if iface_exists(r.ifname)}


async def terminate_user(db: DBSession, username: str) -> bool:
    """Disconnect a user.

    accel-ppp has no per-session pppd PID, so the primary path is
    `accel-cmd terminate username <user>`. We also SIGTERM any recorded PID as
    a fallback for the xl2tpd/pppd engine.
    """
    rows = (
        await db.execute(select(SessionRow).where(SessionRow.username == username))
    ).scalars().all()
    for r in rows:
        pid = r.pid if r.pid and r.pid > 0 else pid_from_ifname(r.ifname)
        kill_pid(pid)
    await accel.terminate(username)
    return True
