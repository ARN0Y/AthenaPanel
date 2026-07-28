"""Node management — superadmin only.

A node is a machine that terminates users. Node 1 is this panel server itself,
which is why it has no agent and cannot be edited or deleted here: it is
discovered from the local kernel, not from anything an agent claims.
"""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import (
    audit, chap_secrets, livecache, nodes as nodes_mod, nodesessions, pki,
    sysmon, wireguard,
)
from ..database import get_session
from ..deps import require_superadmin
from ..models import LOCAL_NODE_ID, Node, Session as SessionRow, User
from ..schemas import NodeCreate, NodeCreated, NodeOut, NodeUpdate
from .stats import _port_bound

log = logging.getLogger("vpn-panel.nodes-api")

router = APIRouter(
    prefix="/api/nodes", tags=["nodes"], dependencies=[Depends(require_superadmin)]
)


def _local_address() -> str:
    """This server's own outbound address, read from the kernel.

    Node 1's address is a property of the machine, so it is discovered rather
    than typed — an operator editing it could only ever make it wrong, which is
    also why the local node is not editable. Uses a UDP socket with no traffic:
    connect() on UDP just selects a route, so this asks the routing table which
    source address it would use without sending a packet or needing the network
    to be reachable.
    """
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.settimeout(0.2)
            sk.connect(("1.1.1.1", 53))
            return sk.getsockname()[0]
    except OSError:
        return ""


def _live_counts() -> dict[int, tuple[int, int]]:
    """(ppp, wireguard) session counts per node, from the shared live snapshot.

    One source for the Nodes page and the Sessions page, so the two can never
    show different numbers for the same machine. It also means a node that has
    gone quiet reports zero rather than its last known figure: its rows are
    HELD, not closed, but nobody can currently say those sessions are up, and
    the card already says the node is offline.

    Counting from the sessions table instead would drift by up to one poll
    against what the operator sees on the Sessions page — and a panel that
    contradicts itself is read as broken, however defensible each number is.
    """
    counts: dict[int, list[int]] = {}
    for s in livecache.snapshot().get("sessions") or []:
        node_id = getattr(s, "node_id", LOCAL_NODE_ID)
        slot = counts.setdefault(node_id, [0, 0])
        slot[1 if s.protocol == "WireGuard" else 0] += 1
    return {nid: (c[0], c[1]) for nid, c in counts.items()}


def _local_host() -> dict:
    """Host stats for the report node 1 never sends.

    The panel server is node 1, and it has no agent — it reads its own kernel
    directly. Without this its card would show no host stats, which is not "we
    don't know", it is "we know and threw it away".
    """
    try:
        sysinfo = sysmon.collect()
        host = {
            "uptime_seconds": sysinfo.uptime_seconds,
            "load1": sysinfo.load_1,
            "mem_total_bytes": sysinfo.mem_total,
            "mem_available_bytes": max(0, sysinfo.mem_total - sysinfo.mem_used),
        }
    except Exception:  # noqa: BLE001
        host = {}
    # Detect engines the same way /api/health does — by which port is bound, not
    # by unit name. A service-name check already broke once here when a daemon
    # ran under a different unit and the panel reported it as down.
    host.update({
        "xl2tpd_ok": _port_bound(1701),
        "ipsec_ok": _port_bound(500) or _port_bound(4500),
        "accel_ppp_ok": _port_bound(443),
        "wireguard_ok": wireguard.iface_up(),
    })
    return host


def _summarise(node: Node, counts: tuple[int, int]) -> NodeOut:
    """Flatten a node plus its newest report into something the UI can render.

    `counts` is (ppp, wireguard) from the live snapshot — the same numbers the
    Sessions page is showing right now. The card displays a total and a
    breakdown of that total, so both come from one place: reading the total from
    the table and the breakdown from the last report let them disagree on screen
    whenever one moved before the other, which reads as a broken panel rather
    than as the sampling gap it is.

    WireGuard on a REMOTE node is the exception, and it has to be. The panel
    provisions WireGuard itself, so every peer it knows about is on this
    machine; a peer on another node was put there by something else and cannot
    be attributed to an account, which is why it is counted from that node's
    report and never turned into a session. Showing zero instead would claim the
    node is idle when it is carrying traffic.
    """
    host: dict = {}
    report: dict = {}
    if node.is_local:
        host = _local_host()
    elif node.last_report:
        try:
            report = json.loads(node.last_report) or {}
            host = report.get("host") or {}
        except ValueError:
            report = {}

    ppp_count, wg_count = counts
    if not node.is_local:
        wg_count = len(report.get("wg") or [])

    online = False
    seen_seconds: int | None = None
    if node.is_local:
        # Node 1 is this process. It is online whenever the panel is answering,
        # so asking whether an agent checked in would be meaningless.
        online = True
    elif node.last_seen_at is not None:
        seen = node.last_seen_at
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        seen_seconds = int((datetime.now(timezone.utc) - seen).total_seconds())
        online = seen_seconds < nodes_mod.STALE_AFTER_SECONDS

    return NodeOut(
        id=node.id,
        name=node.name,
        is_local=node.is_local,
        enabled=node.enabled,
        # Node 1's address is discovered, never stored: it belongs to the
        # machine, and a stale row would be worse than no row.
        address=(_local_address() or node.address) if node.is_local else node.address,
        note=node.note,
        agent_version=node.agent_version,
        hostname=node.hostname,
        kernel=node.kernel,
        online=online,
        last_seen_seconds=seen_seconds,
        sessions=ppp_count + wg_count,
        ppp_count=ppp_count,
        wg_count=wg_count,
        uptime_seconds=int(host.get("uptime_seconds") or 0),
        load1=float(host.get("load1") or 0.0),
        mem_total_bytes=int(host.get("mem_total_bytes") or 0),
        mem_available_bytes=int(host.get("mem_available_bytes") or 0),
        xl2tpd_ok=bool(host.get("xl2tpd_ok")),
        ipsec_ok=bool(host.get("ipsec_ok")),
        accel_ppp_ok=bool(host.get("accel_ppp_ok")),
        wireguard_ok=bool(host.get("wireguard_ok")),
        rx_total_bytes=node.rx_total_bytes or 0,
        tx_total_bytes=node.tx_total_bytes or 0,
        # A node that stopped reporting is not moving traffic, whatever its
        # last sample said. Showing a stale rate as "current" is worse than
        # showing zero.
        rx_rate_bps=(node.rx_rate_bps or 0) if online else 0,
        tx_rate_bps=(node.tx_rate_bps or 0) if online else 0,
        wg_port=node.wg_port,
        sstp_port=node.sstp_port,
        l2tp_port=node.l2tp_port,
        ext_l2tp_address=node.ext_l2tp_address or "",
        ext_l2tp_raw_address=node.ext_l2tp_raw_address or "",
        ext_sstp_address=node.ext_sstp_address or "",
        ext_wg_endpoint=node.ext_wg_endpoint or "",
    )


async def _get(db: AsyncSession, node_id: int) -> Node:
    node = (await db.execute(select(Node).where(Node.id == node_id))).scalar_one_or_none()
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Node not found")
    return node


@router.get("", response_model=list[NodeOut])
async def list_nodes(db: AsyncSession = Depends(get_session)):
    counts = _live_counts()
    rows = (await db.execute(select(Node).order_by(Node.id))).scalars().all()
    return [_summarise(n, counts.get(n.id, (0, 0))) for n in rows]


@router.post("", response_model=NodeCreated, status_code=status.HTTP_201_CREATED)
async def create_node(
    payload: NodeCreate,
    db: AsyncSession = Depends(get_session),
    admin=Depends(require_superadmin),
):
    """Register a node and hand back everything its agent needs.

    The certificate and key are returned ONCE and never stored on the panel —
    only the CA stays here, so a panel compromise does not also hand over every
    node's private key. Losing the bundle is recoverable (reissue it); that is
    a better trade than keeping copies around.
    """
    node = await nodes_mod.register(
        db, name=payload.name.strip(), address=(payload.address or "").strip(),
        note=(payload.note or "").strip(),
    )
    node.wg_port = payload.wg_port
    node.sstp_port = payload.sstp_port
    node.l2tp_port = payload.l2tp_port
    await audit.record(db, "create_node", node.name, f"id={node.id}", actor=admin.username)
    await db.commit()

    cert, key, ca = pki.issue_node(node.id, node.name)
    return NodeCreated(
        id=node.id, name=node.name, token=node.token,
        client_cert=cert, client_key=key, ca_cert=ca,
    )


@router.post("/{node_id}/rotate", response_model=NodeCreated)
async def rotate_node(
    node_id: int,
    db: AsyncSession = Depends(get_session),
    admin=Depends(require_superadmin),
):
    """Mint a fresh token and certificate. The old ones stop working at once."""
    node = await _get(db, node_id)
    if node.is_local:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The local node has no agent")
    node.token = nodes_mod.new_token()
    await audit.record(db, "rotate_node", node.name, f"id={node.id}", actor=admin.username)
    await db.commit()

    cert, key, ca = pki.issue_node(node.id, node.name)
    return NodeCreated(
        id=node.id, name=node.name, token=node.token,
        client_cert=cert, client_key=key, ca_cert=ca,
    )


@router.post("/{node_id}/reconnect", status_code=status.HTTP_202_ACCEPTED)
async def reconnect_node(
    node_id: int,
    db: AsyncSession = Depends(get_session),
    admin=Depends(require_superadmin),
):
    """Ask the hub to drop this node's stream so its agent redials.

    Goes through the database because the hub runs as its own process; it
    notices the request on the node's next report and closes the stream, and
    the agent's backoff brings it back within seconds. Nothing is lost by
    cutting mid-flight — the agent holds no state and re-derives everything
    from the kernel on the next report.
    """
    node = await _get(db, node_id)
    if node.is_local:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The local node has no agent")
    node.reconnect_requested_at = datetime.now(timezone.utc)
    await audit.record(db, "reconnect_node", node.name, f"id={node.id}", actor=admin.username)
    await db.commit()
    return {"detail": "Reconnect requested"}


@router.patch("/{node_id}", response_model=NodeOut)
async def update_node(
    node_id: int,
    payload: NodeUpdate,
    db: AsyncSession = Depends(get_session),
    admin=Depends(require_superadmin),
):
    node = await _get(db, node_id)
    if node.is_local:
        # Node 1's identity comes from the machine this runs on. Letting it be
        # renamed or disabled here would only create a lie in the database.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The local node cannot be edited")

    changed = []
    if payload.name is not None and payload.name.strip():
        node.name = payload.name.strip()
        changed.append("name")
    if payload.address is not None:
        node.address = payload.address.strip()
        changed.append("address")
    if payload.note is not None:
        node.note = payload.note.strip()
        changed.append("note")
    if payload.enabled is not None:
        node.enabled = payload.enabled
        changed.append("enabled")
    for field in ("wg_port", "sstp_port", "l2tp_port"):
        value = getattr(payload, field)
        if value is not None:
            if not 1 <= value <= 65535:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{field} must be 1-65535")
            setattr(node, field, value)
            changed.append(field)
    # External proxy. Empty is meaningful — it means "inherit the panel-wide
    # setting" — so an empty string is stored rather than ignored.
    for field in ("ext_l2tp_address", "ext_l2tp_raw_address",
                  "ext_sstp_address", "ext_wg_endpoint"):
        value = getattr(payload, field)
        if value is not None:
            setattr(node, field, value.strip())
            changed.append(field)

    await audit.record(db, "update_node", node.name, ", ".join(changed) or "no change", actor=admin.username)
    await db.commit()
    return _summarise(node, _live_counts().get(node.id, (0, 0)))


@router.delete("/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    node_id: int,
    db: AsyncSession = Depends(get_session),
    admin=Depends(require_superadmin),
):
    node = await _get(db, node_id)
    if node.is_local or node.id == LOCAL_NODE_ID:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The local node cannot be deleted")

    # Deleting a node out from under LIVE sessions strands rows nothing can ever
    # close, so a node that is still reporting must be drained first. A node
    # that has gone silent is a different case entirely: its rows are held, not
    # live, and nothing will ever resume them — refusing there would make a dead
    # node permanently undeletable. Their bytes are already billed (the credit
    # loop commits as it goes), so dropping them loses no money, only a display.
    live = (
        await db.execute(
            select(func.count(SessionRow.id)).where(SessionRow.node_id == node_id)
        )
    ).scalar_one()
    reporting = False
    if node.last_seen_at is not None:
        seen = node.last_seen_at
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        reporting = (
            datetime.now(timezone.utc) - seen
        ).total_seconds() < nodes_mod.STALE_AFTER_SECONDS
    if live and reporting:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{live} session(s) still on this node. Disable it and wait for them to end.",
        )

    name = node.name
    dropped = await nodesessions.drop_node(db, node_id)
    if dropped:
        log.warning(
            "deleting silent node %d (%s): dropped %d held session row(s)",
            node_id, name, dropped,
        )

    # Accounts assigned here come home rather than being orphaned. A user whose
    # node_id points at a row that no longer exists is served by nobody: they
    # are absent from every node's account list and from the master's, so they
    # simply stop being able to connect, with nothing in the UI to say why.
    # Reassigning is also the reversible choice — the operator can move them out
    # again, whereas silently broken accounts have to be found first.
    stranded = (
        await db.execute(select(User).where(User.node_id == node_id))
    ).scalars().all()
    for user in stranded:
        user.node_id = LOCAL_NODE_ID
        user.disconnect_requested_at = None

    await db.delete(node)
    detail = f"id={node_id}"
    if stranded:
        detail += f", {len(stranded)} account(s) moved to node {LOCAL_NODE_ID}"
        log.warning("node %d deleted: moved %d account(s) home", node_id, len(stranded))
    await audit.record(db, "delete_node", name, detail, actor=admin.username)
    await db.commit()
    if stranded:
        # They are the local node's responsibility now, so it needs their
        # credentials before they can authenticate again.
        await chap_secrets.rewrite(db)
