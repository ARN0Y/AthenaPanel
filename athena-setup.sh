#!/usr/bin/env bash
###############################################################################
#  Athena Panel — one-shot installer  (Ubuntu 22.04 / 24.04, run as root)
#
#  Brings up an IDENTICAL panel on a fresh server: PostgreSQL 16 + TimescaleDB,
#  the FastAPI backend, the built React frontend, nginx, systemd, WireGuard —
#  and RESTORES all data + settings from an athena-backup bundle. Anything that
#  is environment-specific (domain, PSK, admin login, VPN core) is asked
#  interactively, with sane defaults; nothing is guessed silently.
#
#      sudo bash athena-setup.sh                         # fresh install (prompts)
#      sudo BACKUP=/root/athena-backup-*.zip bash athena-setup.sh   # restore
#
#  Run it from the cloned repo root (bootstrap.sh clones the private repo for
#  you). Re-runnable: existing packages/DB are detected and reused.
###############################################################################
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR=/opt/vpn-panel
LOG_DIR=/var/log/vpn-panel
DB_DIR=/var/lib/vpn-panel
DB_NAME=vpnpanel
DB_USER=vpnpanel
TTY=/dev/tty

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '   \033[1;32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }
hr()   { printf '\033[0;90m%s\033[0m\n' "----------------------------------------------------------------------------"; }

ask() {  # ask "Prompt" "default"  -> echoes the answer (default if empty/non-interactive)
    local prompt="$1" def="${2:-}" ans
    if [ "${NONINTERACTIVE:-0}" = "1" ]; then echo "$def"; return; fi
    if [ -n "$def" ]; then printf '\033[1;36m ? \033[0m%s \033[0;90m[%s]\033[0m: ' "$prompt" "$def" > "$TTY"
    else printf '\033[1;36m ? \033[0m%s: ' "$prompt" > "$TTY"; fi
    read -r ans < "$TTY" || true
    echo "${ans:-$def}"
}
ask_secret() {  # ask_secret "Prompt" "default"
    local prompt="$1" def="${2:-}" ans
    if [ "${NONINTERACTIVE:-0}" = "1" ]; then echo "$def"; return; fi
    printf '\033[1;36m ? \033[0m%s \033[0;90m[keep]\033[0m: ' "$prompt" > "$TTY"
    read -rs ans < "$TTY" || true; printf '\n' > "$TTY"
    echo "${ans:-$def}"
}
yesno() {  # yesno "Prompt" "Y|N"  -> returns 0 for yes
    local def="${2:-Y}" ans
    [ "${NONINTERACTIVE:-0}" = "1" ] && { [ "$def" = "Y" ]; return; }
    ans=$(ask "$1 (y/n)" "$def"); case "$ans" in [Yy]*) return 0;; *) return 1;; esac
}

[ "$(id -u)" -eq 0 ] || die "Run as root (sudo bash athena-setup.sh)"
. /etc/os-release 2>/dev/null || true
[ "${ID:-}" = "ubuntu" ] || warn "Tested on Ubuntu 22.04/24.04 — '${ID:-unknown}' may need adjustments"

hr; log "Athena Panel installer"; hr

# =========================================================================
# 0) Restore bundle — unpack first so its .env can seed the config
# =========================================================================
RESTORE_DIR=""
if [ -z "${BACKUP:-}" ] && ls /root/athena-backup-*.zip >/dev/null 2>&1; then
    CAND=$(ls -t /root/athena-backup-*.zip | head -1)
    yesno "Found backup ${CAND##*/} — restore from it?" Y && BACKUP="$CAND"
fi
if [ -n "${BACKUP:-}" ]; then
    [ -f "$BACKUP" ] || die "BACKUP file not found: $BACKUP"
    command -v unzip >/dev/null 2>&1 || { apt-get update -qq; apt-get install -y unzip >/dev/null; }
    RESTORE_DIR="$(mktemp -d)"
    unzip -qo "$BACKUP" -d "$RESTORE_DIR"
    ok "backup unpacked ($(cat "$RESTORE_DIR/MANIFEST.txt" 2>/dev/null | grep -E 'created_utc|db_mode' | tr '\n' ' '))"
fi

# =========================================================================
# 1) Configuration (restored .env wins; otherwise prompt with defaults)
# =========================================================================
log "[1/9] Configuration"
mkdir -p "$INSTALL_DIR" "$LOG_DIR" "$DB_DIR"
if [ -n "$RESTORE_DIR" ] && [ -f "$RESTORE_DIR/panel.env" ]; then
    cp "$RESTORE_DIR/panel.env" "$INSTALL_DIR/.env"
    ok "settings restored from backup (.env) — keeping identical secrets/PSK/multiplier"
elif [ -f "$INSTALL_DIR/.env" ]; then
    ok "reusing existing $INSTALL_DIR/.env"
else
    cp "$REPO_DIR/.env.example" "$INSTALL_DIR/.env"
    log "  fresh install — a few questions:"
    _u=$(ask "Admin username" "admin")
    _p=$(ask "Admin password" "$(openssl rand -hex 6)")
    _psk=$(ask "IPsec pre-shared key (VPN_PSK)" "$(openssl rand -hex 12)")
    _path=$(ask "Secret panel URL path" "/admin-athena")
    _srv=$(ask "L2TP endpoint clients dial (SERVER_ADDRESS)" "vpn.example.com")
    _dbpass="$(openssl rand -hex 16)"
    sed -i "s|^ADMIN_USERNAME=.*|ADMIN_USERNAME=${_u}|"       "$INSTALL_DIR/.env"
    sed -i "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=${_p}|"       "$INSTALL_DIR/.env"
    sed -i "s|^VPN_PSK=.*|VPN_PSK=${_psk}|"                   "$INSTALL_DIR/.env"
    sed -i "s|^PANEL_PATH=.*|PANEL_PATH=${_path}|"            "$INSTALL_DIR/.env"
    sed -i "s|^SERVER_ADDRESS=.*|SERVER_ADDRESS=${_srv}|"     "$INSTALL_DIR/.env"
    sed -i "s|^JWT_SECRET=.*|JWT_SECRET=$(openssl rand -hex 32)|" "$INSTALL_DIR/.env"
    grep -q '^DATABASE_URL=' "$INSTALL_DIR/.env" \
        && sed -i "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+asyncpg://${DB_USER}:${_dbpass}@127.0.0.1:5432/${DB_NAME}|" "$INSTALL_DIR/.env" \
        || echo "DATABASE_URL=postgresql+asyncpg://${DB_USER}:${_dbpass}@127.0.0.1:5432/${DB_NAME}" >> "$INSTALL_DIR/.env"
    warn "  generated admin pass: ${_p}   (change later in $INSTALL_DIR/.env)"
fi
chmod 600 "$INSTALL_DIR/.env"
set -a; . "$INSTALL_DIR/.env"; set +a
: "${PANEL_PORT:=80}"; : "${PANEL_PATH:=/admin-athena}"
PANEL_PATH="/${PANEL_PATH#/}"; PANEL_PATH="${PANEL_PATH%/}"; [ -z "$PANEL_PATH" ] && PANEL_PATH=/admin-athena
VITE_BASE="${PANEL_PATH}/"
# Derive DB credentials from DATABASE_URL (restored or generated)
DB_URL="${DATABASE_URL:-}"
[ -z "$DB_URL" ] && die "DATABASE_URL missing in .env — Postgres is required for this build"
DB_PASS="$(printf '%s' "$DB_URL" | sed -nE 's#.*://[^:]+:([^@]+)@.*#\1#p')"
ok "config ready (panel path ${PANEL_PATH}, db ${DB_NAME})"

# =========================================================================
# 2) Base packages
# =========================================================================
log "[2/9] Base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y curl wget ca-certificates gawk gnupg lsb-release unzip zip \
    iproute2 iptables python3 python3-venv python3-dev build-essential \
    nginx wireguard-tools qrencode conntrack >/dev/null
ok "base packages installed"

# =========================================================================
# 3) PostgreSQL 16 + TimescaleDB
# =========================================================================
log "[3/9] PostgreSQL 16 + TimescaleDB"
if ! command -v psql >/dev/null 2>&1; then
    install -d /usr/share/postgresql-common/pgdg
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list
    curl -fsSL https://packagecloud.io/timescale/timescaledb/gpgkey | gpg --dearmor \
        -o /etc/apt/trusted.gpg.d/timescaledb.gpg
    echo "deb https://packagecloud.io/timescale/timescaledb/ubuntu/ $(lsb_release -cs) main" \
        > /etc/apt/sources.list.d/timescaledb.list
    apt-get update -qq
    apt-get install -y postgresql-16 postgresql-client-16 timescaledb-2-postgresql-16 >/dev/null
    timescaledb-tune --quiet --yes >/dev/null 2>&1 || true
    systemctl restart postgresql
fi
systemctl enable --now postgresql >/dev/null 2>&1 || true
# role + database (idempotent) with the password from .env so the app connects
sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1 \
    || sudo -u postgres psql -qc "CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}';"
sudo -u postgres psql -qc "ALTER ROLE ${DB_USER} PASSWORD '${DB_PASS}';"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1 \
    || sudo -u postgres createdb -O "${DB_USER}" "${DB_NAME}"
sudo -u postgres psql -d "${DB_NAME}" -qc "CREATE EXTENSION IF NOT EXISTS timescaledb;" 2>/dev/null || true
ok "postgres + timescaledb ready; role/db '${DB_NAME}' ensured"

# =========================================================================
# 4) Restore the database (TimescaleDB-aware, permission-safe)
# =========================================================================
if [ -n "$RESTORE_DIR" ] && [ -f "$RESTORE_DIR/vpnpanel.dump" ]; then
    log "[4/9] Restoring database"
    EXISTING=$(sudo -u postgres psql -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" "${DB_NAME}" 2>/dev/null || echo 0)
    if [ "${EXISTING:-0}" -gt 0 ]; then
        yesno "  DB '${DB_NAME}' already has ${EXISTING} tables — DROP and restore fresh?" N \
            && { sudo -u postgres dropdb "${DB_NAME}"; sudo -u postgres createdb -O "${DB_USER}" "${DB_NAME}"; \
                 sudo -u postgres psql -d "${DB_NAME}" -qc "CREATE EXTENSION IF NOT EXISTS timescaledb;"; } \
            || warn "  keeping existing data — skipping restore"
    fi
    if [ "$(sudo -u postgres psql -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" "${DB_NAME}")" -eq 0 ]; then
        # backups are root:600 -> copy to a postgres-readable temp before pg_restore
        RTMP=/var/lib/postgresql/.athena-restore.dump
        install -o postgres -g postgres -m 600 "$RESTORE_DIR/vpnpanel.dump" "$RTMP"
        sudo -u postgres psql -d "${DB_NAME}" -qc "SELECT timescaledb_pre_restore();" 2>/dev/null || true
        sudo -u postgres pg_restore -d "${DB_NAME}" --no-owner "$RTMP" 2>/dev/null || true
        sudo -u postgres psql -d "${DB_NAME}" -qc "SELECT timescaledb_post_restore();" 2>/dev/null || true
        rm -f "$RTMP"
        ok "database restored ($(sudo -u postgres psql -tAc 'SELECT count(*) FROM users' "${DB_NAME}" 2>/dev/null || echo '?') users)"
    fi
    # WireGuard server keypair/NAT (so existing client configs keep working)
    [ -f "$RESTORE_DIR/wg-panel.conf" ] && { install -d -m 700 /etc/wireguard; install -m 600 "$RESTORE_DIR/wg-panel.conf" /etc/wireguard/wg-panel.conf; ok "wg-panel.conf restored"; }
else
    log "[4/9] Fresh database (no backup) — panel will create the schema on first boot"
fi

# =========================================================================
# 5) VPN core (Libreswan + xl2tpd) — interactive, optional
# =========================================================================
log "[5/9] VPN core (L2TP/IPsec)"
if [ -f /etc/ppp/options.xl2tpd ] || systemctl list-unit-files 2>/dev/null | grep -qE '^(xl2tpd|ipsec)'; then
    ok "IPsec/xl2tpd already present — leaving the running VPN core untouched"
elif yesno "  Install the L2TP/IPsec core now (hwdsl2 setup-ipsec-vpn — builds Libreswan, ~5 min)?" Y; then
    : "${VPN_PSK:?VPN_PSK must be set}"
    mkdir -p /opt/src
    wget -qO /opt/src/vpnsetup.sh https://raw.githubusercontent.com/hwdsl2/setup-ipsec-vpn/master/vpnsetup.sh
    VPN_IPSEC_PSK="$VPN_PSK" VPN_USER="${VPN_USER:-vpnuser}" \
        VPN_PASSWORD="${VPN_PASSWORD:-$(openssl rand -hex 8)}" bash /opt/src/vpnsetup.sh
    ok "VPN core installed"
else
    warn "  skipped — install/point your L2TP/IPsec + backhaul stack manually (env-specific)"
fi
# ppp accounting/shaping hooks
install -d -m 0755 /etc/ppp/ip-up.d /etc/ppp/ip-down.d
install -m 0755 "$REPO_DIR/configs/ppp-ip-up.sh"   /etc/ppp/ip-up.d/vpn-panel
install -m 0755 "$REPO_DIR/configs/ppp-ip-down.sh" /etc/ppp/ip-down.d/vpn-panel
for d in up down; do
  if [ ! -x /etc/ppp/ip-$d ] || ! grep -q "ip-$d.d/\*" /etc/ppp/ip-$d 2>/dev/null; then
    printf '#!/bin/sh\nfor s in /etc/ppp/ip-%s.d/*; do [ -x "$s" ] && "$s" "$@"; done\nexit 0\n' "$d" > /etc/ppp/ip-$d
    chmod 0755 /etc/ppp/ip-$d
  fi
done
touch "$LOG_DIR/accounting.log" "$LOG_DIR/ip-up.log"; chmod 640 "$LOG_DIR"/*.log 2>/dev/null || true
[ -f "$REPO_DIR/tune-network.sh" ] && bash "$REPO_DIR/tune-network.sh" >/dev/null 2>&1 || true
ok "ppp hooks + network tuning applied"

# =========================================================================
# 6) WireGuard panel interface
# =========================================================================
log "[6/9] WireGuard (wg-panel)"
WG_PORT="${WG_LISTEN_PORT:-51820}"; WG_POOL="${WG_POOL:-10.10.0.0/16}"; WG_MTU="${WG_MTU:-1320}"
WAN_IF="$(ip route get 8.8.8.8 2>/dev/null | grep -oP 'dev \K\S+' | head -1)"; WAN_IF="${WAN_IF:-eth0}"
if [ ! -f /etc/wireguard/wg-panel.conf ]; then
    install -d -m 700 /etc/wireguard
    umask 077; WG_KEY="$(wg genkey)"
    cat > /etc/wireguard/wg-panel.conf <<EOF
[Interface]
Address = ${WG_POOL%/*.*}.1/${WG_POOL#*/}
ListenPort = ${WG_PORT}
PrivateKey = ${WG_KEY}
MTU = ${WG_MTU}
PostUp   = iptables -I FORWARD 1 -i %i -j ACCEPT; iptables -I FORWARD 1 -o %i -j ACCEPT; iptables -t nat -A POSTROUTING -s ${WG_POOL} -o ${WAN_IF} -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT; iptables -t nat -D POSTROUTING -s ${WG_POOL} -o ${WAN_IF} -j MASQUERADE
EOF
    chmod 600 /etc/wireguard/wg-panel.conf
fi
sysctl -w net.ipv4.ip_forward=1 >/dev/null; echo 'net.ipv4.ip_forward=1' > /etc/sysctl.d/99-wg-forward.conf
systemctl enable --now wg-quick@wg-panel >/dev/null 2>&1 || warn "  wg-panel did not start — check 'wg-quick up wg-panel'"
ok "wireguard wg-panel on udp/${WG_PORT}"

# =========================================================================
# 7) Backend (Python venv)
# =========================================================================
log "[7/9] Backend"
rm -rf "$INSTALL_DIR/backend"
cp -r "$REPO_DIR/backend" "$INSTALL_DIR/"
python3 -m venv "$INSTALL_DIR/backend/venv"
"$INSTALL_DIR/backend/venv/bin/pip" install --upgrade pip >/dev/null
"$INSTALL_DIR/backend/venv/bin/pip" install -r "$INSTALL_DIR/backend/requirements.txt" >/dev/null
ok "backend + venv at $INSTALL_DIR/backend"

# =========================================================================
# 8) Frontend (build) + nginx
# =========================================================================
log "[8/9] Frontend + nginx"
command -v node >/dev/null 2>&1 || { curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null 2>&1; apt-get install -y nodejs >/dev/null; }
pushd "$REPO_DIR/frontend" >/dev/null
npm install --no-audit --no-fund >/dev/null 2>&1
VITE_BASE="$VITE_BASE" npm run build >/dev/null
FRONTEND_DIST="$INSTALL_DIR/frontend-dist"; rm -rf "$FRONTEND_DIST"; mkdir -p "$FRONTEND_DIST"; cp -r dist/* "$FRONTEND_DIST/"
popd >/dev/null
install -m 0644 "$REPO_DIR/deploy/nginx/vpn-panel.conf" /etc/nginx/sites-available/vpn-panel
sed -i "s|__PANEL_PORT__|${PANEL_PORT}|g; s|__FRONTEND_DIST__|${FRONTEND_DIST}|g; s|__PANEL_PATH__|${PANEL_PATH}|g" /etc/nginx/sites-available/vpn-panel
ln -sf /etc/nginx/sites-available/vpn-panel /etc/nginx/sites-enabled/vpn-panel; rm -f /etc/nginx/sites-enabled/default
mkdir -p /var/www/html; nginx -t >/dev/null 2>&1 && ok "nginx configured" || warn "nginx -t failed — check the config"

# =========================================================================
# 9) systemd + start
# =========================================================================
log "[9/9] Services"
install -m 0644 "$REPO_DIR/deploy/systemd/vpn-panel.service" /etc/systemd/system/vpn-panel.service
[ -f "$REPO_DIR/deploy/systemd/accel-ppp-sstp.service" ] && install -m 0644 "$REPO_DIR/deploy/systemd/accel-ppp-sstp.service" /etc/systemd/system/ 2>/dev/null || true
systemctl daemon-reload
systemctl enable --now vpn-panel.service >/dev/null 2>&1
systemctl restart nginx
sleep 3

hr; log "Verification"
for u in postgresql vpn-panel nginx wg-quick@wg-panel; do
    systemctl is-active --quiet "$u" 2>/dev/null && ok "$u active" || warn "$u NOT active (systemctl status $u)"
done
PING=$(curl -fsS --max-time 5 http://127.0.0.1:8000/api/ping 2>/dev/null || echo '')
[ "$PING" = '{"pong":true}' ] && ok "backend API responding" || warn "backend API not responding yet"
IP="$(curl -fsS --max-time 4 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
PORT_SUFFIX=""; [ "$PANEL_PORT" != "80" ] && PORT_SUFFIX=":${PANEL_PORT}"
[ -n "$RESTORE_DIR" ] && rm -rf "$RESTORE_DIR"

hr
cat <<EOF
  \033[1;32mAthena Panel is up.\033[0m
    Panel : http://${IP}${PORT_SUFFIX}${PANEL_PATH}/
    Admin : ${ADMIN_USERNAME:-admin}  (password in ${INSTALL_DIR}/.env)
    Logs  : journalctl -u vpn-panel -f
$( [ -n "${BACKUP:-}" ] && echo "    Restored from: ${BACKUP}" )
  Note: relay / backhaul topology is environment-specific — configure it per your setup.
EOF
hr
