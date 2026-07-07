"""Per-user outbound routing (direct vs WARP).

A user's traffic egresses through a named "outbound". `direct` is the node's own
egress (default, nothing to do). `warp` policy-routes the user's client IP through
the Cloudflare WARP interface — the OS plumbing (WireGuard `warp` iface, routing
table 200, fwmark, masquerade, health-check fallback) is installed out-of-band by
`setup-warp.sh`. The panel only owns the **`warp_users` ipset**: it reconciles it
to contain exactly the client IPs of currently-online users whose outbound=='warp'.

Reconcile is idempotent and self-healing — it's called on connect/disconnect, on
user edit, and every enforcer cycle, so the ipset always converges to the truth.
Direct users are never touched, and if the WARP plumbing is absent the reconcile
is a safe no-op.
"""

import asyncio
import logging
import time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Session as SessionRow
from .models import User, WgPeer

log = logging.getLogger("vpn-panel.outbound")

WARP = "warp"
DIRECT = "direct"
VALID = {DIRECT, WARP}
_IPSET = "warp_users"


def normalize(value: str | None) -> str:
    v = (value or "").strip().lower()
    return v if v in VALID else DIRECT


def _ip(addr: str) -> str:
    return (addr or "").split("/")[0].strip()


async def _run(*args: str) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
    except FileNotFoundError:
        return 127, "ipset not installed"
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace")


async def desired_warp_ips(db: AsyncSession) -> set[str]:
    """Client IPs that should currently egress via WARP: online L2TP/SSTP sessions
    and enabled WireGuard peers belonging to users whose outbound == 'warp'."""
    ips: set[str] = set()
    rows = (
        await db.execute(
            select(SessionRow.peer_ip)
            .join(User, User.username == SessionRow.username)
            .where(User.outbound == WARP, SessionRow.peer_ip != "")
        )
    ).all()
    ips.update(_ip(r[0]) for r in rows)
    rows = (
        await db.execute(
            select(WgPeer.address)
            .join(User, User.id == WgPeer.user_id)
            .where(User.outbound == WARP, WgPeer.enabled.is_(True), WgPeer.address != "")
        )
    ).all()
    ips.update(_ip(r[0]) for r in rows)
    return {ip for ip in ips if ip}


async def _current_members() -> set[str] | None:
    rc, out = await _run("ipset", "list", _IPSET)
    if rc != 0:
        return None  # ipset missing or WARP not set up -> reconcile is a no-op
    members: set[str] = set()
    seen_header = False
    for line in out.splitlines():
        if line.startswith("Members:"):
            seen_header = True
            continue
        if seen_header and line.strip():
            members.add(line.split()[0])
    return members


async def reconcile(db: AsyncSession) -> None:
    """Converge the warp_users ipset to the desired set. Safe no-op if WARP plumbing
    is absent. Never raises into the caller."""
    try:
        desired = await desired_warp_ips(db)
        current = await _current_members()
        if current is None:
            if desired:
                log.debug("WARP ipset absent; %d user-ip(s) pending until plumbing is up", len(desired))
            return
        for ip in desired - current:
            await _run("ipset", "add", _IPSET, ip, "-exist")
        for ip in current - desired:
            await _run("ipset", "del", _IPSET, ip)
        if desired != current:
            log.info("outbound reconcile: warp_users -> %d ip(s)", len(desired))
    except Exception:  # noqa: BLE001
        log.exception("outbound reconcile failed")


# ---- live status (for the panel's Outbounds tab) ---------------------------
_status_cache: dict = {"ts": 0.0, "probe": None}


def _trace_ip(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("ip="):
            return line[3:].strip()
    return None


async def _probe_egress() -> tuple[str | None, bool, str | None]:
    """(direct_egress_ip, warp_up, warp_egress_ip). Best-effort, short timeouts."""
    direct_ip = None
    rc, out = await _run("curl", "-s", "--max-time", "5", "https://1.1.1.1/cdn-cgi/trace")
    if rc == 0:
        direct_ip = _trace_ip(out)
    rc, _ = await _run("wg", "show", "warp")
    warp_up = rc == 0
    warp_ip = None
    if warp_up:
        rc2, out2 = await _run(
            "curl", "-s", "--max-time", "5", "--interface", "warp", "https://1.1.1.1/cdn-cgi/trace"
        )
        if rc2 == 0 and "warp=on" in out2:
            warp_ip = _trace_ip(out2)
    return direct_ip, warp_up, warp_ip


async def status(db: AsyncSession) -> list[dict]:
    """Live status of every outbound for the Settings → Outbounds tab. The egress
    probes are cached 60s so the page is snappy and we don't hammer the network."""
    counts = {
        o: int(c)
        for o, c in (
            await db.execute(select(User.outbound, func.count()).select_from(User).group_by(User.outbound))
        ).all()
    }
    now = time.monotonic()
    if _status_cache["probe"] is None or now - _status_cache["ts"] > 60:
        _status_cache["probe"] = await _probe_egress()
        _status_cache["ts"] = now
    direct_ip, _warp_up, warp_ip = _status_cache["probe"]
    members = await _current_members()
    return [
        {
            "id": DIRECT,
            "name": "Direct",
            "kind": DIRECT,
            "description": "Straight out the exit node's own address. Fastest, default.",
            "status": "up",
            "egress_ip": direct_ip,
            "users": counts.get(DIRECT, 0),
            "active": None,
            "is_default": True,
        },
        {
            "id": WARP,
            "name": "Cloudflare WARP",
            "kind": WARP,
            "description": "Tunnels traffic through Cloudflare WARP — exits on a Cloudflare IP.",
            "status": "up" if warp_ip else "down",
            "egress_ip": warp_ip,
            "users": counts.get(WARP, 0),
            "active": len(members) if members is not None else 0,
            "is_default": False,
        },
    ]
