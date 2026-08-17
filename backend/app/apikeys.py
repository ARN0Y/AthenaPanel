"""API keys: programmatic credentials for the public API.

A key authenticates AS an admin. That one decision is what keeps this small:
`rbac.py` already decides what an admin may see, so a reseller's key is scoped
to that reseller's accounts without a single line here knowing what a reseller
is. Scopes narrow further, and can only ever narrow — a read-only key belonging
to a superadmin still cannot write, and a key belonging to a reseller can never
see another reseller's users no matter what scopes it carries.

Key format:

    ath_<24 chars public prefix>_<43 chars secret>

The prefix is stored in clear and is what appears in logs, in the panel and in
"which key was that" conversations. The secret is stored as SHA-256 and shown
exactly once. bcrypt would be the reflex and is the wrong tool here: this is
verified on every single request, and a deliberately slow KDF would become a
self-inflicted rate limit. A 32-byte random secret has no dictionary to attack,
so a fast hash over full entropy is the right trade — the same reasoning that
makes session tokens fast to check.
"""

import hashlib
import logging
import secrets
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Admin, ApiKey

log = logging.getLogger("vpn-panel.apikeys")

PREFIX = "ath"
_PREFIX_LEN = 12
_SECRET_BYTES = 32
# The prefix is cut back out of the presented key by splitting on '_', so it
# must not contain one itself. token_urlsafe would be the reflex and is wrong
# here: its base64url alphabet includes '_' and '-', and a prefix that happened
# to contain '_' made split_prefix() cut in the wrong place — the key hashed
# fine but could never be looked up again. Measured at 17% of generated keys,
# i.e. one key in six dead on arrival with no symptom but "it doesn't work".
_PREFIX_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

# Requests per minute for a key that does not set its own.
DEFAULT_RATE_LIMIT = 120

# Scopes. Deliberately coarse: a bot author should be able to hold the whole
# permission model in their head, and every extra scope is a decision someone
# has to get right at 2am. "*" is not a scope name, it is the absence of any.
SCOPES: dict[str, str] = {
    "users:read": "List and read VPN accounts, their usage and their sessions.",
    "users:write": "Create, edit, enable, disable and delete VPN accounts.",
    "sessions:read": "See who is connected right now.",
    "sessions:write": "Disconnect a live session.",
    "system:read": "Nodes, outbounds, panel health and platform totals.",
}
WRITE_SCOPES = {"users:write", "sessions:write"}


def generate() -> tuple[str, str, str]:
    """(full_key, prefix, hash). The full key is never stored anywhere.

    The secret may contain '_' freely — split_prefix() only ever splits off the
    first two segments, so anything after them is opaque to it.
    """
    body = "".join(secrets.choice(_PREFIX_ALPHABET) for _ in range(_PREFIX_LEN))
    prefix = f"{PREFIX}_{body}"
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    full = f"{prefix}_{secret}"
    return full, prefix, hash_key(full)


def hash_key(full_key: str) -> str:
    return hashlib.sha256(full_key.encode()).hexdigest()


def split_prefix(full_key: str) -> str:
    """The public part, for looking a key up before verifying it."""
    parts = (full_key or "").split("_")
    if len(parts) < 3 or parts[0] != PREFIX:
        return ""
    return f"{parts[0]}_{parts[1]}"


def normalize_scopes(raw: list[str] | str | None) -> str:
    if raw is None:
        return ""
    items = raw.split() if isinstance(raw, str) else list(raw)
    kept = [s.strip() for s in items if s.strip() in SCOPES]
    # Sorted so two keys with the same permissions compare equal as strings and
    # the panel does not show them in whatever order they were typed.
    return " ".join(sorted(set(kept)))


def allows(key: ApiKey, scope: str) -> bool:
    """Whether this key may perform an action needing `scope`.

    No scopes means "everything the owning admin can do" — which is the useful
    default for the operator's own automation and still cannot exceed that
    admin's own reach.
    """
    granted = key.scope_set
    return not granted or scope in granted


async def resolve(db: AsyncSession, full_key: str) -> tuple[ApiKey, Admin] | None:
    """Look up and verify a presented key. None for anything not usable.

    Deliberately returns the same None for "no such key", "revoked", "expired"
    and "its admin is disabled": a caller learning WHICH of those is true learns
    whether a key exists, and there is no legitimate use for that distinction.
    """
    prefix = split_prefix(full_key)
    if not prefix:
        return None
    key = (
        await db.execute(select(ApiKey).where(ApiKey.prefix == prefix))
    ).scalar_one_or_none()
    if key is None:
        return None
    # Compared in constant time even though a hash mismatch leaks nothing
    # useful here — the lookup is by prefix, so this is the one comparison that
    # sees the secret, and making it timing-safe costs nothing.
    if not secrets.compare_digest(key.key_hash, hash_key(full_key)):
        return None
    if not key.is_active or key.is_expired:
        return None
    admin = await db.get(Admin, key.admin_id)
    if admin is None or not admin.is_active:
        return None
    return key, admin


# ---- rate limiting ---------------------------------------------------------
#
# A fixed window per key, in memory. In memory because the panel is one process
# and a limiter that survives restarts would be solving a problem nobody has;
# fixed window rather than a token bucket because the failure mode of a fixed
# window — up to 2x the limit across a boundary — is irrelevant when the point
# is to stop a runaway loop, not to meter a paid product.
_WINDOWS: dict[int, tuple[int, int]] = {}   # key_id -> (window_start, count)


def check_rate(key: ApiKey) -> tuple[bool, int, int]:
    """(allowed, limit, remaining)."""
    limit = key.rate_limit or DEFAULT_RATE_LIMIT
    now = int(time.time())
    window = now - (now % 60)
    start, count = _WINDOWS.get(key.id, (window, 0))
    if start != window:
        start, count = window, 0
    count += 1
    _WINDOWS[key.id] = (start, count)
    return count <= limit, limit, max(0, limit - count)


def reset_rate_state() -> None:
    """For tests. Production never needs this — the window ages out."""
    _WINDOWS.clear()


# ---- usage bookkeeping -----------------------------------------------------

_LAST_TOUCH: dict[int, float] = {}
_TOUCH_INTERVAL = 60.0


def should_touch(key: ApiKey) -> bool:
    """Whether to write last_used_at/request_count on this request.

    Throttled to once a minute per key. The column answers "is this key still
    in use", which does not need second precision and is not worth a write on
    every call — a busy bot would otherwise turn every read into a write.
    """
    now = time.monotonic()
    last = _LAST_TOUCH.get(key.id, 0.0)
    if now - last < _TOUCH_INTERVAL:
        return False
    _LAST_TOUCH[key.id] = now
    return True


def touch(key: ApiKey, requests_since: int = 1) -> None:
    key.last_used_at = datetime.now(timezone.utc)
    key.request_count = (key.request_count or 0) + requests_since
