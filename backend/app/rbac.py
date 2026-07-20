"""Ownership scoping — the single place that decides what an admin may see.

Two roles:
  * `superadmin` — sees and manages everything, including which operator created
    each VPN account.
  * `admin` (reseller) — sees ONLY the users they created. Not other operators'
    accounts, not their counts, and not any server-wide aggregate (traffic,
    throughput, host health). They exist to provision accounts and hand them to
    their own customers.

Every scoped endpoint routes through here rather than re-deriving the rule, so a
new endpoint cannot quietly ship with a different (or missing) notion of
ownership — which is exactly how the traffic-total and daily-usage leaks got in.

Convention: `None` means "no restriction" (superadmin). An empty set means a
reseller who owns nothing — which must still filter everything out, so callers
must test `is None`, never truthiness.
"""

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Admin, User


def scope_users(stmt: Select, admin: Admin) -> Select:
    """Restrict a SELECT over `users` to what this admin owns."""
    if admin.is_superadmin:
        return stmt
    return stmt.where(User.created_by_admin_id == admin.id)


async def owned_usernames(db: AsyncSession, admin: Admin) -> set[str] | None:
    """Usernames this admin may see, or None for "everything" (superadmin)."""
    if admin.is_superadmin:
        return None
    rows = await db.execute(select(User.username).where(User.created_by_admin_id == admin.id))
    return {u for (u,) in rows.all()}


def visible(allowed: set[str] | None, username: str) -> bool:
    """Whether `username` is inside the caller's scope."""
    return allowed is None or username in allowed


def scoped_live_bytes(live_by_user: dict[str, int], allowed: set[str] | None) -> int:
    """Live overlay bytes limited to the caller's scope.

    The dashboard's "total"/"today" figures add this to committed usage; using
    the global `live_total` here is what let a reseller read the whole
    platform's throughput off their own dashboard.
    """
    if allowed is None:
        return sum(live_by_user.values())
    return sum(b for u, b in live_by_user.items() if u in allowed)
