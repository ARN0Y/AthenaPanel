#!/usr/bin/env bash
###############################################################################
# Update ONLY the panel (backend + frontend). Does NOT touch the L2TP engine
# (xl2tpd or accel-ppp), IPsec, kernel modules, or chap-secrets ordering — so
# NO connected VPN user is disconnected. Only the vpn-panel service restarts,
# which is independent of the pppd sessions.
#
#     sudo bash deploy-panel.sh
###############################################################################
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR=/opt/vpn-panel
ENV_FILE="$INSTALL_DIR/.env"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || die "Run as root"
[ -d "$INSTALL_DIR/backend" ] || die "$INSTALL_DIR/backend missing — run install.sh first"

# --- ensure required .env keys (won't change the VPN engine) -------------
ensure_env() {
    local key="$1" val="$2"
    grep -q "^${key}=" "$ENV_FILE" 2>/dev/null \
        && sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE" \
        || echo "${key}=${val}" >> "$ENV_FILE"
}
touch "$ENV_FILE"
ensure_env CHAP_SECRETS /etc/ppp/chap-secrets
ensure_env ACCT_LOG /var/log/vpn-panel/accounting.log
ensure_env VPN_DB_PATH /var/lib/vpn-panel/vpn.db

# Read a key: prefer the repo .env (what you edit with nano), fall back to the
# deployed env. Never fails the script if absent (-> empty).
REPO_ENV="$REPO_DIR/.env"
readkey() {
    local v=""
    [ -f "$REPO_ENV" ] && v="$(grep -E "^$1=" "$REPO_ENV" 2>/dev/null | head -1 | cut -d= -f2- || true)"
    [ -z "$v" ] && v="$(grep -E "^$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || true)"
    printf '%s' "$v"
}
# propagate client-facing + path settings into the DEPLOYED env (backend reads it)
for k in PANEL_PATH SERVER_ADDRESS SSTP_ADDRESS SSTP_ENABLED PANEL_PORT; do
    v="$(readkey "$k")"; [ -n "$v" ] && ensure_env "$k" "$v"
done
log "verified $ENV_FILE"

# --- backend code (additive DB migration runs on restart) ----------------
cp -r "$REPO_DIR/backend/app" "$INSTALL_DIR/backend/"
"$INSTALL_DIR/backend/venv/bin/pip" install -q -r "$INSTALL_DIR/backend/requirements.txt" >/dev/null 2>&1 || true
log "backend updated"

# --- ppp hooks (harmless to refresh; same content) -----------------------
install -d -m 0755 /etc/ppp/ip-up.d /etc/ppp/ip-down.d
install -m 0755 "$REPO_DIR/configs/ppp-ip-up.sh"   /etc/ppp/ip-up.d/vpn-panel
install -m 0755 "$REPO_DIR/configs/ppp-ip-down.sh" /etc/ppp/ip-down.d/vpn-panel

# --- secret panel path (build base + nginx) ------------------------------
PANEL_PATH="$(readkey PANEL_PATH)"; : "${PANEL_PATH:=/admin-athena}"
PANEL_PATH="/${PANEL_PATH#/}"; PANEL_PATH="${PANEL_PATH%/}"
[ -z "$PANEL_PATH" ] && PANEL_PATH="/admin-athena"
PANEL_PORT="$(readkey PANEL_PORT)"; : "${PANEL_PORT:=80}"
FRONTEND_DIST="$INSTALL_DIR/frontend-dist"

# --- frontend ------------------------------------------------------------
if command -v npm >/dev/null 2>&1; then
    pushd "$REPO_DIR/frontend" >/dev/null
    npm install --no-audit --no-fund >/dev/null 2>&1
    VITE_BASE="${PANEL_PATH}/" npm run build >/dev/null
    rm -rf "$FRONTEND_DIST"/*
    cp -r dist/* "$FRONTEND_DIST/"
    popd >/dev/null
    log "frontend rebuilt (base ${PANEL_PATH}/)"
fi

# --- nginx (serve under the secret path) ---------------------------------
install -m 0644 "$REPO_DIR/deploy/nginx/vpn-panel.conf" /etc/nginx/sites-available/vpn-panel
sed -i "s|__PANEL_PORT__|${PANEL_PORT}|g; s|__FRONTEND_DIST__|${FRONTEND_DIST}|g; s|__PANEL_PATH__|${PANEL_PATH}|g" /etc/nginx/sites-available/vpn-panel
mkdir -p /var/www/html
ln -sf /etc/nginx/sites-available/vpn-panel /etc/nginx/sites-enabled/vpn-panel
rm -f /etc/nginx/sites-enabled/default
if nginx -t 2>/dev/null; then systemctl reload nginx; log "nginx serving panel at ${PANEL_PATH}/"; else warn "nginx config test failed — left previous config running"; fi

# --- restart ONLY the panel service --------------------------------------
systemctl restart vpn-panel
sleep 2

log "panel status:"
systemctl is-active --quiet vpn-panel \
    && printf '    \033[1;32m✓\033[0m vpn-panel\n' \
    || printf '    \033[1;31m✗\033[0m vpn-panel (journalctl -u vpn-panel -n 40)\n'

echo
log "backend log:"
journalctl -u vpn-panel -n 15 --no-pager || true

cat <<EOF

============================================================================
  Panel updated. The L2TP engine and active VPN sessions were NOT touched.

  Your .env ADMIN_USERNAME/ADMIN_PASSWORD is now the SUPERADMIN.
  Existing VPN users are preserved and shown to the superadmin.

  New: Admins page (create sub-admins + invite links), per-admin user
  ownership, and creation date / creator shown on each user.
============================================================================
EOF
