"""Settings endpoint: server / network info + editable client-facing profile."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import appsettings, audit, outbound
from ..config import settings
from ..database import get_session
from ..deps import require_admin, require_superadmin
from ..models import Admin, Outbound, User
from ..schemas import (
    OutboundCreate,
    OutboundRegister,
    OutboundUpdate,
    PanelSettingsUpdate,
    SettingsOut,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=SettingsOut)
async def get_settings_info(
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_session),
):
    """Client-facing profile settings for any admin; node internals redacted.

    A reseller genuinely needs the endpoints + PSK — that is what they hand to
    their customers. They must NOT learn the operator's login name, the PPP pool
    layout, the WAN interface or on-disk paths, so those fields come back empty
    for them rather than being served to every authenticated caller.
    """
    editable = await appsettings.get_all(db)
    node = admin.is_superadmin
    return SettingsOut(
        vpn_psk=settings.vpn_psk,
        wan_iface=settings.wan_iface if node else "",
        ppp_local_ip=settings.ppp_local_ip if node else "",
        ppp_pool=settings.ppp_pool if node else "",
        admin_username=settings.admin_username if node else "",
        chap_secrets=settings.chap_secrets if node else "",
        server_address=editable["server_address"],
        sstp_address=editable["sstp_address"],
        sub_address=editable["sub_address"],
        l2tp_raw_address=editable["l2tp_raw_address"],
        l2tp_enabled=appsettings.as_bool(editable["l2tp_enabled"]),
        sstp_enabled=appsettings.as_bool(editable["sstp_enabled"]),
    )


@router.put("", response_model=SettingsOut)
async def update_settings(
    payload: PanelSettingsUpdate,
    me: Admin = Depends(require_superadmin),
    db: AsyncSession = Depends(get_session),
):
    changes = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    await _validate_endpoints(db, changes)
    before = await appsettings.get_all(db)
    await appsettings.update(db, changes)
    changed = [f"{k}: {before.get(k, '')!r} → {v!r}" for k, v in changes.items() if before.get(k) != v]
    if changed:
        await audit.record(db, "update_settings", "panel", "; ".join(changed), actor=me.username)
        await db.commit()
    return await get_settings_info(me, db)


def _host(value: str) -> str:
    return (value or "").strip().strip("/").lower()


async def _validate_endpoints(db: AsyncSession, changes: dict) -> None:
    """The raw-L2TP entry MUST be a different host from the IPsec one.

    IPsec is negotiated before the user is known, so the two modes cannot share
    an endpoint: pointing raw users at the IPsec host makes Libreswan's per-client
    xfrm policies drop their plain udp/1701 packets with no visible error. Reject
    the save instead of letting it fail silently on the wire. Merged against the
    stored values so it also catches changing only ONE of the two fields.
    """
    if "l2tp_raw_address" not in changes and "server_address" not in changes:
        return
    current = await appsettings.get_all(db)
    raw = _host(changes.get("l2tp_raw_address", current["l2tp_raw_address"]))
    main = _host(changes.get("server_address", current["server_address"]))
    if raw and raw == main:
        raise HTTPException(
            status_code=400,
            detail="The L2TP raw address must differ from the L2TP/IPsec address — "
                   "raw mode needs its own entry host.",
        )


# Exposes the node's real egress IPs and platform-wide per-outbound user
# counts -> superadmin only.
@router.get("/outbounds", dependencies=[Depends(require_superadmin)])
async def list_outbounds(db: AsyncSession = Depends(get_session)):
    """Live status of every egress outbound for the Outbounds tab."""
    return await outbound.status(db)


@router.post("/outbounds", dependencies=[Depends(require_superadmin)])
async def create_outbound(
    payload: OutboundCreate,
    admin: Admin = Depends(require_superadmin),
    db: AsyncSession = Depends(get_session),
):
    """Reserve a location and return the command to run on its server.

    Two steps, because the panel must own the addressing: two locations that
    each picked their own tunnel subnet would collide here, where all of them
    terminate side by side. So the panel allocates first — mark, table, rule
    priority, /30 and this end's keypair — and hands back a command with those
    baked in. Nothing is plumbed yet; the row is a reservation until the remote
    server answers with its public key.
    """
    name = (payload.name or "").strip().lower()
    if not outbound.valid_name(name):
        raise HTTPException(
            status_code=400,
            detail="Name must be 2-12 characters of a-z, 0-9 or '-', and not "
                   "'direct' or 'warp'.",
        )
    if (await db.execute(select(Outbound).where(Outbound.name == name))).scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"An outbound named '{name}' already exists.")

    mark, table, prio, address, peer_address = await outbound.allocate(db)
    private_key, public_key = outbound.generate_keypair()
    psk = outbound.generate_psk()

    country = (payload.country or "").strip().lower()
    if country and (len(country) != 2 or not country.isalpha()):
        raise HTTPException(status_code=400, detail="Country must be a two-letter code, or empty.")

    ob = Outbound(
        name=name,
        country=country,
        note=(payload.note or "").strip(),
        private_key=private_key,
        preshared_key=psk,
        address=address,
        peer_address=peer_address,
        mtu=payload.mtu or 1380,
        fwmark=mark,
        table_id=table,
        rule_priority=prio,
        enabled=False,  # a reservation until the remote registers
    )
    db.add(ob)
    await audit.record(db, "outbound_create", name, actor=admin.username)
    await db.commit()
    await db.refresh(ob)

    return {
        "name": ob.name,
        "install_command": outbound.install_command(ob, public_key, payload.port or 51833),
        "expects": "Run that on the egress server, then POST its output to /register.",
    }


@router.post("/outbounds/{name}/register", dependencies=[Depends(require_superadmin)])
async def register_outbound(
    name: str,
    payload: OutboundRegister,
    admin: Admin = Depends(require_superadmin),
    db: AsyncSession = Depends(get_session),
):
    """Complete a reservation with the line athena-outbound.sh printed, then
    bring the tunnel up. Re-registering is allowed and is how you point an
    existing location at a rebuilt server."""
    ob = (await db.execute(select(Outbound).where(Outbound.name == name))).scalar_one_or_none()
    if ob is None:
        raise HTTPException(status_code=404, detail="No such outbound.")

    parsed = outbound.parse_registration(payload.registration)
    if parsed is None:
        raise HTTPException(
            status_code=400,
            detail="That does not look like the line the script printed. It should "
                   "read athena-ob:<address>:<port>:<public key>.",
        )
    host, port, public_key = parsed
    ob.endpoint = f"{host}:{port}"
    ob.public_key = public_key
    ob.enabled = True
    await db.commit()
    await db.refresh(ob)

    ok, out = await outbound.plumb_up(ob)
    if not ok:
        # Keep the row: the registration is good, the host is not. Deleting it
        # would throw away the keypair the remote server is already configured
        # with, forcing the operator to redo the far end too.
        ob.enabled = False
        ob.last_status = "error"
        await db.commit()
        raise HTTPException(
            status_code=500,
            detail=f"Registered, but the tunnel would not come up: {out.strip()[-300:]}",
        )
    await outbound.refresh_known(db)
    await outbound.reconcile(db)
    await audit.record(db, "outbound_register", name, ob.endpoint, actor=admin.username)
    await db.commit()
    return {"ok": True, "name": ob.name, "endpoint": ob.endpoint}


@router.patch("/outbounds/{name}", dependencies=[Depends(require_superadmin)])
async def update_outbound(
    name: str,
    payload: OutboundUpdate,
    admin: Admin = Depends(require_superadmin),
    db: AsyncSession = Depends(get_session),
):
    """Rename an outbound and/or change its flag.

    The flag is cosmetic and free. A rename is not: the name IS the interface,
    the ipset and the config filename, so the old plumbing has to come down and
    the new go up, and every user pointing at the old name has to move with it —
    in the same transaction, or a crash in between would strand them on a name
    that no longer exists.

    Its users fall back to direct for the second the tunnel is being rebuilt.
    That is the same behaviour as the location briefly going down, which they
    are already built to survive.
    """
    ob = (await db.execute(select(Outbound).where(Outbound.name == name))).scalar_one_or_none()
    if ob is None:
        raise HTTPException(status_code=404, detail="No such outbound.")

    if payload.country is not None:
        country = payload.country.strip().lower()
        if country and (len(country) != 2 or not country.isalpha()):
            raise HTTPException(status_code=400, detail="Country must be a two-letter code, or empty.")
        ob.country = country

    new_name = (payload.name or "").strip().lower()
    renamed = bool(new_name) and new_name != ob.name
    if renamed:
        if not outbound.valid_name(new_name):
            raise HTTPException(
                status_code=400,
                detail="Name must be 2-12 characters of a-z, 0-9 or '-', and not "
                       "'direct' or 'warp'.",
            )
        if (await db.execute(select(Outbound).where(Outbound.name == new_name))).scalar_one_or_none():
            raise HTTPException(status_code=409, detail=f"An outbound named '{new_name}' already exists.")

    old_name = ob.name
    if renamed:
        await db.execute(
            update(User).where(User.outbound == old_name).values(outbound=new_name)
        )
        ob.name = new_name
    await audit.record(
        db, "outbound_update", old_name,
        f"-> {new_name}" if renamed else f"country={ob.country or '-'}",
        actor=admin.username,
    )
    await db.commit()
    await db.refresh(ob)

    if renamed:
        await outbound.plumb_down(old_name)
        if ob.enabled and ob.public_key:
            ok, out = await outbound.plumb_up(ob)
            if not ok:
                ob.enabled = False
                ob.last_status = "error"
                await db.commit()
                raise HTTPException(
                    status_code=500,
                    detail=f"Renamed, but the tunnel would not come back up: {out.strip()[-300:]}",
                )
    await outbound.refresh_known(db)
    await outbound.reconcile(db)
    return {"ok": True, "name": ob.name, "country": ob.country, "renamed": renamed}


@router.delete("/outbounds/{name}", dependencies=[Depends(require_superadmin)])
async def delete_outbound(
    name: str,
    admin: Admin = Depends(require_superadmin),
    db: AsyncSession = Depends(get_session),
):
    """Tear a location down and move its users back to direct.

    The users are moved explicitly rather than left pointing at a name that no
    longer resolves. normalize() would already treat them as direct, but a
    stored value that disagrees with what is happening is the kind of thing that
    wastes an hour six months from now.
    """
    ob = (await db.execute(select(Outbound).where(Outbound.name == name))).scalar_one_or_none()
    if ob is None:
        raise HTTPException(status_code=404, detail="No such outbound.")

    moved = (
        await db.execute(
            update(User).where(User.outbound == name).values(outbound=outbound.DIRECT)
        )
    ).rowcount or 0
    await db.delete(ob)
    await audit.record(db, "outbound_delete", name, f"{moved} user(s) -> direct", actor=admin.username)
    await db.commit()

    await outbound.plumb_down(name)
    await outbound.refresh_known(db)
    await outbound.reconcile(db)
    return {"ok": True, "moved_to_direct": moved}
