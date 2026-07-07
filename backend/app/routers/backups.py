"""Database backup management — superadmin only.

Exposes list / create-now / download / delete. A backup file contains the whole
panel DB (including secrets), so every route is gated on superadmin and served
only over the authenticated API.
"""

import os

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from .. import appsettings, audit, backups, telegram
from ..config import settings
from ..database import get_session
from ..deps import get_current_admin, require_superadmin
from ..models import Admin

router = APIRouter(prefix="/api/backups", tags=["backups"], dependencies=[Depends(require_superadmin)])


@router.get("")
async def list_backups():
    return {"backups": backups.list_backups(), "keep": backups.KEEP, "dir": backups.BACKUP_DIR}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_backup(
    admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    try:
        info = await backups.create_backup()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Backup failed: {e}")
    await audit.record(db, "backup_create", info["name"], actor=admin.username)
    await db.commit()
    return info


@router.get("/{name}/download")
async def download_backup(name: str):
    path = backups.safe_path(name)
    if not path:
        raise HTTPException(status_code=404, detail="Backup not found")
    return FileResponse(path, filename=name, media_type="application/octet-stream")


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_backup(
    name: str,
    admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    if not backups.delete_backup(name):
        raise HTTPException(status_code=404, detail="Backup not found")
    await audit.record(db, "backup_delete", name, actor=admin.username)
    await db.commit()
    return None


async def _tg_creds(db: AsyncSession) -> tuple[str, str]:
    aps = await appsettings.get_all(db)
    return (aps.get("tg_bot_token") or settings.tg_bot_token).strip(), (aps.get("tg_chat_id") or settings.tg_chat_id).strip()


@router.post("/telegram/test")
async def telegram_test(admin: Admin = Depends(get_current_admin), db: AsyncSession = Depends(get_session)):
    """Resolve the chat (operator must have /start'd the bot once), persist it,
    and send a confirmation message."""
    token, chat = await _tg_creds(db)
    if not token:
        raise HTTPException(status_code=400, detail="No Telegram bot token set")
    if not chat:
        chat = await telegram.resolve_chat_id(token)
        if not chat:
            raise HTTPException(status_code=400, detail="Send /start to the bot in Telegram, then retry")
        await appsettings.update(db, {"tg_chat_id": chat})
    if not await telegram.send_message(token, chat, "✅ VPN panel linked. Database backups will be delivered here."):
        raise HTTPException(status_code=400, detail="Telegram send failed — check the token")
    return {"linked": True, "chat_id": chat}


@router.post("/{name}/telegram")
async def telegram_send(name: str, admin: Admin = Depends(get_current_admin), db: AsyncSession = Depends(get_session)):
    path = backups.safe_path(name)
    if not path:
        raise HTTPException(status_code=404, detail="Backup not found")
    size = os.path.getsize(path)
    token, chat = await _tg_creds(db)
    if not chat and token:
        chat = await telegram.resolve_chat_id(token) or ""
        if chat:
            await appsettings.update(db, {"tg_chat_id": chat})
    if not token or not chat:
        raise HTTPException(status_code=400, detail="Telegram not configured (set token + /start the bot)")

    # Over the 50MB cap -> send an essential dump (no usage_samples data) instead
    # of failing, so the operator still gets an off-site copy of what matters.
    tmp = None
    essential = size > telegram.TG_DOC_LIMIT
    try:
        if essential:
            tmp = await backups.create_essential_dump()
            send_path = tmp
            caption = f"🔐 VPN panel backup — essential ({size // 1024 // 1024}MB full was over the 50MB cap)\n{name}"
        else:
            send_path = path
            caption = f"🔐 VPN panel backup\n{name}"
        ok, out = await telegram.send_document(token, chat, send_path, caption=caption)
        if not ok:
            raise HTTPException(status_code=400, detail=f"Telegram send failed: {out[:180]}")
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
    await audit.record(db, "backup_telegram", name, actor=admin.username)
    await db.commit()
    return {"sent": True, "essential": essential}
