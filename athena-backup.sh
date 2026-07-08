#!/usr/bin/env bash
###############################################################################
#  Athena Panel — full backup bundle (for migration / disaster recovery)
#
#  Run on the CURRENT (source) server. Produces ONE self-contained .zip holding
#  the complete database + every setting/secret needed to bring an identical
#  panel up on a fresh server with athena-setup.sh. Read-only w.r.t. the running
#  panel — pg_dump never locks writers, so it does not disrupt live users.
#
#      sudo bash athena-backup.sh                 # full (default)
#      sudo ESSENTIAL=1 bash athena-backup.sh     # skip the huge usage_samples
#
#  The bundle contains SECRETS (the .env, PSK, keys). Keep it PRIVATE — never
#  commit it and transfer it over a trusted channel only (scp).
###############################################################################
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }

INSTALL_DIR=/opt/vpn-panel
DB=vpnpanel
STAMP="$(date -u +%Y%m%d-%H%M%S)"
WORK="$(mktemp -d)"
BUNDLE="/root/athena-backup-${STAMP}.zip"
trap 'rm -rf "$WORK"' EXIT

command -v zip >/dev/null 2>&1 || { log "installing zip"; apt-get install -y zip >/dev/null 2>&1 || true; }

# --- 1) Panel database (TimescaleDB-aware custom-format dump) ----------------
if command -v pg_dump >/dev/null 2>&1 && sudo -u postgres psql -tAc "SELECT 1" "$DB" >/dev/null 2>&1; then
    if [ "${ESSENTIAL:-0}" = "1" ]; then
        log "dumping DB (essential — excludes usage_samples)"
        sudo -u postgres pg_dump -Fc \
            -t admins -t admin_invites -t users -t sessions -t traffic_samples \
            -t accounting -t audit_log -t app_settings -t wg_peers \
            "$DB" > "$WORK/vpnpanel.dump"
        echo "essential" > "$WORK/DB_MODE"
    else
        log "dumping DB (full, incl. usage_samples time-series)"
        sudo -u postgres pg_dump -Fc "$DB" > "$WORK/vpnpanel.dump"
        echo "full" > "$WORK/DB_MODE"
    fi
    log "    DB dump: $(du -h "$WORK/vpnpanel.dump" | cut -f1)"
else
    warn "Postgres DB '$DB' not reachable — bundle will have NO database (settings only)"
fi

# --- 2) Panel settings + secrets (the .env drives everything) ----------------
[ -f "$INSTALL_DIR/.env" ] && cp "$INSTALL_DIR/.env" "$WORK/panel.env" && log "panel .env captured"

# --- 3) WireGuard server interface (server keypair + PostUp NAT) -------------
[ -f /etc/wireguard/wg-panel.conf ] && cp /etc/wireguard/wg-panel.conf "$WORK/wg-panel.conf" && log "wg-panel.conf captured"

# --- 4) Auth material (regenerated from the DB on boot, kept for exact parity)
[ -f /etc/ppp/chap-secrets ] && cp /etc/ppp/chap-secrets "$WORK/chap-secrets"
[ -f /etc/ipsec.secrets ]   && cp /etc/ipsec.secrets   "$WORK/ipsec.secrets"

# --- 5) Manifest ------------------------------------------------------------
{
    echo "Athena Panel backup bundle"
    echo "created_utc: ${STAMP}"
    echo "source_host: $(hostname)"
    echo "source_ip:   $(curl -fsS --max-time 4 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
    echo "db_mode:     $(cat "$WORK/DB_MODE" 2>/dev/null || echo none)"
    echo "files:       $(cd "$WORK" && ls | tr '\n' ' ')"
} > "$WORK/MANIFEST.txt"

# --- 6) Zip it up -----------------------------------------------------------
( cd "$WORK" && zip -qr "$BUNDLE" . )
chmod 600 "$BUNDLE"

log "DONE -> ${BUNDLE}  ($(du -h "$BUNDLE" | cut -f1))"
echo
echo "Next: copy it to the new server, then run the installer there:"
echo "    scp ${BUNDLE} root@NEW_SERVER:/root/"
echo "    # on the new server:  sudo BACKUP=/root/$(basename "$BUNDLE") bash athena-setup.sh"
