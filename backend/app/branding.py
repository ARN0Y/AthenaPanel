"""Login-page appearance: cosmetic settings plus the operator's own image.

Two things make this different from the rest of `appsettings`:

* **It is read before anyone is authenticated.** The login page needs it to
  render, so `GET /api/branding` is public. Everything here is therefore
  deliberately cosmetic — no address, no protocol state, no operator name. Add
  a key here only if you would be happy printing it on a billboard.
* **The image is a file, not a value.** Wallpapers run to several megabytes, so
  storing one as a base64 column would bloat every settings read and every
  nightly dump. It lives on disk and is streamed by its own endpoint.
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
    # white text is unreadable, and the operator picks the picture, not us.
    "login_overlay": "45",
    # object-position for the artwork, so a portrait subject can be kept in
    # frame when the panel is cropped.
    "login_focal": "center",
    # An external URL wins over the uploaded file when set, so an operator who
    # already hosts their artwork does not have to upload it again.
    "login_image_url": "",
    # Bumped on every upload so browsers refetch a replaced image.
    "login_image_version": "0",
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

MAX_IMAGE_BYTES = 12 * 1024 * 1024

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

_STEM = "login-image"


def directory() -> Path:
    """Where the artwork lives. Overridable so tests never touch a real path."""
    return Path(os.environ.get("BRANDING_DIR", "/var/lib/vpn-panel/branding"))


def find_image() -> Path | None:
    """The stored image, whatever extension it was uploaded with."""
    d = directory()
    if not d.is_dir():
        return None
    for ext in CONTENT_TYPES.values():
        p = d / f"{_STEM}{ext}"
        if p.is_file() and p.stat().st_size > 0:
            return p
    return None


def content_type_for(path: Path) -> str:
    for ctype, ext in CONTENT_TYPES.items():
        if path.suffix == ext:
            return ctype
    return "application/octet-stream"


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
    """Write the artwork, replacing any previous one. Returns a version stamp.

    Only one image is ever kept: every other extension is removed first, so a
    png uploaded over a jpg cannot leave the old file behind to be served by
    `find_image()`'s extension scan.
    """
    d = directory()
    d.mkdir(parents=True, exist_ok=True)
    for other in CONTENT_TYPES.values():
        stale = d / f"{_STEM}{other}"
        if stale.is_file():
            stale.unlink()
    target = d / f"{_STEM}{ext}"
    tmp = d / f".{_STEM}.tmp"
    tmp.write_bytes(blob)
    tmp.replace(target)  # atomic, so a reader never sees a half-written file
    return hashlib.sha256(blob).hexdigest()[:12]


def remove() -> bool:
    """Drop the artwork; the login page falls back to its built-in backdrop."""
    gone = False
    for ext in CONTENT_TYPES.values():
        p = directory() / f"{_STEM}{ext}"
        if p.is_file():
            p.unlink()
            gone = True
    return gone


def public_view(values: dict[str, str]) -> dict:
    """The cosmetic subset served to an unauthenticated browser."""
    layout = values.get("login_layout", "split-right")
    focal = values.get("login_focal", "center")
    try:
        overlay = max(0, min(90, int(values.get("login_overlay", "45"))))
    except (TypeError, ValueError):
        overlay = 45
    url = (values.get("login_image_url") or "").strip()
    return {
        "brand_name": values.get("brand_name") or "ATHENA",
        "login_tagline": values.get("login_tagline", ""),
        "login_layout": layout if layout in LAYOUTS else "split-right",
        "login_focal": focal if focal in FOCALS else "center",
        "login_overlay": overlay,
        "login_image_url": url,
        "has_image": bool(url) or find_image() is not None,
        "login_image_version": values.get("login_image_version", "0"),
    }
