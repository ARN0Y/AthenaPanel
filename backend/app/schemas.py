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


class BulkAction(BaseModel):
    ids: list[int]
    action: str  # "enable" | "disable" | "delete" | "reset-quota"


# ---- Sessions ----
class SessionOut(BaseModel):
    username: str
    ifname: str
    ip: str
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


class SessionDownIn(BaseModel):
    username: str
    ifname: str
    in_octets: int = 0
    out_octets: int = 0
    session_time: int = 0
