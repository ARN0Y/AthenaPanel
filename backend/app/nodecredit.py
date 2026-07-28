"""Hub-side handling of a node's credit requests and user synchronisation.

Kept out of nodehub.py so the transport (streams, auth, TLS) stays separate
from the policy (who may use how much). The transport should be boring and
rarely change; this is where the business rules live.
"""

from __future__ import annotations

import logging
import time
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


def _bill_new_bytes(user: User, grant_id: int, consumed_bytes: int) -> int:
    """How much of `consumed_bytes` has not been billed yet. Mutates the user's
    watermark; the caller persists it in the same transaction as used_bytes.

    `consumed_bytes` is CUMULATIVE under the grant the agent is holding, so what
    is new is whatever exceeds the watermark for that same grant. Four cases,
    each a real thing that happens on a link between continents:

      * No watermark at all — we cannot know how much of this figure has
        already been billed, so NOTHING is billed and the position is simply
        adopted. Under-charging once beats charging a customer twice, and this
        happens exactly once per account: on the first request after the
        watermark columns appeared.
      * A newer grant — the agent rebased its own counter when it applied that
        grant, so everything reported under it is new.
      * The SAME grant with a larger cumulative figure — the reply to the
        previous request never arrived and the agent is still spending the grant
        it holds. Bill the difference. Without this the traffic would be lost:
        the agent keeps quoting the grant it has, and a watermark that tracked
        the grant we last ISSUED would judge it superseded forever.
      * An older grant, or the same figure twice — a duplicate delivery, or a
        node the account has already been moved away from. Bill nothing.
    """
    known = user.credit_grant_id or 0
    billed = user.credit_billed_bytes or 0
    consumed = max(0, consumed_bytes)

    if known == 0:
        # Unknown, and deliberately NOT guessed at: adopting `grant_id` here
        # would claim `consumed` had already been billed when it may not have
        # been, or bill it when it already was. The position is established
        # instead by anchor_grant() once a grant is actually issued, which is
        # the first moment the hub knows a figure it can trust.
        return 0
    if grant_id > known:
        # The agent rebased its own counter when it applied this grant, so
        # everything reported under it is new.
        new = consumed
        user.credit_grant_id, user.credit_billed_bytes = grant_id, consumed
    elif grant_id == known:
        new = max(0, consumed - billed)
        user.credit_billed_bytes = max(billed, consumed)
    else:
        new = 0  # stale grant: leave the watermark exactly where it is
    return new


def anchor_grant(user: User, grant: credit.Grant) -> None:
    """Give a user a watermark to compare against, once and only once.

    Called after a real grant is issued and ONLY while the watermark is unknown.
    That restriction is the whole design: advancing it on every issue is what
    the old code did, and it is why a grant whose reply never arrived had its
    traffic dropped forever — the agent kept quoting the grant it still held,
    while the hub had already moved past it.

    Anchoring from unknown loses nothing, because in that state there was no
    figure to compare against in the first place.
    """
    if (user.credit_grant_id or 0) == 0 and grant.is_service:
        user.credit_grant_id = grant.grant_id
        user.credit_billed_bytes = 0


# When each user was last heard from, for the throughput estimate that sizes
# their next grant. Purely a heuristic input, so it stays in memory: losing it
# on restart costs one grant sized from the configured rate instead of the
# observed one, which is the conservative direction anyway.
_LAST_SEEN: dict[str, float] = {}


def _observed_window(username: str) -> float:
    now = time.monotonic()
    previous = _LAST_SEEN.get(username)
    _LAST_SEEN[username] = now
    return max(0.0, now - previous) if previous else 0.0


def _forget_spend(username: str) -> None:
    """Drop a user's in-memory timing once they can no longer be served here."""
    _LAST_SEEN.pop(username, None)


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
        elapsed = _observed_window(username)
        new_bytes = _bill_new_bytes(user, grant_id, consumed_bytes)
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
        anchor_grant(user, grant)
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
