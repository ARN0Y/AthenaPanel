"""Login-page appearance.

The two GETs under /api/branding are the only unauthenticated endpoints in the
panel besides login itself and the subscription link, because the sign-in screen
has to draw before anyone has a token. Everything they return is cosmetic by
construction — see branding.py. The writes are superadmin-only and live under
/api/settings so they sit with every other operator-level change.

**None of this writes to the audit log.** The log exists so an operator can see
who changed something that matters — a quota, an owner, a protocol endpoint.
Wallpapers and dim sliders are not that, and burying those entries under a
stream of cosmetic edits makes the log worse at its actual job.
"""

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from .. import appsettings, branding
from ..database import get_session
from ..deps import require_superadmin
from ..models import Admin
from ..schemas import BrandingImage, BrandingOut, BrandingUpdate

public = APIRouter(tags=["branding"])
router = APIRouter(prefix="/api/settings/branding", tags=["branding"])


async def _view(db: AsyncSession) -> BrandingOut:
    return BrandingOut(**branding.public_view(await appsettings.get_all(db)))


@public.get("/api/branding", response_model=BrandingOut)
async def get_branding(db: AsyncSession = Depends(get_session)):
    """Public: what the sign-in screen should look like."""
    return await _view(db)


@public.get("/api/branding/image")
async def get_branding_image(id: str = "", db: AsyncSession = Depends(get_session)):
    """Public: an image from the library.

    Without `id`, serves whichever one is active — that is what the login page
    asks for. With `id`, serves that one, which is how the settings gallery
    shows thumbnails of images that are not currently in use.
    """
    image_id = id
    if not image_id:
        values = await appsettings.get_all(db)
        image_id = (values.get("login_image_id") or "").strip()
    if not image_id:
        raise HTTPException(status_code=404, detail="No login image set")

    path = branding.path_for(image_id)
    if path is None:
        raise HTTPException(status_code=404, detail="No such image")
    return Response(
        content=path.read_bytes(),
        media_type=branding.content_type_for(path),
        headers={
            # The id IS the content hash, so a given id can never mean different
            # bytes and a long cache is safe.
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": f'"{image_id}"',
        },
    )


@router.get("/images", response_model=list[BrandingImage])
async def list_images(
    _: Admin = Depends(require_superadmin),
    db: AsyncSession = Depends(get_session),
):
    """The image library, newest first, with the active one flagged."""
    values = await appsettings.get_all(db)
    active = (values.get("login_image_id") or "").strip()
    return [BrandingImage(**row, active=row["id"] == active) for row in branding.library()]


@router.put("", response_model=BrandingOut)
async def update_branding(
    payload: BrandingUpdate,
    _: Admin = Depends(require_superadmin),
    db: AsyncSession = Depends(get_session),
):
    changes = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if not changes:
        return await _view(db)

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
    if "login_image_id" in changes:
        image_id = str(changes["login_image_id"]).strip()
        if image_id and branding.path_for(image_id) is None:
            raise HTTPException(status_code=404, detail="No such image in the library")
        changes["login_image_id"] = image_id

    await appsettings.update(db, {k: str(v) for k, v in changes.items()})
    return await _view(db)


@router.post("/images", response_model=BrandingOut, status_code=status.HTTP_201_CREATED)
async def upload_image(
    file: UploadFile = File(...),
    activate: bool = True,
    _: Admin = Depends(require_superadmin),
    db: AsyncSession = Depends(get_session),
):
    """Add an image to the library and, by default, show it."""
    blob = await file.read()
    try:
        ext = branding.validate(file.content_type or "", blob)
        image_id = branding.save(blob, ext)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not write the image: {exc}") from exc

    if activate:
        await appsettings.update(db, {"login_image_id": image_id})
    return await _view(db)


@router.delete("/images/{image_id}", response_model=BrandingOut)
async def delete_image(
    image_id: str,
    _: Admin = Depends(require_superadmin),
    db: AsyncSession = Depends(get_session),
):
    if not branding.remove(image_id):
        raise HTTPException(status_code=404, detail="No such image")
    # Deleting the one on show leaves the login page pointing at nothing, so
    # fall back to whatever else the library holds rather than to a blank.
    values = await appsettings.get_all(db)
    if (values.get("login_image_id") or "").strip() == image_id:
        remaining = branding.library()
        await appsettings.update(db, {"login_image_id": remaining[0]["id"] if remaining else ""})
    return await _view(db)
