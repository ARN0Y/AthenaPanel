"""Hub-side handling of a node's credit requests and user synchronisation.

Kept out of nodehub.py so the transport (streams, auth, TLS) stays separate
from the policy (who may use how much). The transport should be boring and
rarely change; this is where the business rules live.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import accounting, credit, nodesessions, outbound, pppd
from .config import settings
from .database import AsyncSessionLocal
from .models import Node, User

log = logging.getLogger("vpn-panel.nodecredit")


def _duration(started: datetime | None) -> int:
    """Seconds a session lasted. Zero when its start was never recorded."""
    if started is None:
        return 0
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - started).total_seconds()))


def _effective_rate_bps(user: User, session_rx: int, session_tx: int, elapsed: float) -> int:
    """What speed to size this user's grant against.

    Prefers the rate they are actually achieving, because that is what decides
    how long a grant lasts. Falls back to their configured ceiling, which is the
    only bound we have before any traffic has been observed. Never zero — a
    zero would collapse every grant to the floor and make a fast user ask
    constantly.
    """
    observed = 0
    if elapsed > 1:
        observed = int((session_rx + session_tx) * 8 / elapsed)
    configured = max(user.rate_down_kbps, user.rate_up_kbps) * 1000
    return max(observed, configured, 1_000_000)


async def handle_credit_request(
    node_id: int,
    username: str,
    reason: str,
    consumed_bytes: int,
    grant_id: int,
    session_rx: int,
    session_tx: int,
    ifname: str,
) -> credit.Grant:
    """Bill what was spent, then decide what to authorise next.

    Consumption is applied FIRST and unconditionally. If this process died
    between the two, the worst outcome is a user billed for traffic they were
    then refused — which is honest. Granting first and crediting later would
    give away traffic for free on the same crash, and the mistake would compound
    every time it happened.

    `consumed_bytes` is relative to `grant_id`, so a duplicate delivery of the
    same request contributes nothing the second time: the grant it names has
    already been superseded and its consumption is dropped.
    """
    async with AsyncSessionLocal() as db:
        user = (
            await db.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        if user is None:
            return credit.refuse("no such account")

        # A user is served by exactly one node. A request from anywhere else is
        # a stale agent that has not applied its sync yet; refusing is what
        # stops a moved account being served in two places at once.
        if (user.node_id or 1) != node_id:
            return credit.refuse("account is assigned to another node")

        if consumed_bytes > 0 and _grant_is_current(user, grant_id):
            billed = int(consumed_bytes * settings.usage_multiplier)
            user.used_bytes = (user.used_bytes or 0) + billed
            log.debug(
                "node %d: %s spent %d bytes under grant %d (billed %d)",
                node_id, username, consumed_bytes, grant_id, billed,
            )
        elif consumed_bytes > 0:
            log.info(
                "node %d: dropping %d bytes from %s under stale grant %d",
                node_id, consumed_bytes, username, grant_id,
            )

        if reason == "SESSION_ENDED":
            # The mirrored row is where this session's start time and address
            # pool live; the agent reports neither. Closing it here rather than
            # waiting for the interface to fall out of a report is what makes
            # the ledger row carry a real duration instead of a zero.
            row = await nodesessions.close_session(db, node_id, ifname)
            await accounting.record_session(
                db,
                username=username,
                proto=pppd.classify_proto(row.peer_ip if row else "", row.proto if row else ""),
                ifname=ifname or "",
                started_at=row.started_at if row else None,
                bytes_in=int(session_rx * settings.usage_multiplier),
                bytes_out=int(session_tx * settings.usage_multiplier),
                duration=_duration(row.started_at if row else None),
                node_id=node_id,
            )
            await db.commit()
            return credit.refuse("session ended")

        remaining: int | None = None
        if user.quota_bytes and user.quota_bytes > 0:
            remaining = max(0, user.quota_bytes - (user.used_bytes or 0))

        grant = credit.allocate(
            remaining_bytes=remaining,
            rate_bps=_effective_rate_bps(user, session_rx, session_tx, 0),
            enabled=user.enabled_for_auth,
            refuse_reason=_refusal_reason(user),
        )
        _remember_grant(user, grant)
        await db.commit()
        return grant


def _refusal_reason(user: User) -> str:
    if not user.is_active:
        return "account disabled"
    if user.is_expired:
        return "subscription expired"
    if user.quota_exceeded:
        return "quota exhausted"
    return ""


# The newest grant id issued per user, in memory. It exists only to reject a
# replayed CreditRequest, so losing it on restart is harmless: the first request
# after a restart re-establishes it, and the window where a replay could slip
# through is a few seconds rather than a billing error that persists.
_CURRENT_GRANT: dict[str, int] = {}


def _grant_is_current(user: User, grant_id: int) -> bool:
    known = _CURRENT_GRANT.get(user.username)
    # An INITIAL request carries grant_id 0 and no consumption; anything we have
    # never seen is accepted once, because refusing it would lose real traffic
    # after a hub restart.
    return known is None or grant_id >= known


def _remember_grant(user: User, grant: credit.Grant) -> None:
    _CURRENT_GRANT[user.username] = grant.grant_id


async def users_for_node(db: AsyncSession, node_id: int) -> list[User]:
    """Accounts this node should accept.

    Only the users assigned here. A node cannot authenticate someone it was
    never given, which is what makes the assignment real rather than advisory.
    """
    rows = await db.execute(
        select(User).where(User.node_id == node_id).order_by(User.username)
    )
    return list(rows.scalars().all())


async def build_sync(node_id: int, sync_id: int):
    """The full account list for a node, ready to put on the wire.

    Sent whole rather than as a diff: a node that missed one update would
    diverge silently and nothing would ever notice. At this scale replacing the
    list outright is cheaper than being clever about it.
    """
    from .pb import nodehub_pb2

    async with AsyncSessionLocal() as db:
        users = await users_for_node(db, node_id)

    entries = [
        nodehub_pb2.UserSync.Entry(
            username=u.username,
            password=u.password,
            enabled=u.enabled_for_auth,
            rate_down_kbps=max(0, u.rate_down_kbps or 0),
            rate_up_kbps=max(0, u.rate_up_kbps or 0),
            l2tp_mode=(u.l2tp_mode or "ipsec"),
            outbound=outbound.normalize(u.outbound),
        )
        for u in users
    ]
    return nodehub_pb2.UserSync(sync_id=sync_id, users=entries, full=True)


async def take_pending_disconnects(node_id: int) -> list[tuple[str, str]]:
    """Claim every queued disconnect for a node. Returns (username, reason).

    Claimed, not read: the flag is cleared in the same transaction, so two
    reports racing cannot each send the same kick. The cost of that choice is
    that a stream dying between the clear and the send loses the request — which
    is why this is only ever the operator's "kick them now" button and never the
    path that enforces quota or expiry. Those run on the node itself, in the
    credit loop, and do not depend on the master being reachable at all.
    """
    async with AsyncSessionLocal() as db:
        users = (
            await db.execute(
                select(User).where(
                    User.node_id == node_id,
                    User.disconnect_requested_at.isnot(None),
                )
            )
        ).scalars().all()
        claimed = [(u.username, _refusal_reason(u) or "disconnected by an operator")
                   for u in users]
        for u in users:
            u.disconnect_requested_at = None
        if claimed:
            await db.commit()
        return claimed


async def touch_sync_needed(node_id: int) -> None:
    """Mark a node as needing a fresh user list.

    Set by the panel when an account changes; noticed by the hub, which is a
    different process. The database is the only channel between them, and a
    timestamp is enough — the hub always sends the whole list, so it does not
    matter how many changes happened in between.
    """
    async with AsyncSessionLocal() as db:
        node = await db.get(Node, node_id)
        if node is not None:
            node.sync_requested_at = datetime.now(timezone.utc)
            await db.commit()


async def mark_synced(node_id: int, sync_id: int, ok: bool, detail: str) -> None:
    async with AsyncSessionLocal() as db:
        node = await db.get(Node, node_id)
        if node is None:
            return
        if ok:
            node.synced_at = datetime.now(timezone.utc)
        await db.commit()
    if not ok:
        log.warning("node %d rejected sync %d: %s", node_id, sync_id, detail)
