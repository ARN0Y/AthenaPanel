"""Database models."""

import secrets
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base

# The master terminates users itself, so it IS a node — node 1. Every session,
# usage sample and ledger row carries the node that produced it, and the local
# server goes through exactly the same path a remote node will, so there is
# never a second "special" code path to keep in sync.
LOCAL_NODE_ID = 1


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _token() -> str:
    return secrets.token_urlsafe(24)


class Admin(Base):
    """Panel operator. role 'superadmin' sees everything and manages admins;
    role 'admin' (reseller) manages only the users they create."""

    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)  # bcrypt
    role: Mapped[str] = mapped_column(String(20), default="admin", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_create_users: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_users: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 0 = unlimited
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)

    @property
    def is_superadmin(self) -> bool:
        return self.role == "superadmin"


class AdminInvite(Base):
    """One-time link to provision a new admin (the operator sets their own
    username + password when opening the link)."""

    __tablename__ = "admin_invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True, default=_token, nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="admin", nullable=False)
    can_create_users: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_users: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    used_by: Mapped[int | None] = mapped_column(Integer, nullable=True)

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        exp = self.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return exp < _utcnow()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)  # plaintext for chap-secrets

    quota_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # Committed usage from CLOSED sessions only. Effective usage shown/enforced =
    # used_bytes + live counters of currently-open sessions (self-healing: it can
    # never drift below the authoritative kernel counters of active sessions).
    used_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    rate_up_kbps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rate_down_kbps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_sessions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Egress routing: "direct" (node's own IP) or "warp" (Cloudflare WARP).
    outbound: Mapped[str] = mapped_column(String(16), default="direct", nullable=False)
    # "ipsec" = L2TP/IPsec (default, encrypted) | "raw" = L2TP without IPsec.
    # Selects which entry host the customer is given; see config.l2tp_raw_address.
    l2tp_mode: Mapped[str] = mapped_column(String(8), default="ipsec", nullable=False)

    # Which admin owns/created this user (NULL = legacy/superadmin-owned)
    created_by_admin_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)

    @property
    def password(self) -> str:
        return self.password_hash

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        exp = self.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return exp < _utcnow()

    @property
    def quota_exceeded(self) -> bool:
        return self.quota_bytes > 0 and self.used_bytes >= self.quota_bytes

    @property
    def enabled_for_auth(self) -> bool:
        return self.is_active and not self.is_expired and not self.quota_exceeded


class Node(Base):
    """A server that terminates VPN sessions. Node 1 is the master itself.

    The master is deliberately modelled as an ordinary node so that local
    termination uses the same tables, the same accounting path and the same
    authority rules a remote node will — one code path, not two.
    """

    __tablename__ = "nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    # True only for node 1 (this server). Local state is read from sysfs
    # directly, so it never depends on a network report to stay authoritative.
    is_local: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Control address the agent connects from / is reached at. Empty for local.
    address: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    agent_version: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    # Last time this node produced a trustworthy report. A node that has gone
    # quiet loses authority: its sessions are held, never finalized on a guess.
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)


class Session(Base):
    __tablename__ = "sessions"

    # ifname is only unique WITHIN a node: every node has its own ppp0.
    __table_args__ = (
        UniqueConstraint("node_id", "ifname", name="uq_sessions_node_ifname"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[int] = mapped_column(Integer, default=LOCAL_NODE_ID, index=True, nullable=False)
    username: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    ifname: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    peer_ip: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    pid: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    proto: Mapped[str] = mapped_column(String(8), default="", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    # Last sysfs counters seen by the poller (authoritative, monotonic per iface).
    last_rx: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    last_tx: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # Billing baseline: this session's usage = (last_rx-base_rx)+(last_tx-base_tx).
    # 0 for a fresh session; bumped to the current counter on quota-reset so the
    # live overlay restarts from zero without losing the interface counter.
    base_rx: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    base_tx: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # Consecutive polls the iface was missing; finalize only after >=2 (debounce
    # a transient sysfs read miss so we never drop tracking of a live session).
    # ONLY counted while the owning node is authoritative — see stale_since.
    gone_polls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Set when the owning node stops reporting. A silent node means "unknown",
    # not "disconnected": finalizing on silence would commit the bytes, delete
    # the row, and then bill the SAME session a second time when the node comes
    # back with it still alive. Held rows are resumed, never double-counted.
    stale_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TrafficSample(Base):
    __tablename__ = "traffic_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True, nullable=False)
    online_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rx_bps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tx_bps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class UsageSample(Base):
    """Per-session cumulative counter snapshot, one row per poll per interface.

    The authoritative accounting time-series (a TimescaleDB hypertable on
    Postgres). Usage is reconstructable from it and it survives restarts, so it
    can never under-count. Orphan interfaces (no user mapping) are still recorded
    with username='' and flagged, so nothing is ever silently dropped.
    """

    __tablename__ = "usage_samples"

    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, default=_utcnow)
    ifname: Mapped[str] = mapped_column(String(32), primary_key=True)
    # NOTE: node_id is intentionally NOT part of the primary key yet. This is a
    # 12M-row TimescaleDB hypertable; widening its PK rebuilds the index on every
    # chunk, and it buys nothing while node 1 is the only node — (ts, ifname) is
    # still unique. The PK must be widened to (ts, node_id, ifname) BEFORE the
    # first remote node starts reporting, or two nodes' ppp0 will collide and
    # take down a whole sample batch. Tracked as a Phase 2 prerequisite.
    node_id: Mapped[int] = mapped_column(Integer, default=LOCAL_NODE_ID, nullable=False)
    username: Mapped[str] = mapped_column(String(128), default="", index=True, nullable=False)
    proto: Mapped[str] = mapped_column(String(8), default="", nullable=False)
    rx_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tx_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    rx_rate_bps: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tx_rate_bps: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)


class AccountingRecord(Base):
    """Closed-session accounting ledger (replaces the CSV log).

    One row per finished session, written at finalize. Period/total traffic and
    the connection-events view are computed from this table — fast, exact, and
    immune to logrotate truncation.
    """

    __tablename__ = "accounting"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[int] = mapped_column(Integer, default=LOCAL_NODE_ID, index=True, nullable=False)
    username: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    proto: Mapped[str] = mapped_column(String(8), default="", nullable=False)
    ifname: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True, nullable=False)
    bytes_in: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)   # from client (upload)
    bytes_out: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)  # to client (download)
    duration: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True, nullable=False)
    actor: Mapped[str] = mapped_column(String(128), default="admin", nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="", nullable=False)


class AppSetting(Base):
    """Editable key/value settings (server address, protocol toggles, …).

    Overrides the .env defaults so operators can change them in the panel UI
    without touching the server. Missing keys fall back to .env defaults.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="", nullable=False)


class WgPeer(Base):
    """WireGuard credential + accounting for a user (one peer per user).

    WireGuard is the third protocol alongside L2TP/SSTP. The parent User owns the
    quota / expiry / rate / active flags (one account, shared across all three
    protocols); this table holds the keypair, the assigned tunnel address, and
    the self-healing accounting baseline (same counter-minus-base model as
    Session — the collector reads `wg show` and credits used_bytes + usage_samples
    with proto='wireguard'). Online = recent handshake.
    """

    __tablename__ = "wg_peers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    public_key: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    private_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    preshared_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    address: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # assigned /32 in the WG pool
    # Self-healing accounting (counter from `wg show` minus the billing base).
    base_rx: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    base_tx: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    last_rx: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    last_tx: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    last_handshake: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # A WireGuard peer is perpetual: it has no connect/disconnect, and it
    # re-handshakes about every two minutes. These three give it the same
    # "current session" notion L2TP/SSTP get from ip-up, so the live view can
    # show how long this peer has actually been online and how much IT used —
    # rather than seconds-since-last-rekey and a lifetime byte total.
    # NULL online_since = the peer is currently offline.
    online_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    session_base_rx: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    session_base_tx: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
