#!/usr/bin/env bash
###############################################################################
# One-shot redeploy of the panel + L2TP config onto an already-installed server
# (Libreswan + accel-ppp). Does NOT restart accel-ppp (which would break kernel
# L2TP state) — it reloads it. Run as root from the repo root:
#
#     sudo bash deploy.sh
###############################################################################
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR=/opt/vpn-panel
ENV_FILE="$INSTALL_DIR/.env"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || die "Run as root (sudo bash deploy.sh)"
[ -d "$INSTALL_DIR" ] || die "$INSTALL_DIR missing — run install.sh first"

# --- 1) Ensure key .env values are correct -------------------------------
ensure_env() {
    local key="$1" val="$2"
    if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
    else
        echo "${key}=${val}" >> "$ENV_FILE"
    fi
}
touch "$ENV_FILE"
ensure_env CHAP_SECRETS /etc/ppp/chap-secrets
ensure_env ACCEL_CLI 127.0.0.1:2001
ensure_env CHAP_SERVER_FIELD '*'
ensure_env ACCT_LOG /var/log/vpn-panel/accounting.log
ensure_env VPN_DB_PATH /var/lib/vpn-panel/vpn.db
log "verified $ENV_FILE"

# --- 2) Backend code ------------------------------------------------------
cp -r "$REPO_DIR/backend/app" "$INSTALL_DIR/backend/"
"$INSTALL_DIR/backend/venv/bin/pip" install -q -r "$INSTALL_DIR/backend/requirements.txt" >/dev/null 2>&1 || true
log "backend code updated"

# --- 3) accel-ppp config + service ---------------------------------------
mkdir -p /etc/accel-ppp /var/log/accel-ppp
install -m 0644 "$REPO_DIR/configs/accel-ppp.conf" /etc/accel-ppp/accel-ppp.conf
install -m 0644 "$REPO_DIR/deploy/systemd/accel-ppp.service" /etc/systemd/system/accel-ppp.service
# Make sure xl2tpd can never steal UDP 1701 from accel-ppp.
systemctl stop xl2tpd 2>/dev/null || true
systemctl mask xl2tpd 2>/dev/null || true

# accel-ppp needs the kernel L2TP modules (in linux-modules-extra on Ubuntu).
# A kernel upgrade can leave them missing -> accel-ppp runs but can't build
# tunnels and clients fail to connect. Ensure they're present.
if ! modprobe l2tp_ppp 2>/dev/null; then
    warn "kernel L2TP modules missing for $(uname -r) — installing linux-modules-extra"
    apt-get install -y "linux-modules-extra-$(uname -r)" >/dev/null 2>&1 \
        || apt-get install -y linux-modules-extra-generic >/dev/null 2>&1 || true
    modprobe l2tp_ppp 2>/dev/null || warn "still cannot load l2tp_ppp — a reboot into the matching kernel may be needed"
fi
modprobe pppol2tp 2>/dev/null || true
modprobe l2tp_netlink 2>/dev/null || true

systemctl daemon-reload
systemctl enable accel-ppp >/dev/null 2>&1 || true
if systemctl is-active --quiet accel-ppp; then
    # up -> reload (NOT restart) so kernel L2TP state stays intact
    accel-cmd -p 2001 reload >/dev/null 2>&1 || warn "accel-cmd reload returned error"
    log "accel-ppp config reloaded"
else
    # down -> a fresh start is safe (clean kernel L2TP state)
    modprobe l2tp_ppp 2>/dev/null || true
    systemctl start accel-ppp
    log "accel-ppp was down -> started"
fi

# --- 4) PPP hooks ---------------------------------------------------------
install -d -m 0755 /etc/ppp/ip-up.d /etc/ppp/ip-down.d
install -m 0755 "$REPO_DIR/configs/ppp-ip-up.sh"   /etc/ppp/ip-up.d/vpn-panel
install -m 0755 "$REPO_DIR/configs/ppp-ip-down.sh" /etc/ppp/ip-down.d/vpn-panel

# --- 5) Network tuning ----------------------------------------------------
[ -f "$REPO_DIR/tune-network.sh" ] && bash "$REPO_DIR/tune-network.sh" >/dev/null 2>&1 || true

# --- 6) Frontend + nginx (secret panel path) ------------------------------
REPO_ENV="$REPO_DIR/.env"
readkey() {
    local v=""
    [ -f "$REPO_ENV" ] && v="$(grep -E "^$1=" "$REPO_ENV" 2>/dev/null | head -1 | cut -d= -f2- || true)"
    [ -z "$v" ] && v="$(grep -E "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || true)"
    printf '%s' "$v"
}
for k in PANEL_PATH SERVER_ADDRESS SSTP_ADDRESS SSTP_ENABLED PANEL_PORT; do
    v="$(readkey "$k")"; [ -n "$v" ] && ensure_env "$k" "$v"
done
PANEL_PATH="$(readkey PANEL_PATH)"; : "${PANEL_PATH:=/admin-athena}"
PANEL_PATH="/${PANEL_PATH#/}"; PANEL_PATH="${PANEL_PATH%/}"
[ -z "$PANEL_PATH" ] && PANEL_PATH="/admin-athena"
PANEL_PORT="$(readkey PANEL_PORT)"; : "${PANEL_PORT:=80}"
FRONTEND_DIST="$INSTALL_DIR/frontend-dist"
if command -v npm >/dev/null 2>&1; then
    pushd "$REPO_DIR/frontend" >/dev/null
    npm install --no-audit --no-fund >/dev/null 2>&1
    VITE_BASE="${PANEL_PATH}/" npm run build >/dev/null
    rm -rf "$FRONTEND_DIST"/*
    cp -r dist/* "$FRONTEND_DIST/"
    popd >/dev/null
    log "frontend rebuilt (base ${PANEL_PATH}/)"
fi
install -m 0644 "$REPO_DIR/deploy/nginx/vpn-panel.conf" /etc/nginx/sites-available/vpn-panel
sed -i "s|__PANEL_PORT__|${PANEL_PORT}|g; s|__FRONTEND_DIST__|${FRONTEND_DIST}|g; s|__PANEL_PATH__|${PANEL_PATH}|g" /etc/nginx/sites-available/vpn-panel
mkdir -p /var/www/html
ln -sf /etc/nginx/sites-available/vpn-panel /etc/nginx/sites-enabled/vpn-panel
rm -f /etc/nginx/sites-enabled/default
nginx -t 2>/dev/null && systemctl reload nginx && log "nginx serving panel at ${PANEL_PATH}/" || warn "nginx test failed"

# --- 7) Restart panel + verify -------------------------------------------
systemctl restart vpn-panel
sleep 2
log "deploy done. status:"
for unit in ipsec accel-ppp vpn-panel nginx; do
    systemctl is-active --quiet "$unit" 2>/dev/null \
        && printf '    \033[1;32m✓\033[0m %s\n' "$unit" \
        || printf '    \033[1;31m✗\033[0m %s\n' "$unit"
done

echo
log "recent backend log (look here for errors when creating a user):"
journalctl -u vpn-panel -n 20 --no-pager || true
echo
log "current chap-secrets:"
cat /etc/ppp/chap-secrets || true
