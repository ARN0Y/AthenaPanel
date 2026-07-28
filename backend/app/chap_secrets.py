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
import logging
import os
import tempfile

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import accel
from .config import settings
from .models import LOCAL_NODE_ID, User

log = logging.getLogger("vpn-panel.chap")

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


async def rewrite(db: AsyncSession, node_id: int = LOCAL_NODE_ID) -> None:
    """Atomically rewrite this node's chap-secrets from the DB.

    Scoped to one node: a user authenticates on the node they are assigned to,
    so writing the whole account list onto every node would let anyone connect
    anywhere regardless of their assignment, and would spread every credential
    across every machine for no benefit.

    The default is the local node, which keeps the panel server behaving exactly
    as before — all pre-existing accounts are assigned to it.
    """
    result = await db.execute(
        select(User).where(User.node_id == node_id).order_by(User.username)
    )
    users = list(result.scalars().all())
    content = render(users)

    path = settings.chap_secrets
    directory = os.path.dirname(path) or "."

    async with _lock:
        await asyncio.to_thread(_atomic_write, directory, path, content)

    # accel-ppp loads chap-secrets at startup -> reload so changes take effect.
    # (No-op for the xl2tpd/pppd engine, which re-reads on each auth.)
    await accel.reload()

    # Remote nodes keep their own copy of this file, so the same change has to
    # reach them. The hub is a separate process; a timestamp on the node row is
    # the channel, and it notices on that node's next report. Failing here must
    # never break the local write that already succeeded — a node that misses a
    # nudge still resyncs when it reconnects.
    try:
        await _nudge_remote_nodes()
    except Exception:  # noqa: BLE001
        log.exception("could not flag remote nodes for resync")


async def _nudge_remote_nodes() -> None:
    """Flag every remote node as needing a fresh account list.

    Runs in its OWN session rather than the caller's. It has to commit, and
    committing someone else's session commits whatever else they had pending —
    every caller happens to commit first today, but that is a property of the
    callers, not something this function can rely on.

    `enabled` is deliberately not consulted, for the same reason
    nodes.authoritative_ids ignores it: a disabled node is one that takes no NEW
    users, not one that stopped serving the ones it has. Skipping it would leave
    a node draining its sessions with a stale account list, still authenticating
    people who were deleted.
    """
    from datetime import datetime, timezone

    from .database import AsyncSessionLocal
    from .models import Node

    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as own:
        rows = (
            await own.execute(select(Node).where(Node.is_local.is_(False)))
        ).scalars().all()
        for node in rows:
            node.sync_requested_at = now
        if rows:
            await own.commit()


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
