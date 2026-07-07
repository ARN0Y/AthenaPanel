"""FastAPI application entrypoint."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from . import chap_secrets, outbound, wireguard
from .config import settings
from .database import AsyncSessionLocal, init_db
from .models import Admin, AppSetting, User, WgPeer
from .models import Session as SessionRow
from .routers import (
    admins,
    auth,
    backups as backups_router,
    events,
    internal,
    sessions,
    settings as settings_router,
    stats,
    sub as sub_router,
    system,
    traffic,
    users,
    wireguard as wireguard_router,
)
from .security import hash_password
from .tasks import _snapshot_once, backup_scheduler, quota_enforcer, snapshot_sampler, traffic_sampler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("vpn-panel")


async def _seed_superadmin() -> None:
    """Create the bootstrap superadmin from .env if no admins exist."""
    async with AsyncSessionLocal() as db:
        count = (await db.execute(select(func.count(Admin.id)))).scalar_one()
        if count == 0:
            db.add(Admin(
                username=settings.admin_username,
                password_hash=hash_password(settings.admin_password),
                role="superadmin",
                can_create_users=True,
                max_users=0,
            ))
            await db.commit()
            log.info("seeded superadmin '%s' from .env", settings.admin_username)


async def _import_existing_users() -> None:
    pairs = chap_secrets.parse_existing(settings.chap_secrets)
    if not pairs:
        return
    async with AsyncSessionLocal() as db:
        for username, password in pairs:
            exists = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
            if exists:
                continue
            db.add(User(username=username, password_hash=password, note="imported"))
            log.info("imported existing chap-secrets user: %s", username)
        await db.commit()
        await chap_secrets.rewrite(db)


async def _reconcile_accounting_v2() -> None:
    """One-time switch to self-healing accounting (idempotent, flag-guarded).

    Old `used_bytes` was an accumulator that already included open sessions'
    committed bytes. The new model treats `used_bytes` as CLOSED-session bytes
    only and overlays each open session's live counter (billing base 0). So we
    subtract each user's open-session committed bytes (last_rx+last_tx) from
    used_bytes and clamp at 0 — which also corrects users whose accumulator had
    drifted BELOW their live counters (e.g. Mgh1012). No data is lost: the live
    overlay re-adds the open sessions from the authoritative kernel counter.
    """
    async with AsyncSessionLocal() as db:
        if await db.get(AppSetting, "accounting_v2_migrated") is not None:
            return
        rows = (await db.execute(select(SessionRow))).scalars().all()
        committed_open: dict[str, int] = {}
        for r in rows:
            committed_open[r.username] = (
                committed_open.get(r.username, 0) + (r.last_rx or 0) + (r.last_tx or 0)
            )
        users = (await db.execute(select(User))).scalars().all()
        for u in users:
            u.used_bytes = max(0, (u.used_bytes or 0) - committed_open.get(u.username, 0))
        db.add(AppSetting(key="accounting_v2_migrated", value="1"))
        await db.commit()
        log.info("accounting v2 reconciliation: re-baselined %d users", len(users))


async def _sync_wireguard() -> None:
    """Re-apply WG peers from the DB to the live wg-panel interface on startup
    (a server/panel restart otherwise loses runtime peers). Peers of
    disabled/expired/over-quota users are removed; the enforcer keeps it in sync."""
    if not wireguard.iface_up():
        return
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(WgPeer, User).join(User, User.id == WgPeer.user_id))).all()
    peers = [(p.public_key, p.preshared_key, p.address, bool(p.enabled and u.enabled_for_auth)) for p, u in rows]
    if peers:
        n = await wireguard.sync_from_db(peers)
        log.info("wireguard: synced %d/%d peers to %s", n, len(peers), wireguard.IFACE)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    try:
        await _seed_superadmin()
        await _import_existing_users()
        await _reconcile_accounting_v2()
        await _sync_wireguard()
    except Exception:  # noqa: BLE001
        log.exception("startup seed/import failed")

    # Warm the live snapshot once so the first requests after a restart aren't
    # empty (the API serves sessions/usage from this cache, not from sysfs).
    try:
        await _snapshot_once()
    except Exception:  # noqa: BLE001
        log.exception("initial snapshot failed")

    # Re-apply WARP outbound mappings for sessions that survived the restart.
    try:
        async with AsyncSessionLocal() as db:
            await outbound.reconcile(db)
    except Exception:  # noqa: BLE001
        log.exception("initial outbound reconcile failed")

    tasks = [
        asyncio.create_task(quota_enforcer()),
        asyncio.create_task(traffic_sampler()),
        asyncio.create_task(snapshot_sampler()),
        asyncio.create_task(backup_scheduler()),
    ]
    log.info("VPN panel backend started")
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        log.info("VPN panel backend stopped")


app = FastAPI(title="VPN Panel API", version="3.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (
    auth.router,
    admins.router,
    users.router,
    sessions.router,
    stats.router,
    system.router,
    traffic.router,
    events.router,
    settings_router.router,
    backups_router.router,
    wireguard_router.router,
    sub_router.router,
    internal.router,
):
    app.include_router(r)


@app.get("/api/ping")
async def ping():
    return {"pong": True}
