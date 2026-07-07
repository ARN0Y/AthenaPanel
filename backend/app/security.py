"""Password hashing + JWT for DB-backed admin accounts."""

from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from .config import settings

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return _pwd.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _pwd.verify(password, hashed)
    except ValueError:
        return False


def create_access_token(admin_id: int, username: str, role: str) -> tuple[str, int]:
    expire_seconds = settings.jwt_expire_hours * 3600
    expire = datetime.now(timezone.utc) + timedelta(seconds=expire_seconds)
    payload = {
        "sub": str(admin_id),
        "username": username,
        "role": role,
        "exp": expire,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expire_seconds


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
