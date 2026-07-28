"""A remote node's live sessions, mirrored into the `sessions` table.

Why mirror them at all: a session on node 3 exists only in that node's kernel.
The Sessions page, the online badge, the per-node session count and every
"is this user connected" check read one table, and a remote session that never
lands there is invisible to all of them — the customer is connected and the
panel says nobody is.

DISPLAY AND OWNERSHIP ONLY, NEVER BILLING. A remote user's bytes are billed
through the credit path (nodecredit.handle_credit_request) continuously, as they
are spent. Crediting them again when the row closes would double every remote
invoice, so nothing in this module touches used_bytes and nothing here writes an
accounting row. Closing a remote session is a delete, and that is the whole
point of the split: exactly one path bills, exactly one path displays.

The hub is the ONLY writer of these rows, and that is what makes "hold a silent
node's sessions" fall out for free rather than needing to be enforced: this code
only runs while a node is reporting. A node that goes quiet simply stops
updating its rows — they keep their counters and their start time, and when it
comes back the same rows resume. Silence cannot be mistaken for a disconnect
here because silence never reaches this file. (The local node's rows are the
enforcer's, and it applies the same rule through nodes.authoritative_ids.)
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import pppd
from .models import Session as SessionRow, User, WgPeer

log = logging.getLogger("vpn-panel.nodesessions")

# Reports every ~15s, so one missing report is a plausible hiccup and two is a
# statement. Matches the enforcer's debounce for local interfaces on purpose:
# "gone" should not mean something different depending on which node you are on.
GONE_REPORTS_BEFORE_CLOSE = 2

# A WireGuard peer counts as connected while its last handshake is this recent.
# Same value as tasks.WG_ONLINE_WINDOW, and for the same reason: WireGuard
# rekeys about every two minutes, so a shorter window makes a healthy peer flap.
WG_ONLINE_WINDOW = 180


async def _note_new_session(db: AsyncSession, username: str, now: datetime) -> None:
    """Mirror what the local ip-up hook records when a session starts."""
    if not username:
        return
    user = (
        await db.execute(select(User).where(User.username == username))
    ).scalar_one_or_none()
    if user is not None:
        user.last_seen = now
        user.total_sessions = (user.total_sessions or 0) + 1


async def apply_report(
    db: AsyncSession, node_id: int, ppp: list[dict], now: datetime
) -> None:
    """Reconcile one node's reported ppp interfaces against its session rows.

    `ppp` entries carry ABSOLUTE interface counters, which is what lets a
    resent or duplicated report be applied twice with no effect: the row is set
    to the reported value rather than advanced by a delta. Caller commits.
    """
    rows = {
        r.ifname: r
        for r in (
            await db.execute(
                # WireGuard rows live in the same table but are reconciled
                # against a different part of the report; sweeping them here
                # would delete every peer two reports after it appeared.
                select(SessionRow).where(
                    SessionRow.node_id == node_id, SessionRow.proto != "wg"
                )
            )
        ).scalars().all()
    }
    seen: set[str] = set()

    for entry in ppp:
        ifname = (entry.get("ifname") or "")[:32]
        if not ifname:
            continue
        seen.add(ifname)
        username = (entry.get("username") or "")[:128]
        peer_ip = (entry.get("peer_ip") or "")[:64]
        rx = max(0, int(entry.get("rx_bytes") or 0))
        tx = max(0, int(entry.get("tx_bytes") or 0))
        pid = int(entry.get("pid") or 0)
        row = rows.get(ifname)

        if row is None:
            db.add(
                SessionRow(
                    node_id=node_id,
                    username=username,
                    ifname=ifname,
                    peer_ip=peer_ip,
                    pid=pid,
                    proto=pppd.classify_proto(peer_ip),
                    # A ppp interface is created per session and starts at zero,
                    # so the counter IS this session's usage — the same baseline
                    # the local ip-up hook anchors.
                    base_rx=0,
                    base_tx=0,
                    last_rx=rx,
                    last_tx=tx,
                )
            )
            await _note_new_session(db, username, now)
            if username:
                log.info("node %d: session %s/%s started", node_id, username, ifname)
            else:
                # The node could not say whose it is — the session predates its
                # agent, so no ip-up hook ever ran for it. The row is kept
                # anyway: its traffic is real and losing it is worse than not
                # knowing who to bill, and this is the only line that says so.
                log.warning(
                    "node %d: %s has no owner (session predates the agent); "
                    "tracked but unattributed", node_id, ifname,
                )
            continue

        # Interface numbers are recycled. If ppp0 comes back with a counter
        # BELOW what we last saw, or serving a different account, the old
        # session ended without us being told (agent restart, lost hook) and
        # this is a new one. Re-anchoring is what keeps the previous session's
        # bytes from being displayed as part of this one.
        restarted = (
            rx < row.last_rx
            or tx < row.last_tx
            or (username and username != row.username)
        )
        if restarted:
            log.info(
                "node %d: %s reused for %s (counter restarted); re-anchoring",
                node_id, ifname, username or row.username,
            )
            row.username = username or row.username
            row.proto = pppd.classify_proto(peer_ip)
            row.started_at = now
            row.base_rx = row.base_tx = 0
            await _note_new_session(db, username, now)

        row.last_rx, row.last_tx = rx, tx
        row.pid = pid
        if peer_ip:
            row.peer_ip = peer_ip
        row.gone_polls = 0
        row.stale_since = None

    for ifname, row in rows.items():
        if ifname in seen:
            continue
        # The node is reporting and does not list this interface, so it really
        # is gone. Normally the row was already removed by the SESSION_ENDED
        # credit request, which carries the final byte count; reaching here
        # means that message never arrived, so this is the safety net.
        row.gone_polls = (row.gone_polls or 0) + 1
        if row.gone_polls >= GONE_REPORTS_BEFORE_CLOSE:
            log.info(
                "node %d: %s/%s vanished from the report without a session-ended "
                "message; dropping the row (its bytes were billed as they were spent)",
                node_id, row.username or "?", ifname,
            )
            await db.delete(row)


async def apply_wg_report(
    db: AsyncSession, node_id: int, peers: list[dict], now: datetime
) -> None:
    """Fold a node's WireGuard peers into the same rows its ppp sessions use.

    WireGuard has no connect or disconnect, so "online" is inferred from the
    handshake — the same rule the master applies to its own peers. A peer that
    has handshaken recently gets a session row; one that has gone quiet loses
    it. Doing anything else would leave WireGuard as the one protocol that is
    invisible on the Sessions page the moment it moves off node 1.

    Attribution is by public key against `wg_peers`, which only matches keys the
    PANEL issued. A peer somebody put on the node by hand is deliberately left
    alone: it is counted on the node card from the report, but inventing a
    session for traffic that belongs to nobody would be worse than omitting it.

    Bytes are not billed here. Like ppp, a remote peer's traffic is billed by
    the credit loop as it is spent; these rows are display and ownership only.
    """
    reported = {
        (p.get("public_key") or ""): p for p in peers if p.get("public_key")
    }
    rows = {
        r.ifname: r
        for r in (
            await db.execute(
                select(SessionRow).where(
                    SessionRow.node_id == node_id, SessionRow.proto == "wg"
                )
            )
        ).scalars().all()
    }

    owners: dict[str, tuple[str, str]] = {}
    if reported:
        found = (
            await db.execute(
                select(WgPeer.public_key, WgPeer.address, User.username)
                .join(User, User.id == WgPeer.user_id)
                .where(WgPeer.public_key.in_(list(reported)))
            )
        ).all()
        owners = {pk: (username, address) for pk, address, username in found}

    seen: set[str] = set()
    for public_key, entry in reported.items():
        owner = owners.get(public_key)
        if owner is None:
            continue
        username, address = owner
        ifname = _wg_ifname(address)
        seen.add(ifname)
        handshake = int(entry.get("last_handshake_unix") or 0)
        online = handshake > 0 and (now.timestamp() - handshake) < WG_ONLINE_WINDOW
        rx = max(0, int(entry.get("rx_bytes") or 0))
        tx = max(0, int(entry.get("tx_bytes") or 0))
        row = rows.get(ifname)

        if not online:
            if row is not None:
                await db.delete(row)
            continue
        if row is None:
            # A new online period. The base is the counter as it stands, so the
            # previous period's bytes are not shown as part of this one — the
            # same anchoring the master's enforcer does with session_base_rx.
            db.add(
                SessionRow(
                    node_id=node_id,
                    username=username,
                    ifname=ifname,
                    peer_ip=address,
                    pid=0,
                    proto="wg",
                    base_rx=rx,
                    base_tx=tx,
                    last_rx=rx,
                    last_tx=tx,
                )
            )
            await _note_new_session(db, username, now)
            log.info("node %d: wireguard %s is up (%s)", node_id, username, address)
            continue
        if rx < row.last_rx or tx < row.last_tx:
            # The peer was re-added, so its counters restarted. Re-anchor rather
            # than let the difference go negative.
            row.base_rx = row.base_tx = 0
            row.started_at = now
        row.username = username
        row.last_rx, row.last_tx = rx, tx
        row.gone_polls = 0
        row.stale_since = None

    for ifname, row in rows.items():
        if ifname not in seen:
            # The panel still has the peer but the node no longer reports it —
            # it was revoked there, or moved to another node.
            await db.delete(row)


def _wg_ifname(address: str) -> str:
    """Session key for a WireGuard peer. Matches what the master's snapshot
    uses (`wg:<address>`) so the Sessions page reads identically on any node."""
    return f"wg:{address}"[:32]


async def close_session(
    db: AsyncSession, node_id: int, ifname: str
) -> SessionRow | None:
    """Remove a remote session row and hand it back so the caller can read it.

    Returns None when there is no row — a session that ended before the panel
    ever saw a report for it, or one already dropped by the safety net above.
    That is not an error, and the caller must cope: the ledger entry is still
    worth writing without a start time.
    """
    if not ifname:
        return None
    row = (
        await db.execute(
            select(SessionRow).where(
                SessionRow.node_id == node_id, SessionRow.ifname == ifname
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        await db.delete(row)
    return row


async def drop_node(db: AsyncSession, node_id: int) -> int:
    """Forget every session row for a node. Caller commits.

    Used when a node is deleted. The rows would otherwise linger forever with
    no node to resume them, counting toward "online" for accounts that are not.
    """
    rows = (
        await db.execute(select(SessionRow).where(SessionRow.node_id == node_id))
    ).scalars().all()
    for row in rows:
        await db.delete(row)
    return len(rows)
