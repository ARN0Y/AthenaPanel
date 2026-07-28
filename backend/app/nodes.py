"""Which nodes are currently trustworthy enough to close a session.

The rule this module exists to enforce: a session may only be finalized by a
node that is authoritative RIGHT NOW. Silence is not evidence of disconnection.

Without it, a master<->node link that flaps for one poll interval makes every
session on that node look gone, so the enforcer commits their bytes, deletes the
rows, and then bills the very same sessions again when the node returns with
them still alive. On a single-server install the distinction is invisible
(sysfs cannot "go quiet"), which is exactly why it has to be encoded before the
first remote node exists rather than after the first wrong invoice.
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import LOCAL_NODE_ID, Node

log = logging.getLogger("vpn-panel.nodes")

# A node must have reported within this window to keep authority over its
# sessions. Generous on purpose: holding a session too long only delays a
# ledger row, while dropping authority too eagerly is what causes double
# billing. Must stay comfortably above the agent's report interval.
STALE_AFTER_SECONDS = 90


async def _sync_id_sequence(db: AsyncSession) -> None:
    """Point the id sequence past the highest existing node id.

    Node 1 is inserted with an EXPLICIT id so the constant LOCAL_NODE_ID always
    means this server. Postgres does not advance a serial's sequence for an
    explicit insert, so the next auto-assigned id would be 1 again and collide.
    Cheap, idempotent, and a no-op on SQLite.
    """
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    try:
        await db.execute(
            text(
                "SELECT setval(pg_get_serial_sequence('nodes','id'), "
                "GREATEST((SELECT COALESCE(MAX(id), 1) FROM nodes), 1))"
            )
        )
    except Exception:  # noqa: BLE001
        log.exception("could not sync the nodes id sequence")


async def ensure_local(db: AsyncSession) -> Node:
    """Make sure node 1 (this server) exists. Idempotent."""
    node = (
        await db.execute(select(Node).where(Node.id == LOCAL_NODE_ID))
    ).scalar_one_or_none()
    if node is None:
        node = Node(
            id=LOCAL_NODE_ID,
            name="local",
            is_local=True,
            enabled=True,
            address="",
            note="This panel server. Terminates users itself as node 1.",
        )
        db.add(node)
        await db.flush()
        await _sync_id_sequence(db)
        log.info("registered the local server as node %d", LOCAL_NODE_ID)
    return node


def effective_endpoints(node: Node | None, app_settings: dict) -> dict[str, str]:
    """What a customer on this node is told to connect to.

    The node's OWN address is never part of this. That address is the machine
    abroad; customers reach it through a relay, and handing out the real one
    would both fail to connect and expose infrastructure that is meant to stay
    behind the entry.

    A node with no external proxy of its own inherits the panel-wide setting.
    That is deliberate rather than lazy: it is what lets node 1 keep serving the
    accounts that predate nodes without anything being copied into it, so there
    is exactly one place to change an address that every node shares.
    """
    def pick(node_value: str | None, fallback_key: str) -> str:
        value = (node_value or "").strip()
        return value or (app_settings.get(fallback_key) or "").strip()

    wg = pick(node.ext_wg_endpoint if node else "", "wg_endpoint")
    # wg_endpoint is host:port panel-wide, while a node carries host and port
    # separately. Respect an explicit port in either place, preferring the one
    # the operator typed into the endpoint itself.
    if node is not None and wg and ":" not in wg:
        wg = f"{wg}:{node.wg_port}"

    return {
        "l2tp": pick(node.ext_l2tp_address if node else "", "server_address"),
        "l2tp_raw": pick(node.ext_l2tp_raw_address if node else "", "l2tp_raw_address"),
        "sstp": pick(node.ext_sstp_address if node else "", "sstp_address"),
        "wg": wg,
    }


def accumulate_traffic(
    node: Node,
    counters: dict[str, tuple[int, int]],
    previous: dict[str, tuple[int, int]],
    now: datetime,
) -> None:
    """Fold one observation of a node's ABSOLUTE per-interface counters into its
    running totals and throughput.

    `counters` and `previous` are {key: (rx, tx)} for the same node, two
    observations apart. Only positive movement counts, so a counter that reset
    (interface bounced, peer re-added) contributes its new value rather than a
    negative number — the same guard pppd.usage_delta applies, minus the billing
    multiplier, because this is a capacity figure and not an invoice.

    An interface that disappears simply stops contributing; its bytes are
    already in the total. An interface that appears starts from its current
    value, which slightly under-counts its first interval — the alternative,
    treating the whole counter as new traffic, would wildly over-count instead.
    """
    rx_delta = tx_delta = 0
    for key, (rx, tx) in counters.items():
        prev = previous.get(key)
        if prev is None:
            continue
        prx, ptx = prev
        rx_delta += (rx - prx) if rx >= prx else rx
        tx_delta += (tx - ptx) if tx >= ptx else tx

    node.rx_total_bytes = (node.rx_total_bytes or 0) + max(0, rx_delta)
    node.tx_total_bytes = (node.tx_total_bytes or 0) + max(0, tx_delta)

    last = node.rate_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    elapsed = (now - last).total_seconds() if last else 0.0
    # Guard both ends: a sub-second gap makes the rate explode, and a long gap
    # (node was away) would smear an hour of traffic into a "current" figure.
    if 0.5 < elapsed < 120:
        node.rx_rate_bps = int(max(0, rx_delta) * 8 / elapsed)
        node.tx_rate_bps = int(max(0, tx_delta) * 8 / elapsed)
    elif elapsed >= 120:
        node.rx_rate_bps = node.tx_rate_bps = 0
    node.rate_at = now


def new_token() -> str:
    """A node's stream credential. 43 chars of urlsafe base64 (256 bits)."""
    return secrets.token_urlsafe(32)


async def register(
    db: AsyncSession, *, name: str, address: str = "", note: str = ""
) -> Node:
    """Create a remote node and mint its token. Caller commits."""
    # Guard against a sequence left behind by node 1's explicit-id insert, and
    # against any database restored from a dump where it was never advanced.
    await _sync_id_sequence(db)
    node = Node(
        name=name,
        is_local=False,
        enabled=True,
        address=address,
        note=note,
        token=new_token(),
    )
    db.add(node)
    await db.flush()
    log.info("registered node %d (%s)", node.id, node.name)
    return node


async def authoritative_ids(
    db: AsyncSession, now: datetime, local_scan_ok: bool
) -> set[int]:
    """Node ids whose reported session state may be acted on this cycle.

    `local_scan_ok` is the result of actually enumerating local interfaces. If
    that read failed there is no local truth this cycle either, so node 1 loses
    authority the same way a silent remote node does — an unreadable /sys must
    never look like "every user disconnected at once".

    `enabled` is deliberately NOT consulted. It controls whether a node may be
    given NEW users; it says nothing about whether the node is telling the
    truth about the sessions it already has. Draining a node by disabling it
    must not freeze its open sessions in limbo, unbilled forever.
    """
    ok: set[int] = set()
    cutoff = now - timedelta(seconds=STALE_AFTER_SECONDS)
    for node in (await db.execute(select(Node))).scalars().all():
        if node.is_local:
            if local_scan_ok:
                ok.add(node.id)
            continue
        seen = node.last_seen_at
        if seen is None:
            continue
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=now.tzinfo)
        if seen >= cutoff:
            ok.add(node.id)
    return ok
