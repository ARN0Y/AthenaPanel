"""Atomic chap-secrets writer for pppd / xl2tpd.

Format (pppd):
    # client    server    secret    IP
    "username"  *         "password"  *

pppd re-reads /etc/ppp/chap-secrets on every authentication, so NO daemon
reload/SIGHUP is needed -- a fresh file takes effect for the next connection.
Existing sessions are untouched (the enforcer kills disabled users separately).

Only users that are enabled_for_auth are written, so disabled / expired /
over-quota users cannot authenticate.
"""

import asyncio
import os
import tempfile

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import accel
from .config import settings
from .models import User

_lock = asyncio.Lock()

_HEADER = (
    "# Managed by vpn-panel. DO NOT EDIT BY HAND.\n"
    "# client\tserver\tsecret\tIP\n"
)


def _quote(value: str) -> str:
    # Always quote so spaces / special chars are safe.
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(users: list[User]) -> str:
    server = settings.chap_server_field or "*"
    lines = [_HEADER]
    for u in users:
        if not u.enabled_for_auth:
            continue
        lines.append(f"{_quote(u.username)}\t{server}\t{_quote(u.password)}\t*\n")
    return "".join(lines)


async def rewrite(db: AsyncSession) -> None:
    """Atomically rewrite chap-secrets from the DB."""
    result = await db.execute(select(User).order_by(User.username))
    users = list(result.scalars().all())
    content = render(users)

    path = settings.chap_secrets
    directory = os.path.dirname(path) or "."

    async with _lock:
        await asyncio.to_thread(_atomic_write, directory, path, content)

    # accel-ppp loads chap-secrets at startup -> reload so changes take effect.
    # (No-op for the xl2tpd/pppd engine, which re-reads on each auth.)
    await accel.reload()


def _atomic_write(directory: str, path: str, content: str) -> None:
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".chap-secrets.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)  # atomic rename on same filesystem
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def parse_existing(path: str) -> list[tuple[str, str]]:
    """Parse an existing chap-secrets into (username, password) pairs.

    Used on first boot to import users already provisioned by the hwdsl2
    installer. Tolerates quoted and unquoted fields.
    """
    pairs: list[tuple[str, str]] = []
    if not os.path.exists(path):
        return pairs

    def _unquote(tok: str) -> str:
        tok = tok.strip()
        if len(tok) >= 2 and tok[0] == '"' and tok[-1] == '"':
            return tok[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        return tok

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            # split respecting quotes (simple state machine)
            tokens: list[str] = []
            cur = ""
            in_q = False
            for ch in line:
                if ch == '"':
                    in_q = not in_q
                    cur += ch
                elif ch.isspace() and not in_q:
                    if cur:
                        tokens.append(cur)
                        cur = ""
                else:
                    cur += ch
            if cur:
                tokens.append(cur)
            if len(tokens) >= 3:
                user = _unquote(tokens[0])
                secret = _unquote(tokens[2])
                if user and secret:
                    pairs.append((user, secret))
    return pairs
