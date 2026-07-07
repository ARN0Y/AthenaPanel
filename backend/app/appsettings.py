"""Editable app settings: DB key/value overriding .env defaults.

Used for the client-facing profile (server address, SSTP address) and protocol
toggles, so a superadmin can change them from the panel without redeploying.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .models import AppSetting

# editable key -> default (from .env / config)
DEFAULTS: dict[str, str] = {
    "server_address": settings.server_address,
    "sstp_address": settings.sstp_address,
    "sub_address": settings.sub_address,
    "l2tp_enabled": "1" if settings.l2tp_enabled else "0",
    "sstp_enabled": "1" if settings.sstp_enabled else "0",
    # WireGuard (3rd protocol)
    "wg_enabled": "1" if settings.wg_enabled else "0",
    "wg_endpoint": settings.wg_endpoint,
    "wg_server_pubkey": settings.wg_server_pubkey,
    "wg_dns": settings.wg_dns,
    # Telegram backup bot
    "tg_bot_token": settings.tg_bot_token,
    "tg_chat_id": settings.tg_chat_id,
    "tg_backup_enabled": "1" if settings.tg_backup_enabled else "0",
}

_BOOL_KEYS = {"l2tp_enabled", "sstp_enabled", "wg_enabled", "tg_backup_enabled"}


async def get_all(db: AsyncSession) -> dict[str, str]:
    """Return every editable setting, DB value overriding the .env default."""
    merged = dict(DEFAULTS)
    rows = (await db.execute(select(AppSetting))).scalars().all()
    for row in rows:
        if row.key in DEFAULTS:
            merged[row.key] = row.value
    return merged


async def update(db: AsyncSession, changes: dict[str, str]) -> dict[str, str]:
    """Upsert the provided keys (only known keys), then return the merged set."""
    for key, value in changes.items():
        if key not in DEFAULTS or value is None:
            continue
        val = value
        if key in _BOOL_KEYS:
            val = "1" if str(value) in ("1", "true", "True", "on", "yes") else "0"
        existing = await db.get(AppSetting, key)
        if existing:
            existing.value = str(val)
        else:
            db.add(AppSetting(key=key, value=str(val)))
    await db.commit()
    return await get_all(db)


def as_bool(value: str) -> bool:
    return str(value) in ("1", "true", "True", "on", "yes")
