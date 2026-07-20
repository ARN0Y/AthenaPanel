"""Accounting ledger (DB-backed, replaces the old CSV log).

Standard, RADIUS-style accounting:

  * The poller anchors each open session to the kernel's monotonic interface
    counters (self-healing — `used_bytes` can never drift below the live
    counters of active sessions, see tasks.py / models.User.used_bytes).
  * When a session ends it is finalized into the `accounting` table — one row
    per closed session with bytes_in/out + duration.
  * Totals, "today", and the connection-events view are computed from that table
    (+ the live contribution of currently-open sessions). No CSV, no logrotate
    truncation, no O(file) scans on every dashboard poll.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AccountingRecord


async def record_session(
    db: AsyncSession,
    *,
    username: str,
    proto: str,
    ifname: str,
    started_at: datetime | None,
    bytes_in: int,
    bytes_out: int,
    duration: int,
) -> None:
    """Append a closed-session row. Caller commits."""
    db.add(
        AccountingRecord(
            username=username,
            proto=proto or "",
            ifname=ifname or "",
            started_at=started_at,
            stopped_at=datetime.now(timezone.utc),
            bytes_in=max(0, int(bytes_in)),
            bytes_out=max(0, int(bytes_out)),
            duration=max(0, int(duration)),
        )
    )


async def ledger_total_bytes(db: AsyncSession, usernames: set[str] | None = None) -> int:
    """Sum of all closed-session traffic recorded in the ledger.

    `usernames=None` means every user (superadmin); a set restricts the sum to
    those accounts. See rbac — a reseller must never be handed a platform-wide
    total.
    """
    stmt = select(func.coalesce(func.sum(AccountingRecord.bytes_in + AccountingRecord.bytes_out), 0))
    if usernames is not None:
        stmt = stmt.where(AccountingRecord.username.in_(usernames or [""]))
    return int((await db.execute(stmt)).scalar_one() or 0)


async def ledger_today_bytes(db: AsyncSession, usernames: set[str] | None = None) -> int:
    """Sum of closed-session traffic whose stop time is today (UTC), scoped."""
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    stmt = (
        select(func.coalesce(func.sum(AccountingRecord.bytes_in + AccountingRecord.bytes_out), 0))
        .where(AccountingRecord.stopped_at >= start)
    )
    if usernames is not None:
        stmt = stmt.where(AccountingRecord.username.in_(usernames or [""]))
    return int((await db.execute(stmt)).scalar_one() or 0)


async def read_events(db: AsyncSession, limit: int = 200, usernames: set[str] | None = None) -> list[dict]:
    """Most recent connection-end events (newest first), scoped.

    The filter is applied in SQL, BEFORE the limit: filtering a globally-limited
    page in Python made a reseller's own events vanish whenever other operators'
    users were busier than their own.
    """
    stmt = select(AccountingRecord).order_by(AccountingRecord.stopped_at.desc())
    if usernames is not None:
        stmt = stmt.where(AccountingRecord.username.in_(usernames or [""]))
    rows = (await db.execute(stmt.limit(limit))).scalars().all()
    return [
        {
            "ts": r.stopped_at,
            "username": r.username,
            "in_octets": r.bytes_in,
            "out_octets": r.bytes_out,
            "total_octets": r.bytes_in + r.bytes_out,
            "session_time": r.duration,
        }
        for r in rows
    ]
