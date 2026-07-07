"""Signed, stateless per-user subscription token.

The token is `<user_id>-<sig>` where sig = HMAC(jwt_secret, "sub:<id>"). It needs
no DB column and can't be forged without the server secret. Rotating jwt_secret
invalidates every sub link at once.
"""

import base64
import hashlib
import hmac

from .config import settings


def _sig(uid: int) -> str:
    raw = hmac.new(settings.jwt_secret.encode(), f"sub:{uid}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")[:22]


def make_token(uid: int) -> str:
    return f"{uid}-{_sig(uid)}"


def parse_token(token: str) -> int | None:
    try:
        # uid is numeric, so the FIRST hyphen is the separator; the base64 sig
        # may itself contain '-'.
        uid_s, sig = token.split("-", 1)
        uid = int(uid_s)
    except (ValueError, AttributeError):
        return None
    return uid if hmac.compare_digest(sig, _sig(uid)) else None
