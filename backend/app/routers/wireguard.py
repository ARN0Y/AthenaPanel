"""WireGuard provisioning per user — enable/disable + client config/QR.

RBAC: an admin may only manage WireGuard for users they own (superadmin: all).
The user's quota/expiry/rate/active flags are shared across L2TP/SSTP/WG; this
just manages the WG credential + live peer.
"""

import time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import appsettings, audit, nodes as nodes_mod, wireguard
from ..config import settings
from ..database import get_session
from ..deps import get_current_admin
from ..models import LOCAL_NODE_ID, Admin, Node, User, WgPeer

router = APIRouter(prefix="/api/wireguard", tags=["wireguard"])


async def _owned_user(db: AsyncSession, admin: Admin, user_id: int) -> User:
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not (admin.is_superadmin or user.created_by_admin_id == admin.id):
        raise HTTPException(status_code=403, detail="Not your user")
    return user


async def _peer_for(db: AsyncSession, user_id: int) -> WgPeer | None:
    return (await db.execute(select(WgPeer).where(WgPeer.user_id == user_id))).scalar_one_or_none()


async def _online(peer: WgPeer) -> bool:
    hs = (await wireguard.show_dump()).get(peer.public_key, {}).get("handshake", 0)
    return hs > 0 and (time.time() - hs) < 180


def _out(peer: WgPeer, online: bool = False) -> dict:
    return {
        "enabled": True,
        "user_id": peer.user_id,
        "public_key": peer.public_key,
        "address": peer.address,
        "online": online,
        "created_at": peer.created_at,
    }


@router.get("/{user_id}")
async def get_peer(user_id: int, admin: Admin = Depends(get_current_admin), db: AsyncSession = Depends(get_session)):
    await _owned_user(db, admin, user_id)
    peer = await _peer_for(db, user_id)
    if not peer:
        return {"enabled": False}
    return _out(peer, await _online(peer))


@router.post("/{user_id}/enable", status_code=status.HTTP_201_CREATED)
async def enable(user_id: int, admin: Admin = Depends(get_current_admin), db: AsyncSession = Depends(get_session)):
    user = await _owned_user(db, admin, user_id)
    existing = await _peer_for(db, user_id)
    if existing:
        return _out(existing, await _online(existing))

    username, uid = user.username, user.id
    priv, pub = await wireguard.gen_keypair()
    psk = await wireguard.gen_psk()

    # Allocate the /32 + insert with a bounded retry. user_id/address/public_key
    # are all UNIQUE, so two concurrent enables can race: if another request won
    # for THIS user we return its peer; if only the address collided we simply
    # re-allocate the next free one. This turns a would-be 500 into a correct,
    # idempotent result under concurrency.
    peer = address = None
    for _ in range(5):
        used = {a for (a,) in (await db.execute(select(WgPeer.address))).all()}
        address = wireguard.allocate_address(used)
        peer = WgPeer(user_id=uid, public_key=pub, private_key=priv,
                      preshared_key=psk, address=address, enabled=True)
        db.add(peer)
        try:
            await db.flush()
            break
        except IntegrityError:
            await db.rollback()
            existing = await _peer_for(db, uid)
            if existing:  # another request enabled WG for this user first
                return _out(existing, await _online(existing))
            peer = None   # address collided -> loop re-allocates a fresh one
    if peer is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="WireGuard address allocation is busy, please retry")

    await audit.record(db, "wg_enable", username, actor=admin.username)
    await db.commit()
    await db.refresh(peer)
    await _apply_peer(db, user, pub, psk, address)
    return _out(peer)


async def _apply_peer(db: AsyncSession, user: User, pub: str, psk: str, address: str) -> None:
    """Put a new peer where its owner is actually served.

    On node 1 that is this kernel. On any other node it is a message to the
    hub, which is a separate process — so the channel is the same resync
    timestamp every other account change uses. Adding it locally as well would
    put the same key on two servers, and the customer would connect to whichever
    answered first while only one of them billed.
    """
    node_id = user.node_id or LOCAL_NODE_ID
    if node_id == LOCAL_NODE_ID:
        await wireguard.add_peer(pub, psk, address)
        return
    from .. import nodecredit

    await nodecredit.touch_sync_needed(node_id)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disable(user_id: int, admin: Admin = Depends(get_current_admin), db: AsyncSession = Depends(get_session)):
    user = await _owned_user(db, admin, user_id)
    peer = await _peer_for(db, user_id)
    if peer:
        # Removed here unconditionally: even for a user served elsewhere the key
        # may still be on this interface from before they were moved, and a
        # revoked key left behind keeps working.
        await wireguard.remove_peer(peer.public_key)
        await db.delete(peer)
        await audit.record(db, "wg_disable", user.username, actor=admin.username)
        await db.commit()
        node_id = user.node_id or LOCAL_NODE_ID
        if node_id != LOCAL_NODE_ID:
            from .. import nodecredit

            await nodecredit.touch_sync_needed(node_id)
    return None


@router.get("/{user_id}/config")
async def get_config(user_id: int, admin: Admin = Depends(get_current_admin), db: AsyncSession = Depends(get_session)):
    user = await _owned_user(db, admin, user_id)
    peer = await _peer_for(db, user_id)
    if not peer:
        raise HTTPException(status_code=404, detail="WireGuard not enabled for this user")

    aps = await appsettings.get_all(db)
    dns = aps.get("wg_dns") or settings.wg_dns

    # The endpoint AND the server key must both describe the machine this user
    # actually connects to. Mixing them — the node's endpoint with the master's
    # key — produces a config that looks completely correct, resolves, sends
    # handshake initiations, and never gets a reply.
    node = await db.get(Node, user.node_id or LOCAL_NODE_ID)
    endpoint = nodes_mod.effective_endpoints(node, aps)["wg"]
    if node is not None and not node.is_local:
        server_pub = (node.wg_public_key or "").strip()
        if not server_pub:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Node '{node.name}' has not reported a WireGuard key yet — "
                    "its agent is offline, or it was bootstrapped without WireGuard."
                ),
            )
    else:
        server_pub = (
            (aps.get("wg_server_pubkey") or settings.wg_server_pubkey).strip()
            or await wireguard.server_pubkey()
        )
    if not endpoint:
        if node is not None and not node.is_local:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Node '{node.name}' has no WireGuard external proxy set. "
                    "Set it on the Nodes page — the panel-wide address points at "
                    "the master and would send this customer to the wrong server."
                ),
            )
        raise HTTPException(status_code=400, detail="WG endpoint not set — Settings: set the relay host:port")
    if not server_pub:
        raise HTTPException(status_code=400, detail="WG server public key unavailable")

    conf = wireguard.client_config(
        private_key=peer.private_key, address=peer.address, server_pub=server_pub,
        preshared_key=peer.preshared_key, endpoint=endpoint, dns=dns, mtu=settings.wg_mtu,
    )
    return {"config": conf, "qr_svg": wireguard.qr_svg(conf), "address": peer.address,
            "filename": f"{user.username}.conf"}
