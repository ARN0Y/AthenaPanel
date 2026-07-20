"""Settings endpoint: server / network info + editable client-facing profile."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from .. import appsettings, audit, outbound
from ..config import settings
from ..database import get_session
from ..deps import require_admin, require_superadmin
from ..models import Admin
from ..schemas import PanelSettingsUpdate, SettingsOut

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
    """Live status of each egress outbound (direct / warp) for the Outbounds tab."""
    return await outbound.status(db)
