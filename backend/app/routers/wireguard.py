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

from .. import appsettings, audit, wireguard
from ..config import settings
from ..database import get_session
from ..deps import get_current_admin
from ..models import Admin, User, WgPeer

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
    await wireguard.add_peer(pub, psk, address)  # apply to the live interface
    return _out(peer)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disable(user_id: int, admin: Admin = Depends(get_current_admin), db: AsyncSession = Depends(get_session)):
    user = await _owned_user(db, admin, user_id)
    peer = await _peer_for(db, user_id)
    if peer:
        await wireguard.remove_peer(peer.public_key)
        await db.delete(peer)
        await audit.record(db, "wg_disable", user.username, actor=admin.username)
        await db.commit()
    return None


@router.get("/{user_id}/config")
async def get_config(user_id: int, admin: Admin = Depends(get_current_admin), db: AsyncSession = Depends(get_session)):
    user = await _owned_user(db, admin, user_id)
    peer = await _peer_for(db, user_id)
    if not peer:
        raise HTTPException(status_code=404, detail="WireGuard not enabled for this user")

    aps = await appsettings.get_all(db)
    endpoint = (aps.get("wg_endpoint") or settings.wg_endpoint).strip()
    server_pub = (aps.get("wg_server_pubkey") or settings.wg_server_pubkey).strip() or await wireguard.server_pubkey()
    dns = aps.get("wg_dns") or settings.wg_dns
    if not endpoint:
        raise HTTPException(status_code=400, detail="WG endpoint not set — Settings: set the relay host:port")
    if not server_pub:
        raise HTTPException(status_code=400, detail="WG server public key unavailable")

    conf = wireguard.client_config(
        private_key=peer.private_key, address=peer.address, server_pub=server_pub,
        preshared_key=peer.preshared_key, endpoint=endpoint, dns=dns, mtu=settings.wg_mtu,
    )
    return {"config": conf, "qr_svg": wireguard.qr_svg(conf), "address": peer.address,
            "filename": f"{user.username}.conf"}
