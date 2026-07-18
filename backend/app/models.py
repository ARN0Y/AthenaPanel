"""Database models."""

import secrets
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


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


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    ifname: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
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
    gone_polls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


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
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
