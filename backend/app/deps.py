"""Shared FastAPI dependencies: current admin resolution + role guards."""

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_session
from .models import Admin
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
