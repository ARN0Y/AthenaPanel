"""API key management.

Deliberately reachable ONLY with a session token, never with an API key. A key
that can mint keys is a key that can escalate itself past its own scopes and
outlive its own revocation, which defeats the point of having scopes and
revocation. Minting a credential is something a human does after logging in.

An admin manages their own keys. A superadmin can see everyone's, because
"which of my resellers has automation running" is an operator question — but
even a superadmin only ever sees prefixes, never a secret, because none of them
are stored.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import apikeys as keysvc
from .. import audit
from ..database import get_session
from ..deps import get_current_admin
from ..models import Admin, ApiKey
from ..schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyOut

log = logging.getLogger("vpn-panel.apikeys")

router = APIRouter(prefix="/api/api-keys", tags=["api-keys"])


def _out(key: ApiKey, owner: str) -> ApiKeyOut:
    return ApiKeyOut(
        id=key.id, name=key.name, prefix=key.prefix,
        scopes=sorted(key.scope_set), is_active=key.is_active,
        created_at=key.created_at, expires_at=key.expires_at,
        last_used_at=key.last_used_at, request_count=key.request_count or 0,
        rate_limit=key.rate_limit or 0, note=key.note or "", owner=owner,
    )


@router.get("/scopes")
async def list_scopes(_: Admin = Depends(get_current_admin)):
    """The scope vocabulary, so the UI and the docs cannot drift from the code."""
    return {
        "scopes": [{"name": n, "description": d} for n, d in keysvc.SCOPES.items()],
        "default_rate_limit_per_minute": keysvc.DEFAULT_RATE_LIMIT,
        "note": "A key with no scopes can do everything its owning admin can do.",
    }


@router.get("", response_model=list[ApiKeyOut])
async def list_keys(
    admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    stmt = select(ApiKey).order_by(ApiKey.id.desc())
    if not admin.is_superadmin:
        stmt = stmt.where(ApiKey.admin_id == admin.id)
    rows = (await db.execute(stmt)).scalars().all()
    owners = {a.id: a.username for a in (await db.execute(select(Admin))).scalars().all()}
    return [_out(k, owners.get(k.admin_id, "")) for k in rows]


@router.post("", response_model=ApiKeyCreated, status_code=201)
async def create_key(
    payload: ApiKeyCreate,
    admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    """Mint a key. The secret is in this response and nowhere else, ever."""
    unknown = [s for s in payload.scopes if s not in keysvc.SCOPES]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown scope(s): {', '.join(unknown)}. Valid: {', '.join(keysvc.SCOPES)}",
        )
    full, prefix, key_hash = keysvc.generate()
    key = ApiKey(
        admin_id=admin.id,
        name=payload.name.strip(),
        prefix=prefix,
        key_hash=key_hash,
        scopes=keysvc.normalize_scopes(payload.scopes),
        expires_at=payload.expires_at,
        rate_limit=payload.rate_limit,
        note=(payload.note or "").strip(),
    )
    db.add(key)
    await audit.record(db, "api_key_create", prefix,
                       f"name={key.name} scopes={key.scopes or '(all)'}", actor=admin.username)
    await db.commit()
    await db.refresh(key)
    log.info("api key %s created for %s (scopes=%s)", prefix, admin.username, key.scopes or "all")
    return ApiKeyCreated(**_out(key, admin.username).model_dump(), key=full)


@router.patch("/{key_id}", response_model=ApiKeyOut)
async def update_key(
    key_id: int,
    payload: ApiKeyCreate,
    admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    key = await _owned(db, admin, key_id)
    key.name = payload.name.strip()
    key.scopes = keysvc.normalize_scopes(payload.scopes)
    key.expires_at = payload.expires_at
    key.rate_limit = payload.rate_limit
    key.note = (payload.note or "").strip()
    await audit.record(db, "api_key_update", key.prefix,
                       f"scopes={key.scopes or '(all)'}", actor=admin.username)
    await db.commit()
    await db.refresh(key)
    owners = {a.id: a.username for a in (await db.execute(select(Admin))).scalars().all()}
    return _out(key, owners.get(key.admin_id, ""))


@router.post("/{key_id}/revoke", response_model=ApiKeyOut)
async def revoke_key(
    key_id: int,
    admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    """Switch a key off without deleting it, so the audit trail and the usage
    counters survive the incident that made you revoke it."""
    key = await _owned(db, admin, key_id)
    key.is_active = False
    await audit.record(db, "api_key_revoke", key.prefix, key.name, actor=admin.username)
    await db.commit()
    await db.refresh(key)
    return _out(key, admin.username)


@router.delete("/{key_id}")
async def delete_key(
    key_id: int,
    admin: Admin = Depends(get_current_admin),
    db: AsyncSession = Depends(get_session),
):
    key = await _owned(db, admin, key_id)
    prefix = key.prefix
    await db.delete(key)
    await audit.record(db, "api_key_delete", prefix, key.name, actor=admin.username)
    await db.commit()
    return {"deleted": prefix}


async def _owned(db: AsyncSession, admin: Admin, key_id: int) -> ApiKey:
    key = await db.get(ApiKey, key_id)
    if key is None or (not admin.is_superadmin and key.admin_id != admin.id):
        raise HTTPException(status_code=404, detail="No such API key")
    return key
