"""Login-page appearance.

The two GETs here are the only unauthenticated endpoints in the panel besides
login itself and the subscription link, because the sign-in screen has to draw
before anyone has a token. Everything they return is cosmetic by construction —
see branding.py. The writes are superadmin-only and live under /api/settings so
they sit with every other operator-level change.
"""

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from .. import appsettings, audit, branding
from ..database import get_session
from ..deps import require_superadmin
from ..models import Admin
from ..schemas import BrandingOut, BrandingUpdate

public = APIRouter(tags=["branding"])
router = APIRouter(prefix="/api/settings/branding", tags=["branding"])


@public.get("/api/branding", response_model=BrandingOut)
async def get_branding(db: AsyncSession = Depends(get_session)):
    """Public: what the sign-in screen should look like."""
    return BrandingOut(**branding.public_view(await appsettings.get_all(db)))


@public.get("/api/branding/image")
async def get_branding_image(db: AsyncSession = Depends(get_session)):
    """Public: the operator's artwork, or 404 to fall back to the built-in look."""
    path = branding.find_image()
    if path is None:
        raise HTTPException(status_code=404, detail="No login image set")
    values = await appsettings.get_all(db)
    return Response(
        content=path.read_bytes(),
        media_type=branding.content_type_for(path),
        headers={
            # Immutable against the version stamp: the URL carries ?v=<stamp>,
            # so a replaced image is a new URL and a long cache is safe.
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": f'"{values.get("login_image_version", "0")}"',
        },
    )


@router.put("", response_model=BrandingOut)
async def update_branding(
    payload: BrandingUpdate,
    me: Admin = Depends(require_superadmin),
    db: AsyncSession = Depends(get_session),
):
    changes = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if not changes:
        return BrandingOut(**branding.public_view(await appsettings.get_all(db)))

    if "login_layout" in changes and changes["login_layout"] not in branding.LAYOUTS:
        raise HTTPException(status_code=400, detail=f"layout must be one of {', '.join(branding.LAYOUTS)}")
    if "login_focal" in changes and changes["login_focal"] not in branding.FOCALS:
        raise HTTPException(status_code=400, detail=f"focal must be one of {', '.join(branding.FOCALS)}")
    if "login_overlay" in changes:
        changes["login_overlay"] = str(max(0, min(90, int(changes["login_overlay"]))))
    if "login_image_url" in changes:
        url = str(changes["login_image_url"]).strip()
        # An operator pasting a javascript: or data: URL here would have it
        # rendered on a page every one of their operators visits.
        if url and not url.startswith(("https://", "http://", "/")):
            raise HTTPException(status_code=400, detail="Image URL must start with https://, http:// or /")
        changes["login_image_url"] = url

    before = await appsettings.get_all(db)
    await appsettings.update(db, {k: str(v) for k, v in changes.items()})
    changed = [f"{k}: {before.get(k, '')!r} → {v!r}" for k, v in changes.items() if before.get(k) != str(v)]
    if changed:
        await audit.record(db, "update_branding", "login page", "; ".join(changed), actor=me.username)
        await db.commit()
    return BrandingOut(**branding.public_view(await appsettings.get_all(db)))


@router.post("/image", response_model=BrandingOut, status_code=status.HTTP_201_CREATED)
async def upload_branding_image(
    file: UploadFile = File(...),
    me: Admin = Depends(require_superadmin),
    db: AsyncSession = Depends(get_session),
):
    blob = await file.read()
    try:
        ext = branding.validate(file.content_type or "", blob)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        stamp = branding.save(blob, ext)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not write the image: {exc}") from exc

    await appsettings.update(db, {"login_image_version": stamp})
    await audit.record(
        db, "update_branding", "login image",
        f"uploaded {file.filename!r}, {len(blob) // 1024} KB, {ext}", actor=me.username,
    )
    await db.commit()
    return BrandingOut(**branding.public_view(await appsettings.get_all(db)))


@router.delete("/image", response_model=BrandingOut)
async def delete_branding_image(
    me: Admin = Depends(require_superadmin),
    db: AsyncSession = Depends(get_session),
):
    if branding.remove():
        await appsettings.update(db, {"login_image_version": "0"})
        await audit.record(db, "update_branding", "login image", "removed", actor=me.username)
        await db.commit()
    return BrandingOut(**branding.public_view(await appsettings.get_all(db)))
