"""Hub-side handling of a node's credit requests and user synchronisation.

Kept out of nodehub.py so the transport (streams, auth, TLS) stays separate
from the policy (who may use how much). The transport should be boring and
rarely change; this is where the business rules live.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
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


@dataclass
class _Spend:
    """What this hub has already billed a user for, and when it last heard.

    `grant_id` / `billed` are a watermark, NOT a record of the newest grant
    issued. That distinction is the whole point: `consumed_bytes` on the wire is
    CUMULATIVE under the grant the agent is holding, so the amount that is new
    is whatever exceeds what was already billed under that same grant. Storing
    the grant we last ISSUED instead would silently discard traffic every time a
    reply failed to arrive — the agent would keep quoting the grant it still
    holds, the hub would judge it superseded, and the customer would use real
    bandwidth for free until the two happened to line up again.
    """

    grant_id: int = 0
    billed: int = 0
    at: float = 0.0


# Per username. In memory because it is a de-duplication watermark, not a
# ledger: losing it on restart costs at most one duplicate acceptance, while
# persisting it would put a write on the hot path of every credit request.
_SPEND: dict[str, _Spend] = {}


def _bill_new_bytes(username: str, grant_id: int, consumed_bytes: int) -> tuple[int, float]:
    """How much of `consumed_bytes` has not been billed yet, and over how long.

    Three cases, and each one is a real thing that happens on a link between
    continents:

      * A newer grant than the watermark — everything reported under it is new.
      * The SAME grant reported again with a larger cumulative figure — the
        reply to the previous request never arrived and the agent is still
        spending the grant it holds. Bill the difference.
      * An older grant, or the same figure twice — a duplicate delivery. Bill
        nothing.
    """
    now = time.monotonic()
    st = _SPEND.get(username)
    if st is None:
        # Nothing known: either the first request ever, or the first after a
        # restart. Accepted once, because refusing would throw away real traffic
        # to protect against a replay that has never been observed.
        _SPEND[username] = _Spend(grant_id=grant_id, billed=consumed_bytes, at=now)
        return max(0, consumed_bytes), 0.0

    elapsed = max(0.0, now - st.at) if st.at else 0.0
    if grant_id > st.grant_id:
        new = max(0, consumed_bytes)
        st.grant_id, st.billed = grant_id, consumed_bytes
    elif grant_id == st.grant_id:
        new = max(0, consumed_bytes - st.billed)
        st.billed = max(st.billed, consumed_bytes)
    else:
        new = 0
    st.at = now
    return new, elapsed


def _forget_spend(username: str) -> None:
    """Drop a user's watermark once they can no longer be served here."""
    _SPEND.pop(username, None)


def _effective_rate_bps(user: User, new_bytes: int, elapsed: float) -> int:
    """What speed to size this user's grant against.

    Prefers the rate they are actually achieving, because that is what decides
    how long a grant lasts — measured over the window between their last two
    requests, which is the only interval this process can time accurately.

    The configured ceiling is the floor of the estimate, not a fallback: an
    account with NO rate limit would otherwise be sized from the 1 Mbps minimum
    and end up asking for credit several times a second on a gigabit line.
    """
    observed = int(new_bytes * 8 / elapsed) if elapsed > 1 and new_bytes > 0 else 0
    configured = max(user.rate_down_kbps or 0, user.rate_up_kbps or 0) * 1000
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

    `consumed_bytes` is cumulative under `grant_id`; see _bill_new_bytes for how
    a duplicate delivery and a retry after a lost reply are told apart.
    """
    async with AsyncSessionLocal() as db:
        user = (
            await db.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        if user is None:
            _forget_spend(username)
            return credit.refuse("no such account")

        # Bill BEFORE deciding anything, including before checking which node
        # this is. The bytes moved: whether the account has since been moved,
        # disabled or exhausted changes what happens next, never whether the
        # traffic that already flowed is real. Charging only for traffic the
        # panel approves of is how a node ends up serving gigabytes for free
        # during exactly the situations that matter most.
        new_bytes, elapsed = _bill_new_bytes(username, grant_id, consumed_bytes)
        if new_bytes > 0:
            billed = int(new_bytes * settings.usage_multiplier)
            user.used_bytes = (user.used_bytes or 0) + billed
            log.debug(
                "node %d: %s spent %d new bytes under grant %d (billed %d)",
                node_id, username, new_bytes, grant_id, billed,
            )
        elif consumed_bytes > 0:
            log.debug(
                "node %d: %s reported %d bytes under grant %d, already billed",
                node_id, username, consumed_bytes, grant_id,
            )

        # A user is served by exactly one node. A request from anywhere else is
        # a stale agent that has not applied its sync yet; refusing is what
        # stops a moved account being served in two places at once — but only
        # after the traffic above has been paid for.
        if (user.node_id or 1) != node_id:
            await db.commit()
            return credit.refuse("account is assigned to another node")

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
            rate_bps=_effective_rate_bps(user, new_bytes, elapsed),
            enabled=user.enabled_for_auth,
            refuse_reason=_refusal_reason(user),
        )
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
    from .models import WgPeer
    from .pb import nodehub_pb2

    async with AsyncSessionLocal() as db:
        users = await users_for_node(db, node_id)
        # Peers travel WITH their account rather than as a separate list, so a
        # node can never end up holding a key whose owner it does not know —
        # that is what makes a WireGuard peer's traffic billable instead of
        # anonymous, and it is why the node refuses to meter one it was not told
        # about.
        peers_by_user: dict[int, list[WgPeer]] = {}
        if users:
            rows = (
                await db.execute(
                    select(WgPeer).where(WgPeer.user_id.in_([u.id for u in users]))
                )
            ).scalars().all()
            for peer in rows:
                if peer.enabled:
                    peers_by_user.setdefault(peer.user_id, []).append(peer)

    entries = [
        nodehub_pb2.UserSync.Entry(
            username=u.username,
            password=u.password,
            enabled=u.enabled_for_auth,
            rate_down_kbps=max(0, u.rate_down_kbps or 0),
            rate_up_kbps=max(0, u.rate_up_kbps or 0),
            l2tp_mode=(u.l2tp_mode or "ipsec"),
            outbound=outbound.normalize(u.outbound),
            wg_peers=[
                nodehub_pb2.UserSync.WgPeer(
                    public_key=p.public_key,
                    preshared_key=p.preshared_key or "",
                    address=p.address,
                )
                for p in peers_by_user.get(u.id, [])
            ],
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
