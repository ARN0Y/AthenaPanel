"""Per-user egress routing.

A user's traffic leaves through a named "outbound". Three kinds exist:

  direct   this host's own address. The default; nothing is installed for it.
  warp     Cloudflare WARP, plumbed out-of-band by setup-warp.sh.
  <name>   an operator-added location: a WireGuard tunnel from this host to a
           server they own, set up by running athena-outbound.sh there and
           pasting the line it prints into the panel.

The mechanism is the same for all of them, and it is deliberately protocol-
blind: packets are matched by the CLIENT'S SOURCE IP, marked, and policy-routed.

    ipset ob-<name>  --mangle PREROUTING-->  MARK  --ip rule-->  table  -->  dev ob-<name>  --> MASQUERADE

Because the match is on the client's address, an outbound covers L2TP/IPsec,
L2TP raw, SSTP and WireGuard the moment it exists, with no per-protocol code.
Adding a fifth protocol tomorrow would need nothing here either — it only has
to give the client an address this module can see.

This module owns exactly one thing: the CONTENTS of each ipset. It reconciles
them to "the client IPs of users currently assigned to this outbound", which
makes it idempotent and self-healing — it is called on connect, on disconnect,
on user edit, on outbound create/delete and on every enforcer cycle, and it
always converges on the truth rather than applying a delta it might have missed.

The plumbing around the ipsets (interface, table, rule, masquerade, health
check) is installed by outbound-plumb.sh, which this module invokes. Keeping
the two apart means the routing survives a panel restart, and an operator can
run the script by hand to inspect or repair a location.

Failure is always toward direct. An absent ipset makes reconcile a no-op; the
health check withdraws an unhealthy outbound's ip rule so its users fall back
to the main table instead of blackholing. A user is never cut off because their
egress broke.
"""

import asyncio
import ipaddress
import logging
import re
import time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import LOCAL_NODE_ID, Outbound
from .models import Session as SessionRow
from .models import User, WgPeer

log = logging.getLogger("vpn-panel.outbound")

WARP = "warp"
DIRECT = "direct"
BUILTINS = {DIRECT, WARP}

_WARP_IPSET = "warp_users"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,11}$")

# Resource pools for operator-added outbounds. warp already holds mark 0x2,
# table 200 and priority 1000; these start clear of it with room to spare.
FIRST_MARK = 0x10
FIRST_TABLE = 210
FIRST_PRIORITY = 1010
# Tunnel /30s are carved out of this. Deliberately not 10.0.0.0/8 territory the
# panel already uses (10.10.0.0/16 wireguard pool, 10.50/10.30 backhauls).
TUNNEL_NET = ipaddress.ip_network("10.201.0.0/16")

PLUMB = "/usr/local/sbin/outbound-plumb.sh"

# Names that resolve to "themselves" in normalize(). Refreshed whenever the
# outbound set changes, so normalize() can stay synchronous — it is called from
# request handlers that have no business doing a query to validate one field.
_known: set[str] = set(BUILTINS)


def known_names() -> set[str]:
    return set(_known)


async def refresh_known(db: AsyncSession) -> set[str]:
    global _known
    rows = (await db.execute(select(Outbound.name).where(Outbound.enabled.is_(True)))).scalars().all()
    _known = set(BUILTINS) | set(rows)
    return set(_known)


def normalize(value: str | None) -> str:
    """Resolve a stored/submitted outbound name, falling back to direct.

    Unknown names degrade rather than raise: an outbound that was deleted while
    a user referenced it simply means that user egresses directly, which is the
    behaviour that keeps them online.
    """
    v = (value or "").strip().lower()
    return v if v in _known else DIRECT


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name or "")) and name not in BUILTINS


def _ip(addr: str) -> str:
    return (addr or "").split("/")[0].strip()


async def _run(*args: str) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
    except FileNotFoundError:
        return 127, f"{args[0]} not installed"
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace")


# ---- who belongs where -----------------------------------------------------


async def desired_ips(db: AsyncSession) -> dict[str, set[str]]:
    """Client IPs that should currently egress via each non-direct outbound.

    Both halves are restricted to THIS host. The ipsets live in this kernel and
    only steer this kernel's traffic, so including a user served by a remote
    node would put an address here that never appears in a packet — clutter that
    reads like a bug the first time someone compares the set against reality.
    """
    out: dict[str, set[str]] = {}

    def add(name: str, addr: str) -> None:
        ip = _ip(addr)
        if not ip:
            return
        name = (name or "").strip().lower()
        if name in ("", DIRECT):
            return
        out.setdefault(name, set()).add(ip)

    rows = (
        await db.execute(
            select(User.outbound, SessionRow.peer_ip)
            .join(User, User.username == SessionRow.username)
            .where(
                SessionRow.node_id == LOCAL_NODE_ID,
                User.outbound != DIRECT,
                SessionRow.peer_ip != "",
            )
        )
    ).all()
    for name, addr in rows:
        add(name, addr)

    # WireGuard peers are served by whichever node owns the user; only ours
    # route through this host's tables.
    rows = (
        await db.execute(
            select(User.outbound, WgPeer.address)
            .join(User, User.id == WgPeer.user_id)
            .where(
                User.outbound != DIRECT,
                User.node_id == LOCAL_NODE_ID,
                WgPeer.enabled.is_(True),
                WgPeer.address != "",
            )
        )
    ).all()
    for name, addr in rows:
        add(name, addr)

    return out


# ---- ipset reconciliation --------------------------------------------------


def _ipset_for(name: str) -> str:
    return _WARP_IPSET if name == WARP else f"ob-{name}"


async def _members(ipset: str) -> set[str] | None:
    """Current members, or None if the set does not exist (plumbing absent)."""
    rc, out = await _run("ipset", "list", ipset)
    if rc != 0:
        return None
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
    """Converge every outbound's ipset to the users assigned to it.

    Safe no-op wherever the plumbing is absent, and never raises into the
    caller: this runs on the connect path, and a routing-policy hiccup must not
    be able to fail a session that is otherwise fine.
    """
    try:
        await refresh_known(db)
        desired = await desired_ips(db)
        # Every outbound we know of is reconciled, not just those with members —
        # otherwise removing the last user from an outbound would leave their IP
        # in its set forever, still being routed.
        names = (set(_known) - {DIRECT}) | set(desired)
        for name in sorted(names):
            ipset = _ipset_for(name)
            want = desired.get(name, set())
            have = await _members(ipset)
            if have is None:
                if want:
                    log.debug("outbound %s: ipset absent; %d ip(s) pending plumbing", name, len(want))
                continue
            for ip in want - have:
                await _run("ipset", "add", ipset, ip, "-exist")
            for ip in have - want:
                await _run("ipset", "del", ipset, ip)
            if want != have:
                log.info("outbound reconcile: %s -> %d ip(s)", name, len(want))
    except Exception:  # noqa: BLE001
        log.exception("outbound reconcile failed")


# ---- resource allocation ---------------------------------------------------


async def allocate(db: AsyncSession) -> tuple[int, int, int, str, str]:
    """Pick an unused (fwmark, table, priority, our address, peer address).

    Scans what is already allocated rather than counting rows, so deleting an
    outbound genuinely frees its resources and a gap in the ids never produces
    a collision.
    """
    rows = (await db.execute(select(Outbound.fwmark, Outbound.table_id,
                                    Outbound.rule_priority, Outbound.address))).all()
    marks = {r[0] for r in rows}
    tables = {r[1] for r in rows}
    prios = {r[2] for r in rows}
    nets = {_ip(r[3]) for r in rows}

    mark = next(m for m in range(FIRST_MARK, FIRST_MARK + 4096) if m not in marks)
    table = next(t for t in range(FIRST_TABLE, FIRST_TABLE + 4096) if t not in tables)
    prio = next(p for p in range(FIRST_PRIORITY, FIRST_PRIORITY + 4096) if p not in prios)

    # The egress server takes the FIRST host of the /30 and this end the second.
    # athena-outbound.sh derives both from --net the same way, so the two ends
    # cannot be told different things; if you change the order here, change it
    # there in the same commit.
    for sub in TUNNEL_NET.subnets(new_prefix=30):
        hosts = list(sub.hosts())
        theirs, ours = str(hosts[0]), str(hosts[1])
        if ours not in nets:
            return mark, table, prio, f"{ours}/30", theirs
    raise RuntimeError("outbound tunnel address space exhausted")


# ---- registration ----------------------------------------------------------

_REGISTRATION_RE = re.compile(
    r"^\s*athena-ob:(?P<host>[A-Za-z0-9_.\-]+):(?P<port>\d{1,5}):(?P<key>[A-Za-z0-9+/]{42,44}=?)\s*$"
)


def generate_keypair() -> tuple[str, str]:
    """(private, public) for this end of a tunnel.

    Shelling out to `wg` rather than doing X25519 here: it is the same
    implementation the kernel will use, it is already a hard dependency, and a
    hand-rolled key derivation is not somewhere to be creative.
    """
    import subprocess

    priv = subprocess.run(["wg", "genkey"], capture_output=True, text=True, check=True).stdout.strip()
    pub = subprocess.run(
        ["wg", "pubkey"], input=priv, capture_output=True, text=True, check=True
    ).stdout.strip()
    return priv, pub


def generate_psk() -> str:
    import subprocess

    return subprocess.run(["wg", "genpsk"], capture_output=True, text=True, check=True).stdout.strip()


def install_command(ob: Outbound, our_public_key: str, port: int) -> str:
    """The exact line to run on the egress server.

    Everything the remote needs travels outward in this string; the only thing
    that comes back is its public key, which is not a secret. The pre-shared key
    is in here, so the panel shows this over its own TLS session and does not
    log it.
    """
    # ipaddress, not string surgery: /30s tile as .0, .4, .8 ... so the network
    # address is only sometimes the one ending in 0, and guessing it would send
    # the second location's server the first location's subnet.
    net = ipaddress.ip_interface(ob.address).network
    return (
        f"./athena-outbound.sh --net {net} "
        f"--peer-key {our_public_key} "
        f"--psk {ob.preshared_key} "
        f"--port {port}"
    )


def parse_registration(line: str) -> tuple[str, int, str] | None:
    """(host, port, public_key) from what athena-outbound.sh printed."""
    m = _REGISTRATION_RE.match(line or "")
    if not m:
        return None
    port = int(m.group("port"))
    if not (1 <= port <= 65535):
        return None
    return m.group("host"), port, m.group("key")


# ---- plumbing (delegated to the script that also runs standalone) ----------


async def plumb_up(ob: Outbound) -> tuple[bool, str]:
    rc, out = await _run(
        PLUMB, "up", ob.name,
        "--endpoint", ob.endpoint,
        "--peer-key", ob.public_key,
        "--psk", ob.preshared_key or "",
        "--private-key", ob.private_key,
        "--address", ob.address,
        "--mtu", str(ob.mtu),
        "--mark", hex(ob.fwmark),
        "--table", str(ob.table_id),
        "--priority", str(ob.rule_priority),
    )
    if rc != 0:
        log.error("outbound %s: plumb up failed rc=%s: %s", ob.name, rc, out.strip()[-500:])
    return rc == 0, out


async def plumb_down(name: str) -> tuple[bool, str]:
    rc, out = await _run(PLUMB, "down", name)
    if rc != 0:
        log.warning("outbound %s: plumb down rc=%s: %s", name, rc, out.strip()[-300:])
    return rc == 0, out


# ---- live status (for the panel's Outbounds tab) ---------------------------

_status_cache: dict = {"ts": 0.0, "probe": None}


def _trace_ip(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("ip="):
            return line[3:].strip()
    return None


async def _egress_ip(iface: str | None) -> str | None:
    args = ["curl", "-s", "--max-time", "5"]
    if iface:
        args += ["--interface", iface]
    args.append("https://1.1.1.1/cdn-cgi/trace")
    rc, out = await _run(*args)
    if rc != 0:
        return None
    if iface == "warp" and "warp=on" not in out:
        return None
    return _trace_ip(out)


async def _probe(db: AsyncSession) -> dict:
    """One pass over every outbound. Cached by status(); never on a hot path."""
    result: dict = {DIRECT: await _egress_ip(None)}
    rc, _ = await _run("wg", "show", "warp")
    result[WARP] = await _egress_ip("warp") if rc == 0 else None
    for ob in (await db.execute(select(Outbound))).scalars().all():
        rc, _ = await _run("wg", "show", ob.ifname)
        result[ob.name] = await _egress_ip(ob.ifname) if rc == 0 else None
    return result


async def status(db: AsyncSession) -> list[dict]:
    """Live status of every outbound for the Settings -> Outbounds tab. The
    egress probes are cached 60s so the page is snappy and we don't hammer the
    network on every poll."""
    counts = {
        o: int(c)
        for o, c in (
            await db.execute(select(User.outbound, func.count()).select_from(User).group_by(User.outbound))
        ).all()
    }
    now = time.monotonic()
    if _status_cache["probe"] is None or now - _status_cache["ts"] > 60:
        _status_cache["probe"] = await _probe(db)
        _status_cache["ts"] = now
    probe: dict = _status_cache["probe"] or {}

    async def active(name: str) -> int:
        m = await _members(_ipset_for(name))
        return len(m) if m is not None else 0

    out: list[dict] = [
        {
            "id": DIRECT,
            "name": "Direct",
            "kind": DIRECT,
            "description": "Straight out the exit node's own address. Fastest, default.",
            "status": "up",
            "egress_ip": probe.get(DIRECT),
            "users": counts.get(DIRECT, 0),
            "active": None,
            "is_default": True,
            "removable": False,
        },
        {
            "id": WARP,
            "name": "Cloudflare WARP",
            "kind": WARP,
            "description": "Tunnels traffic through Cloudflare WARP — exits on a Cloudflare IP.",
            "status": "up" if probe.get(WARP) else "down",
            "egress_ip": probe.get(WARP),
            "users": counts.get(WARP, 0),
            "active": await active(WARP),
            "is_default": False,
            "removable": False,
        },
    ]
    for ob in (await db.execute(select(Outbound).order_by(Outbound.id))).scalars().all():
        ip = probe.get(ob.name)
        out.append(
            {
                "id": ob.name,
                # The slug IS the display name. There is deliberately no second
                # "label" field to keep in sync with it.
                "name": ob.name,
                "country": ob.country,
                "kind": ob.kind,
                "description": ob.note or f"WireGuard tunnel to {ob.endpoint}",
                "status": "up" if ip else ("disabled" if not ob.enabled else "down"),
                "egress_ip": ip,
                "users": counts.get(ob.name, 0),
                "active": await active(ob.name),
                "is_default": False,
                "removable": True,
                "endpoint": ob.endpoint,
                "mtu": ob.mtu,
                "created_at": ob.created_at.isoformat() if ob.created_at else None,
            }
        )
    return out
