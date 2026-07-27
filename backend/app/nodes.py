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
from datetime import datetime, timedelta

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
