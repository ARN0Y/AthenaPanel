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
    # Egress routing, by NAME: the built-ins "direct" (this host's own address)
    # and "warp" (Cloudflare WARP), or the name of a row in `outbounds`.
    #
    # A name rather than a foreign key on purpose: the built-ins have no row, and
    # deleting an outbound must degrade its users to direct rather than fail or
    # cascade. outbound.normalize() resolves an unknown name to "direct", so a
    # dangling reference is safe by construction.
    #
    # Enforced on the host that terminates the session — see outbound.py. For a
    # user served by a REMOTE node this field is carried in UserSync but has no
    # effect there, because their traffic never crosses this host.
    outbound: Mapped[str] = mapped_column(String(32), default="direct", nullable=False)
    # "ipsec" = L2TP/IPsec (default, encrypted) | "raw" = L2TP without IPsec.
    # Selects which entry host the customer is given; see config.l2tp_raw_address.
    l2tp_mode: Mapped[str] = mapped_column(String(8), default="ipsec", nullable=False)
    # Which node terminates this user. Defaults to LOCAL_NODE_ID so every
    # account that existed before nodes stays exactly where it already is, and
    # so a user created without thinking about nodes lands somewhere real
    # instead of nowhere. One node per user is the business rule; if that ever
    # becomes a set, this column becomes the "primary" and a join table carries
    # the rest, without any of the surrounding logic changing shape.
    node_id: Mapped[int] = mapped_column(
        Integer, default=LOCAL_NODE_ID, index=True, nullable=False
    )
    # An admin asked to kick this account off its node. The panel cannot signal
    # a remote node directly — the hub is a separate process — so this column is
    # the queue: the panel sets it, the hub sends the Disconnect on the node's
    # next report and clears it. NULL means nothing is pending. Local users
    # never use it; the panel kills their pppd itself, in-process.
    disconnect_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # How much of the credit grant a node is holding has already been billed.
    #
    # `consumed_bytes` on the wire is CUMULATIVE under one grant, so the amount
    # that is new is whatever exceeds this watermark. It lives on the row rather
    # than in the hub's memory because it is written in the same transaction as
    # used_bytes — losing it would mean the next request re-bills everything the
    # agent has spent under the grant it still holds, which is exactly what a
    # hub restart used to do to every user with a live session.
    #
    # Zero means "never seen". That is treated as unknown rather than as an old
    # grant, so the first request after this column appeared bills nothing
    # instead of billing a whole grant twice.
    credit_grant_id: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    credit_billed_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

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
    # Shared secret the agent presents to open its stream. Generated by the
    # panel; rotating it simply invalidates the old one on the next reconnect.
    # Empty for the local node, which has no agent to authenticate.
    token: Mapped[str] = mapped_column(String(64), default="", index=True, nullable=False)
    # Last thing the node told us about itself. Diagnostics only — never used
    # to decide anything, so a lying or stale agent cannot affect billing.
    hostname: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    kernel: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    # The node's most recent report, as JSON. One row per node, overwritten —
    # this is current state, not a time series. Persisted rather than kept in
    # the hub's memory so that any process (the panel, an operator, the Phase 1
    # verifier) can read what the node last said without being the hub.
    last_report: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # Traffic OBSERVED on this node, accumulated from absolute counters with a
    # watermark. Deliberately separate from billing: this answers "how much has
    # gone through this machine", which is a capacity question, while
    # users.used_bytes answers "what do we charge", which is scaled and has its
    # own rules. Mixing them would make one of the two wrong.
    rx_total_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tx_total_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # Throughput from the last two observations. Stored rather than computed on
    # read because the two samples it needs are seconds apart, not request-time.
    rx_rate_bps: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    tx_rate_bps: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    rate_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Set by the panel, noticed by the hub, which then drops the node's stream.
    # The agent's own backoff reconnects within seconds. Done through the
    # database because the hub is a separate process — there is no other channel
    # between them, and inventing one for a button would not be worth it.
    reconnect_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Set by the panel when an account on this node changes; noticed by the hub,
    # which is a separate process. A timestamp is enough because the hub always
    # sends the WHOLE list — how many changes happened in between is irrelevant.
    sync_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Per-node service ports. A node in one country may have to run WireGuard on
    # 443 because 51820 is blocked, while another uses the default; without this
    # every node has to look the same. Feeds config/subscription generation.
    wg_port: Mapped[int] = mapped_column(Integer, default=51820, nullable=False)
    sstp_port: Mapped[int] = mapped_column(Integer, default=443, nullable=False)
    l2tp_port: Mapped[int] = mapped_column(Integer, default=1701, nullable=False)

    # --- external proxy: what the CUSTOMER connects to -----------------------
    #
    # `address` above is the node's own address — the machine abroad, used by us
    # for operations and never handed to a customer. These four are the other
    # half: the relay a customer actually dials, which in this deployment is an
    # Iranian entry that forwards over the backhaul. The two are genuinely
    # different hosts and conflating them produces configs that cannot connect.
    #
    # They are per-protocol because in practice they already are: L2TP/IPsec,
    # SSTP and WireGuard share one entry while raw L2TP needs its own, since
    # IPsec is negotiated before the user is known and the two modes cannot live
    # on the same address.
    #
    # Empty means "use the panel-wide setting". That is what keeps node 1
    # behaving exactly as it does today without copying anything into it.
    # This node's OWN WireGuard server key and listen port, reported by its
    # agent. A customer's config must name the key of the machine they actually
    # connect to; handing out the master's key produces a config that looks
    # correct and never completes a handshake.
    wg_public_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    ext_l2tp_address: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    ext_l2tp_raw_address: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    ext_sstp_address: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    ext_wg_endpoint: Mapped[str] = mapped_column(String(255), default="", nullable=False)
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
    # node_id is part of the key: every node has a ppp0, so (ts, ifname) alone
    # is unique only while one node exists. Two nodes sampling in the same
    # second would collide, and Postgres rejects the whole batch rather than
    # the one duplicate row — losing every other node's sample too.
    #
    # An existing database does NOT get this from the model; widening the key
    # on a 12M-row hypertable takes an exclusive lock and rebuilds the index on
    # every chunk, which must not happen implicitly during a panel restart.
    # Run migrate-usage-pk.sh for that, deliberately. Fresh installs are created
    # correctly from here.
    node_id: Mapped[int] = mapped_column(Integer, primary_key=True, default=LOCAL_NODE_ID, nullable=False)
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


class Outbound(Base):
    """An egress location: a WireGuard tunnel from this host to a server the
    operator owns, that selected users' traffic leaves through.

    "direct" and "warp" are NOT rows here — they are built-ins whose plumbing
    predates this table (setup-warp.sh). A row is an operator-added location,
    created by pasting the registration line that athena-outbound.sh prints on
    the remote server. Everything the host needs to bring the tunnel up lives in
    this row, so the plumbing can always be rebuilt from the database alone.

    Why the resources are stored rather than derived: fwmark, table and rule
    priority must stay stable for the life of an outbound. Deriving them from
    the row id would be fine until a row is deleted and the ids shift meaning,
    and deriving them from the name would change them on rename. They are
    allocated once at creation and never move.
    """

    __tablename__ = "outbounds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Slug. Also names the interface (ob-<name>) and the ipset, so it is capped
    # well under IFNAMSIZ (16 including the NUL) and validated to [a-z0-9-].
    name: Mapped[str] = mapped_column(String(12), unique=True, index=True, nullable=False)
    label: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    # ISO 3166-1 alpha-2, lowercase, or "" for none. Purely cosmetic — the panel
    # renders it as a flag next to the name. Stored as the code rather than the
    # emoji so it stays sortable, searchable and safe to put in a URL, and so a
    # client that cannot render the emoji can still show something sensible.
    country: Mapped[str] = mapped_column(String(2), default="", nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="wireguard", nullable=False)

    # The remote end.
    endpoint: Mapped[str] = mapped_column(String(128), default="", nullable=False)  # host:port
    public_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    preshared_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    peer_address: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    # This host's end of the tunnel.
    private_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    address: Mapped[str] = mapped_column(String(64), default="", nullable=False)  # our /32 inside the tunnel
    mtu: Mapped[int] = mapped_column(Integer, default=1380, nullable=False)

    # Allocated once, never reused while the row lives.
    fwmark: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    table_id: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rule_priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    # Last observation from the health check, for the Outbounds tab. Display
    # only: routing never consults these, so a stale probe can never strand a
    # user's traffic.
    last_status: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    last_egress_ip: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def ifname(self) -> str:
        return f"ob-{self.name}"

    @property
    def ipset(self) -> str:
        return f"ob-{self.name}"
