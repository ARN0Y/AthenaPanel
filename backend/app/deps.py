"""Shared FastAPI dependencies: current admin resolution + role guards."""

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from . import apikeys
from .database import get_session
from .models import Admin, ApiKey
from .security import decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login", auto_error=False)

_UNAUTH = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_admin(
    token: str | None = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_session),
) -> Admin:
    if not token:
        raise _UNAUTH
    payload = decode_token(token)
    if not payload or "sub" not in payload:
        raise _UNAUTH
    try:
        admin_id = int(payload["sub"])
    except (TypeError, ValueError):
        raise _UNAUTH
    admin = await db.get(Admin, admin_id)
    if not admin or not admin.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive or unknown admin")
    return admin


async def require_superadmin(admin: Admin = Depends(get_current_admin)) -> Admin:
    if not admin.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Superadmin only")
    return admin


# Backwards-compatible alias used by existing routers
require_admin = get_current_admin


# ---- public API authentication ---------------------------------------------


@dataclass
class Principal:
    """Who is calling, and how.

    `admin` is the identity every ownership rule is written against, so the
    public API gets rbac.py's scoping for free and cannot drift from what the
    panel itself enforces. `key` is None for a session token, which is how a
    scope check knows to allow everything the admin can do.
    """

    admin: Admin
    key: ApiKey | None = None

    @property
    def is_superadmin(self) -> bool:
        return self.admin.is_superadmin

    @property
    def label(self) -> str:
        return f"{self.admin.username}:{self.key.prefix}" if self.key else self.admin.username


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""


async def get_principal(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> Principal:
    """Authenticate a public API call by API key or by session token.

    Both are accepted on purpose: a bot uses a key, and the panel's own UI can
    call the same endpoints with the token it already holds — which means the
    documented API is exercised by the product itself rather than being a
    second surface that quietly rots.
    """
    raw = request.headers.get("x-api-key") or ""
    token = _bearer(request)
    presented = raw or (token if token.startswith(f"{apikeys.PREFIX}_") else "")

    if presented:
        found = await apikeys.resolve(db, presented)
        if found is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or revoked API key",
                headers={"WWW-Authenticate": "Bearer"},
            )
        key, admin = found
        ok, limit, remaining = apikeys.check_rate(key)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded ({limit}/min for this key)",
                headers={"Retry-After": "60", "X-RateLimit-Limit": str(limit),
                         "X-RateLimit-Remaining": "0"},
            )
        if apikeys.should_touch(key):
            apikeys.touch(key)
            await db.commit()
        return Principal(admin=admin, key=key)

    if not token:
        raise _UNAUTH
    admin = await get_current_admin(token=token, db=db)
    return Principal(admin=admin, key=None)


def require_scope(scope: str):
    """Guard a public endpoint with a scope.

    A session token carries no key and so passes every scope check: it already
    proved it is the admin, and the admin's own role is what limits it. Scopes
    exist to restrict a KEY below its owner, never to grant anything.
    """

    async def _guard(principal: Principal = Depends(get_principal)) -> Principal:
        if principal.key is not None and not apikeys.allows(principal.key, scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This API key does not carry the '{scope}' scope",
            )
        return principal

    return _guard
