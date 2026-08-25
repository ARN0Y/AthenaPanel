"""Login-page appearance: cosmetic settings plus the operator's own artwork.

Two things make this different from the rest of `appsettings`:

* **It is read before anyone is authenticated.** The login page needs it to
  render, so `GET /api/branding` is public. Everything here is therefore
  deliberately cosmetic — no address, no protocol state, no operator name. Add
  a key here only if you would be happy printing it on a billboard.
* **Images are files, not values.** Wallpapers run to several megabytes, so
  storing them as base64 columns would bloat every settings read and every
  nightly dump. They live on disk and are streamed by their own endpoint.

Uploads are kept as a **library**, not a single slot. An operator trying two
wallpapers should be able to go back to the first one by clicking it, not by
finding the file again and re-uploading it. Files are content-addressed —
the id is the first 16 hex of the sha256 — so the same image uploaded twice is
one file, and the id doubles as a perfect cache key.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

# Cosmetic keys, merged into appsettings.DEFAULTS. Values are strings because
# that is what the settings table stores.
DEFAULTS: dict[str, str] = {
    "brand_name": "ATHENA",
    "login_tagline": "Operator access to the control plane.",
    # Where the artwork sits relative to the form.
    "login_layout": "split-right",
    # How far the artwork is dimmed, 0-90 percent. A bright wallpaper behind
    # pale text is unreadable, and the operator picks the picture, not us.
    "login_overlay": "45",
    # object-position for the artwork, so a portrait subject can be kept in
    # frame when the panel is cropped.
    "login_focal": "center",
    # An external URL wins over the library when set, so an operator who
    # already hosts their artwork does not have to upload it again.
    "login_image_url": "",
    # Which stored image is showing. Empty = none.
    "login_image_id": "",
}

LAYOUTS = ("split-right", "split-left", "centered", "backdrop")
FOCALS = ("center", "top", "bottom", "left", "right")

# Deliberately a small allow-list rather than "image/*": the file is served back
# to unauthenticated browsers, and SVG is a script-execution vector.
CONTENT_TYPES: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/avif": ".avif",
    "image/gif": ".gif",
}
EXT_TO_TYPE = {ext: ctype for ctype, ext in CONTENT_TYPES.items()}

MAX_IMAGE_BYTES = 12 * 1024 * 1024
# A ceiling so a forgotten panel cannot fill the disk one wallpaper at a time.
MAX_LIBRARY = 12

# Magic numbers, checked against the declared content type. A browser's
# Content-Type header is attacker-controlled; the first bytes of the file are
# the only claim worth trusting.
_MAGIC: dict[str, tuple[bytes, ...]] = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".webp": (b"RIFF",),
    ".avif": (b"\x00\x00\x00",),
    ".gif": (b"GIF87a", b"GIF89a"),
}

_ID_LEN = 16


def directory() -> Path:
    """Where the artwork lives. Overridable so tests never touch a real path."""
    return Path(os.environ.get("BRANDING_DIR", "/var/lib/vpn-panel/branding"))


def _valid_id(image_id: str) -> bool:
    """Ids are our own hex digests. Anything else is someone probing for
    ../../etc/passwd, so it never reaches the filesystem."""
    return (
        isinstance(image_id, str)
        and len(image_id) == _ID_LEN
        and all(c in "0123456789abcdef" for c in image_id)
    )


def path_for(image_id: str) -> Path | None:
    if not _valid_id(image_id):
        return None
    d = directory()
    for ext in CONTENT_TYPES.values():
        p = d / f"{image_id}{ext}"
        if p.is_file():
            return p
    return None


def library() -> list[dict]:
    """Every stored image, newest first."""
    d = directory()
    if not d.is_dir():
        return []
    out = []
    for p in d.iterdir():
        if not p.is_file() or p.suffix not in EXT_TO_TYPE:
            continue
        if not _valid_id(p.stem):
            continue
        st = p.stat()
        out.append({
            "id": p.stem,
            "content_type": EXT_TO_TYPE[p.suffix],
            "bytes": st.st_size,
            "uploaded_at": st.st_mtime,
        })
    out.sort(key=lambda r: r["uploaded_at"], reverse=True)
    return out


def content_type_for(path: Path) -> str:
    return EXT_TO_TYPE.get(path.suffix, "application/octet-stream")


def validate(content_type: str, blob: bytes) -> str:
    """Return the extension to store under, or raise ValueError.

    Checked in the order a caller can act on: type first (the operator picked
    the wrong file), then size, then magic (the file is not what it claims).
    """
    ext = CONTENT_TYPES.get((content_type or "").split(";")[0].strip().lower())
    if ext is None:
        allowed = ", ".join(sorted(CONTENT_TYPES))
        raise ValueError(f"Unsupported image type. Allowed: {allowed}")
    if not blob:
        raise ValueError("The uploaded file is empty")
    if len(blob) > MAX_IMAGE_BYTES:
        raise ValueError(f"Image is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
    if not any(blob.startswith(m) for m in _MAGIC.get(ext, ())):
        raise ValueError("That file's contents are not a valid image of the declared type")
    return ext


def save(blob: bytes, ext: str) -> str:
    """Store an image and return its id.

    Content-addressed, so re-uploading a picture the library already holds is a
    no-op that returns the existing id rather than a second copy of the file.
    """
    image_id = hashlib.sha256(blob).hexdigest()[:_ID_LEN]
    d = directory()
    d.mkdir(parents=True, exist_ok=True)

    existing = path_for(image_id)
    if existing is not None:
        return image_id

    current = library()
    if len(current) >= MAX_LIBRARY:
        raise ValueError(
            f"The library holds {MAX_LIBRARY} images. Remove one before adding another."
        )

    target = d / f"{image_id}{ext}"
    tmp = d / f".{image_id}.tmp"
    tmp.write_bytes(blob)
    tmp.replace(target)  # atomic, so a reader never sees a half-written file
    return image_id


def remove(image_id: str) -> bool:
    p = path_for(image_id)
    if p is None:
        return False
    p.unlink()
    return True


# The single-slot scheme that shipped before the library: one file called
# login-image.<ext> and a version stamp in app_settings.
_LEGACY_STEM = "login-image"


def adopt_legacy() -> str | None:
    """Fold a pre-library image into the library and return its new id.

    Without this the operator's wallpaper is still on disk but invisible: the
    library only lists files whose name is a content id, so a deploy would
    silently blank the login page and the only way back would be to find the
    original file and upload it again.
    """
    d = directory()
    if not d.is_dir():
        return None
    for ext in CONTENT_TYPES.values():
        old = d / f"{_LEGACY_STEM}{ext}"
        if not old.is_file():
            continue
        blob = old.read_bytes()
        image_id = hashlib.sha256(blob).hexdigest()[:_ID_LEN]
        target = d / f"{image_id}{ext}"
        if target.exists():
            old.unlink()  # already adopted on an earlier start
        else:
            old.rename(target)
        return image_id
    return None


def public_view(values: dict[str, str]) -> dict:
    """The cosmetic subset served to an unauthenticated browser."""
    layout = values.get("login_layout", "split-right")
    focal = values.get("login_focal", "center")
    try:
        overlay = max(0, min(90, int(values.get("login_overlay", "45"))))
    except (TypeError, ValueError):
        overlay = 45
    url = (values.get("login_image_url") or "").strip()
    image_id = (values.get("login_image_id") or "").strip()
    # A stored id whose file has gone (deleted by hand, restored from a dump
    # without the directory) must read as "no image" rather than pointing the
    # login page at a 404.
    if image_id and path_for(image_id) is None:
        image_id = ""
    return {
        "brand_name": values.get("brand_name") or "ATHENA",
        "login_tagline": values.get("login_tagline", ""),
        "login_layout": layout if layout in LAYOUTS else "split-right",
        "login_focal": focal if focal in FOCALS else "center",
        "login_overlay": overlay,
        "login_image_url": url,
        "login_image_id": image_id,
        "has_image": bool(url) or bool(image_id),
    }
