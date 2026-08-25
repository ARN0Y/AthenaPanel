"""Pydantic v2 request/response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ---- Auth ----
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    username: str = ""
    role: str = "admin"


class AdminPasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=4)


# ---- Admins (multi-operator RBAC) ----
class AdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    is_active: bool
    can_create_users: bool
    max_users: int
    created_at: datetime
    last_login: datetime | None = None
    note: str
    user_count: int = 0  # number of users this admin owns


class AdminCreate(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=4, max_length=256)
    role: str = "admin"
    can_create_users: bool = True
    max_users: int = 0
    note: str = ""


class AdminUpdate(BaseModel):
    password: str | None = Field(default=None, max_length=256)
    is_active: bool | None = None
    can_create_users: bool | None = None
    max_users: int | None = None
    note: str | None = None


class InviteCreate(BaseModel):
    role: str = "admin"
    can_create_users: bool = True
    max_users: int = 0
    note: str = ""
    expires_in_hours: int = 72


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    token: str
    role: str
    can_create_users: bool
    max_users: int
    note: str
    created_at: datetime
    expires_at: datetime | None
    used: bool


class InviteInfo(BaseModel):
    """Public, safe view of an invite (no secrets) for the accept page."""

    role: str
    valid: bool
    note: str = ""


class InviteAccept(BaseModel):
    token: str
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=4, max_length=256)


# ---- Users ----
class UserBase(BaseModel):
    quota_bytes: int = 0
    rate_up_kbps: int = 0
    rate_down_kbps: int = 0
    is_active: bool = True
    expires_at: datetime | None = None
    note: str = ""
    outbound: str = "direct"
    l2tp_mode: str = "ipsec"   # "ipsec" (L2TP/IPsec) | "raw" (L2TP without IPsec)


class UserCreate(UserBase):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)
    # Superadmin only: create the account already owned by a reseller.
    owner_admin_id: int | None = None


class UserUpdate(BaseModel):
    password: str | None = Field(default=None, max_length=256)
    quota_bytes: int | None = None
    rate_up_kbps: int | None = None
    rate_down_kbps: int | None = None
    is_active: bool | None = None
    expires_at: datetime | None = None
    note: str | None = None
    outbound: str | None = None
    l2tp_mode: str | None = None
    node_id: int | None = None
    # Superadmin only: hand this account to another operator.
    owner_admin_id: int | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    password: str = ""   # plaintext (chap-secrets); admin-only API, used for profiles
    quota_bytes: int
    used_bytes: int
    rate_up_kbps: int
    rate_down_kbps: int
    is_active: bool
    expires_at: datetime | None
    created_at: datetime
    last_seen: datetime | None = None
    total_sessions: int = 0
    note: str
    created_by_admin_id: int | None = None

    # Derived / live
    created_by_username: str = ""
    is_expired: bool = False
    quota_exceeded: bool = False
    online: bool = False
    sub_token: str = ""  # signed token for the public /sub/<token> link
    outbound: str = "direct"
    l2tp_mode: str = "ipsec"
    node_id: int = 1
    node_name: str = ""
    # The addresses THIS user should be handed, already resolved from their
    # node's external proxy with the panel-wide setting as fallback. Resolved
    # server-side so the panel, the subscription page and anything else agree by
    # construction instead of each re-deriving it and drifting.
    endpoint_l2tp: str = ""
    endpoint_l2tp_raw: str = ""
    endpoint_sstp: str = ""
    endpoint_wg: str = ""


# ---- Login-page appearance ----
class BrandingOut(BaseModel):
    """Served unauthenticated — cosmetic fields only. See branding.py."""

    brand_name: str = "ATHENA"
    login_tagline: str = ""
    login_layout: str = "split-right"
    login_focal: str = "center"
    login_overlay: int = 45
    login_image_url: str = ""
    login_image_id: str = ""
    has_image: bool = False


class BrandingUpdate(BaseModel):
    brand_name: str | None = Field(default=None, max_length=48)
    login_tagline: str | None = Field(default=None, max_length=160)
    login_layout: str | None = None
    login_focal: str | None = None
    login_overlay: int | None = None
    login_image_url: str | None = Field(default=None, max_length=512)
    # Which stored image to show; "" clears it.
    login_image_id: str | None = Field(default=None, max_length=64)


class BrandingImage(BaseModel):
    """One entry in the artwork library."""

    id: str
    content_type: str
    bytes: int
    uploaded_at: float
    active: bool = False


class BulkAction(BaseModel):
    ids: list[int]
    action: str  # "enable" | "disable" | "delete" | "reset-quota" | "assign"
    # Required by "assign": the operator the selected accounts move to.
    owner_admin_id: int | None = None


# ---- Sessions ----
class SessionOut(BaseModel):
    username: str
    ifname: str
    ip: str
    # Which server is actually terminating this session. Carried on every
    # session, not only remote ones, so nothing downstream has to guess what a
    # missing value means — and so the live overlay can tell node 1's sessions
    # (billed at finalize) from a node's (billed continuously by the credit path).
    node_id: int = 1
    node_name: str = ""
    protocol: str = "L2TP"   # "L2TP" or "SSTP" (derived from the client IP pool)
    calling_station: str = ""
    uptime_seconds: int = 0
    rx_bytes: int = 0   # from client (upload)
    tx_bytes: int = 0   # to client (download)
    rx_rate_bps: int = 0  # live upload bits/s
    tx_rate_bps: int = 0  # live download bits/s
    state: str = ""


# ---- Stats / dashboard ----
class TopUser(BaseModel):
    username: str
    used_bytes: int
    quota_bytes: int
    online: bool


class QuotaUser(BaseModel):
    """A quota'd user for the dashboard's "running low on data" table."""

    username: str
    used_bytes: int      # effective (committed + live overlay)
    quota_bytes: int
    percent: float       # used/quota * 100 (can exceed 100 when over quota)
    online: bool


class StatsOut(BaseModel):
    total_users: int
    active_users: int
    online_count: int
    traffic_today_bytes: int
    traffic_total_bytes: int
    quota_warnings: int
    expired_users: int
    rx_rate_bps: int = 0
    tx_rate_bps: int = 0
    top_users: list[TopUser] = []
    near_quota: list[QuotaUser] = []


class TrafficPoint(BaseModel):
    ts: datetime
    online_count: int
    rx_bps: int
    tx_bps: int


class SystemStats(BaseModel):
    cpu_percent: float
    mem_total: int
    mem_used: int
    mem_percent: float
    disk_total: int
    disk_used: int
    disk_percent: float
    net_rx_bps: int
    net_tx_bps: int
    load_1: float
    load_5: float
    load_15: float
    uptime_seconds: int
    hostname: str
    kernel: str


class EventOut(BaseModel):
    ts: datetime
    username: str
    in_octets: int
    out_octets: int
    total_octets: int
    session_time: int


class AuditEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ts: datetime
    actor: str
    action: str
    target: str
    detail: str


class HealthOut(BaseModel):
    status: str
    xl2tpd: bool
    ipsec: bool
    db: bool
    accounting_log: bool
    uptime_seconds: float


class SettingsOut(BaseModel):
    vpn_psk: str
    wan_iface: str
    ppp_local_ip: str
    ppp_pool: str
    admin_username: str
    chap_secrets: str
    # editable, client-facing (used by the copy-able profile)
    server_address: str = ""
    sstp_address: str = ""
    sub_address: str = ""
    l2tp_raw_address: str = ""   # separate entry for L2TP without IPsec ("" = off)
    l2tp_enabled: bool = True
    sstp_enabled: bool = False


class PanelSettingsUpdate(BaseModel):
    server_address: str | None = Field(default=None, max_length=255)
    sstp_address: str | None = Field(default=None, max_length=255)
    sub_address: str | None = Field(default=None, max_length=255)
    l2tp_raw_address: str | None = Field(default=None, max_length=255)
    l2tp_enabled: bool | None = None
    sstp_enabled: bool | None = None


# ---- Internal (ppp hooks) ----
class RateOut(BaseModel):
    username: str
    rate_up_kbps: int
    rate_down_kbps: int
    allowed: bool


class SessionUpIn(BaseModel):
    username: str
    ifname: str
    peer_ip: str = ""
    pid: int = 0


class SessionUpOut(BaseModel):
    """Verdict returned to the ip-up hook. `allowed=false` -> drop the link."""

    detail: str = "registered"
    allowed: bool = True
    reason: str = ""


class SessionDownIn(BaseModel):
    username: str
    ifname: str
    in_octets: int = 0
    out_octets: int = 0
    session_time: int = 0


# --- nodes -----------------------------------------------------------------

class NodeCreate(BaseModel):
    name: str
    # What clients are pointed at for this node. Optional at creation on
    # purpose: at that moment the operator usually has a bare server and no
    # entry yet, and demanding one up front just invites a placeholder that
    # nobody ever corrects.
    address: str = ""
    note: str = ""
    wg_port: int = 51820
    sstp_port: int = 443
    l2tp_port: int = 1701


class NodeUpdate(BaseModel):
    name: str | None = None
    address: str | None = None
    note: str | None = None
    enabled: bool | None = None
    wg_port: int | None = None
    sstp_port: int | None = None
    l2tp_port: int | None = None
    ext_l2tp_address: str | None = None
    ext_l2tp_raw_address: str | None = None
    ext_sstp_address: str | None = None
    ext_wg_endpoint: str | None = None


class NodeCreated(BaseModel):
    """Returned once, at registration or rotation. The panel keeps no copy of
    the key — only the CA — so this is the only chance to save it."""

    id: int
    name: str
    token: str
    client_cert: str
    client_key: str
    ca_cert: str


class NodeOut(BaseModel):
    id: int
    name: str
    is_local: bool
    enabled: bool
    address: str
    note: str
    agent_version: str
    hostname: str
    kernel: str
    online: bool
    last_seen_seconds: int | None = None
    sessions: int = 0
    ppp_count: int = 0
    wg_count: int = 0
    uptime_seconds: int = 0
    load1: float = 0.0
    mem_total_bytes: int = 0
    mem_available_bytes: int = 0
    xl2tpd_ok: bool = False
    ipsec_ok: bool = False
    accel_ppp_ok: bool = False
    wireguard_ok: bool = False
    # Traffic observed on this node. A capacity figure, not an invoice — it is
    # unscaled and counts every byte the machine moved.
    rx_total_bytes: int = 0
    tx_total_bytes: int = 0
    rx_rate_bps: int = 0
    tx_rate_bps: int = 0
    wg_port: int = 51820
    sstp_port: int = 443
    l2tp_port: int = 1701
    # Customer-facing. Empty means the node inherits the panel-wide setting.
    ext_l2tp_address: str = ""
    ext_l2tp_raw_address: str = ""
    ext_sstp_address: str = ""
    ext_wg_endpoint: str = ""


# ---- Outbounds (operator-added egress locations) ----
class OutboundCreate(BaseModel):
    # Names the interface (ob-<name>) and the ipset, so it is short and
    # restricted; validated properly in outbound.valid_name(). It is also what
    # the operator sees — there is no separate display name, because two names
    # for one thing is a question nobody wants to answer twice.
    name: str = Field(min_length=2, max_length=12)
    # ISO 3166-1 alpha-2, or "" for no flag.
    country: str = ""
    note: str = ""
    port: int = Field(default=51833, ge=1, le=65535)
    # 1380 = 1500 - 60 (WireGuard) - 60 of headroom for whatever the egress
    # server's own path already costs. Lower it if that path is itself a tunnel.
    mtu: int = Field(default=1380, ge=1280, le=1500)


class OutboundRegister(BaseModel):
    """The single line athena-outbound.sh prints when it finishes."""

    registration: str


class OutboundUpdate(BaseModel):
    """Both optional: None means "leave it alone", which is what lets the flag
    be cleared (empty string) without that reading as "no change"."""

    name: str | None = Field(default=None, max_length=12)
    country: str | None = Field(default=None, max_length=2)


# ---- Public API v1 ----
#
# Separate from the panel's internal schemas on purpose. The frontend and the
# backend ship together and can change shape in one commit; a bot cannot. These
# are the contract, and they are allowed to be duller and more explicit than
# what the UI uses — bytes are accompanied by gigabytes, timestamps are always
# ISO-8601 UTC, and nothing is omitted just because the UI happens to know it.
class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    scopes: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    rate_limit: int = Field(default=0, ge=0, le=100_000)
    note: str = ""


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    prefix: str
    scopes: list[str] = Field(default_factory=list)
    is_active: bool
    created_at: datetime
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    request_count: int = 0
    rate_limit: int = 0
    note: str = ""
    owner: str = ""


class ApiKeyCreated(ApiKeyOut):
    """Only ever returned once, by the create call."""

    key: str


class V1UserOut(BaseModel):
    """A VPN account as the public API describes it."""

    username: str
    password: str
    enabled: bool
    online: bool
    # Quota. limit_bytes 0 means unlimited; the *_gb mirrors exist so a bot can
    # render a number without doing byte arithmetic in three places.
    limit_bytes: int
    limit_gb: float
    used_bytes: int
    used_gb: float
    remaining_bytes: int | None      # None when unlimited
    remaining_gb: float | None
    usage_percent: float | None
    quota_exceeded: bool
    # Time
    expires_at: datetime | None
    days_remaining: int | None
    expired: bool
    created_at: datetime
    last_seen: datetime | None
    total_sessions: int
    # Placement and policy
    node_id: int
    node_name: str
    outbound: str
    l2tp_mode: str
    rate_up_kbps: int
    rate_down_kbps: int
    note: str
    owner: str
    # Everything a client needs to connect, resolved for THIS user's node.
    endpoints: dict = Field(default_factory=dict)
    subscription_url: str = ""


class V1UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    # Omit to have one generated — the common case for a bot handing out
    # accounts, and better than every bot author inventing their own generator.
    password: str | None = Field(default=None, max_length=256)
    limit_gb: float = Field(default=0, ge=0)
    duration_days: int | None = Field(default=None, ge=0)
    expires_at: datetime | None = None
    enabled: bool = True
    node_id: int | None = None
    outbound: str | None = None
    l2tp_mode: str | None = None
    rate_up_kbps: int = Field(default=0, ge=0)
    rate_down_kbps: int = Field(default=0, ge=0)
    note: str = ""


class V1UserUpdate(BaseModel):
    password: str | None = Field(default=None, max_length=256)
    limit_gb: float | None = Field(default=None, ge=0)
    expires_at: datetime | None = None
    enabled: bool | None = None
    node_id: int | None = None
    outbound: str | None = None
    l2tp_mode: str | None = None
    rate_up_kbps: int | None = Field(default=None, ge=0)
    rate_down_kbps: int | None = Field(default=None, ge=0)
    note: str | None = None


class V1Extend(BaseModel):
    """Add to an account rather than set it — the operation a renewal actually
    is. Setting an absolute value races with the customer's clock; adding does
    not."""

    days: int = Field(default=0, ge=0, le=3650)
    gb: float = Field(default=0, ge=0)
    reset_usage: bool = False


class V1SessionOut(BaseModel):
    username: str
    node_id: int
    node_name: str
    ifname: str
    protocol: str
    peer_ip: str
    started_at: datetime | None
    duration_seconds: int
    bytes_in: int
    bytes_out: int
    bytes_total: int


class V1Page(BaseModel):
    """Every list in this API answers with the same envelope."""

    items: list = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 50
    pages: int = 1
