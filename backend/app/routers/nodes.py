"""Node management — superadmin only.

A node is a machine that terminates users. Node 1 is this panel server itself,
which is why it has no agent and cannot be edited or deleted here: it is
discovered from the local kernel, not from anything an agent claims.
"""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, livecache, nodes as nodes_mod, pki, sysmon, wireguard
from ..database import get_session
from ..deps import require_superadmin
from ..models import LOCAL_NODE_ID, Node, Session as SessionRow
from ..schemas import NodeCreate, NodeCreated, NodeOut, NodeUpdate
from .stats import _port_bound

router = APIRouter(
    prefix="/api/nodes", tags=["nodes"], dependencies=[Depends(require_superadmin)]
)


def _local_snapshot() -> tuple[dict, dict]:
    """Stand in for the report node 1 never sends.

    The panel server is node 1, and it has no agent — it reads its own kernel
    directly. Without this its card would show zero tunnels and no host stats,
    which is not "we don't know", it is "we know and threw it away". Shaped like
    a report so the rest of the summariser does not care where it came from.
    """
    live = livecache.snapshot().get("online") or []
    ppp = [s for s in live if getattr(s, "protocol", "") != "WireGuard"]
    wg = [s for s in live if getattr(s, "protocol", "") == "WireGuard"]
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
    return {"ppp": ppp, "wg": wg}, host


def _summarise(node: Node, sessions: int) -> NodeOut:
    """Flatten a node plus its newest report into something the UI can render."""
    report: dict = {}
    host: dict = {}
    if node.is_local:
        report, host = _local_snapshot()
    elif node.last_report:
        try:
            report = json.loads(node.last_report)
            host = report.get("host") or {}
        except ValueError:
            report = {}

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
        address=node.address,
        note=node.note,
        agent_version=node.agent_version,
        hostname=node.hostname,
        kernel=node.kernel,
        online=online,
        last_seen_seconds=seen_seconds,
        sessions=sessions,
        ppp_count=len(report.get("ppp") or []),
        wg_count=len(report.get("wg") or []),
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


async def _session_counts(db: AsyncSession) -> dict[int, int]:
    rows = await db.execute(
        select(SessionRow.node_id, func.count(SessionRow.id)).group_by(SessionRow.node_id)
    )
    return {nid: cnt for nid, cnt in rows.all()}


async def _get(db: AsyncSession, node_id: int) -> Node:
    node = (await db.execute(select(Node).where(Node.id == node_id))).scalar_one_or_none()
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Node not found")
    return node


@router.get("", response_model=list[NodeOut])
async def list_nodes(db: AsyncSession = Depends(get_session)):
    counts = await _session_counts(db)
    rows = (await db.execute(select(Node).order_by(Node.id))).scalars().all()
    return [_summarise(n, counts.get(n.id, 0)) for n in rows]


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
    counts = await _session_counts(db)
    return _summarise(node, counts.get(node.id, 0))


@router.delete("/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    node_id: int,
    db: AsyncSession = Depends(get_session),
    admin=Depends(require_superadmin),
):
    node = await _get(db, node_id)
    if node.is_local or node.id == LOCAL_NODE_ID:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The local node cannot be deleted")

    # Sessions carry accounting state; deleting a node out from under live ones
    # would strand rows nothing can ever finalize. Disable it, let its sessions
    # drain, then delete.
    live = (
        await db.execute(
            select(func.count(SessionRow.id)).where(SessionRow.node_id == node_id)
        )
    ).scalar_one()
    if live:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{live} session(s) still on this node. Disable it and wait for them to end.",
        )

    name = node.name
    await db.delete(node)
    await audit.record(db, "delete_node", name, f"id={node_id}", actor=admin.username)
    await db.commit()
