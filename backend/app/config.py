"""Application settings loaded from environment / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Admin login
    admin_username: str = "admin"
    admin_password: str = "changeme"

    # JWT
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 24

    # IPsec / network (informational for the panel; the VPN core is hwdsl2)
    vpn_psk: str = ""
    ppp_local_ip: str = "192.168.42.1"
    ppp_pool: str = "192.168.42.10-192.168.42.250"
    wan_iface: str = "eth0"

    # Client-facing endpoints shown in the copy-able profile (editable in panel)
    server_address: str = "lttp.topmeli.com"   # L2TP/IPsec endpoint
    sstp_address: str = "sstp.topmeli.com"      # SSTP (https) endpoint
    sub_address: str = "sb.topmeli.com:2087"    # subscription page host:port (no scheme)
    # Raw L2TP (no IPsec) entry. It MUST be a separate host from server_address:
    # IPsec is negotiated before the user is known, so the two modes cannot share
    # one endpoint. Empty -> the raw option stays hidden in the panel.
    l2tp_raw_address: str = ""
    l2tp_enabled: bool = True
    sstp_enabled: bool = False                  # enabled once setup-sstp.sh has run
    # Peer-IP prefix of the SSTP ip-pool (configs/accel-ppp-sstp.conf). Sessions
    # whose client IP starts with this are reported as SSTP, otherwise L2TP.
    sstp_subnet: str = "192.168.44."
    l2tp_raw_subnet: str = "192.168.45."   # pool of the raw (no-IPsec) xl2tpd instance

    # Secret URL prefix the panel is served under (nginx + frontend build).
    # "/" = root (legacy). Set to something like /admin-athena to hide it.
    panel_path: str = "/"

    # L2TP engine control (accel-ppp CLI socket; accel-cmd default port is 2001)
    accel_cli: str = "127.0.0.1:2001"

    # WireGuard (3rd protocol, alongside L2TP/SSTP — same user account/quota)
    wg_iface: str = "wg-panel"
    wg_enabled: bool = False
    wg_endpoint: str = ""               # public host:port clients dial, e.g. wg.topmeli.com:51820
    wg_server_pubkey: str = ""          # wg-panel server public key
    wg_pool: str = "10.10.0.0/16"       # client address pool (gw .1)
    wg_dns: str = "1.1.1.1, 8.8.8.8"
    wg_listen_port: int = 51820
    wg_mtu: int = 1320

    # Paths (pppd-compatible stack: accel-ppp or xl2tpd)
    chap_secrets: str = "/etc/ppp/chap-secrets"
    # Server field in chap-secrets. options.xl2tpd uses `name l2tpd`; `*`
    # (wildcard) matches it and is the most portable choice.
    chap_server_field: str = "*"
    acct_log: str = "/var/log/vpn-panel/accounting.log"
    vpn_db_path: str = "/var/lib/vpn-panel/vpn.db"
    pppd_pid_dir: str = "/var/run"

    # Bind
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # Quota enforcement
    quota_poll_seconds: int = 30

    # Accounting multiplier applied to ALL billed traffic. Server-side only —
    # never exposed by any API, never rendered in the panel/UI. 1.0 = exact
    # (billed == measured); e.g. 1.4 = bill +40%. Set via the .env file only.
    usage_multiplier: float = 1.0

    # Telegram backup bot
    tg_bot_token: str = ""
    tg_chat_id: str = ""
    tg_backup_enabled: bool = False

    # Full SQLAlchemy URL (env DATABASE_URL). Empty -> local SQLite (legacy).
    # Production: postgresql+asyncpg://vpnpanel:***@127.0.0.1:5432/vpnpanel
    database_url: str = ""

    # Postgres connection pool (ignored on SQLite). Sized for many concurrent
    # admins + the background tasks at 500+ users; stays well under PG's default
    # max_connections=100. Tunable via .env (DB_POOL_SIZE / DB_MAX_OVERFLOW).
    db_pool_size: int = 20
    db_max_overflow: int = 10

    @property
    def sqlalchemy_url(self) -> str:
        return self.database_url or f"sqlite+aiosqlite:///{self.vpn_db_path}"

    @property
    def is_postgres(self) -> bool:
        return self.sqlalchemy_url.startswith("postgresql")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
