#!/usr/bin/env bash
###############################################################################
# VPN Panel installer (Ubuntu 22.04 / 24.04) — native, no Docker
#
#   VPN core : Libreswan + xl2tpd + pppd  (hwdsl2/setup-ipsec-vpn, run natively)
#   Panel    : FastAPI + React + nginx + systemd
#
# This is the same stack as the hwdsl2/ipsec-vpn-server Docker image, installed
# directly on the host. Run as root from the repository root:
#
#     sudo bash install.sh
#
# Skip the VPN core install (if you already have xl2tpd working):
#     sudo SKIP_VPN_CORE=1 bash install.sh
###############################################################################
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR=/opt/vpn-panel
LOG_DIR=/var/log/vpn-panel
DB_DIR=/var/lib/vpn-panel

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run as root (sudo bash install.sh)"

# --- Load / create .env ---------------------------------------------------
if [ ! -f "$REPO_DIR/.env" ]; then
    log "No .env found; creating from .env.example"
    cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
    JWT="$(openssl rand -hex 32)"
    sed -i "s|^JWT_SECRET=.*|JWT_SECRET=${JWT}|" "$REPO_DIR/.env"
    warn "Generated JWT_SECRET. Review VPN_PSK / ADMIN_PASSWORD in .env!"
fi

set -a
# shellcheck disable=SC1091
source "$REPO_DIR/.env"
set +a

: "${PANEL_PORT:=80}"
: "${PANEL_PATH:=/admin-athena}"
# normalize: leading slash, no trailing slash
PANEL_PATH="/${PANEL_PATH#/}"; PANEL_PATH="${PANEL_PATH%/}"
[ -z "$PANEL_PATH" ] && PANEL_PATH="/admin-athena"
VITE_BASE="${PANEL_PATH}/"
: "${VPN_PSK:?VPN_PSK must be set in .env}"
: "${VPN_USER:=}"
: "${VPN_PASSWORD:=}"

# =========================================================================
log "[1/8] Installing base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y \
    curl wget ca-certificates gawk sqlite3 iproute2 \
    python3 python3-venv python3-dev \
    nginx \
    >/dev/null
log "    base packages installed"

# =========================================================================
log "[2/8] Installing VPN core (Libreswan + xl2tpd + pppd)"
if [ "${SKIP_VPN_CORE:-0}" = "1" ]; then
    warn "    SKIP_VPN_CORE=1 — skipping hwdsl2 installer"
elif [ -f /etc/ppp/options.xl2tpd ] && systemctl list-unit-files | grep -q '^xl2tpd'; then
    log "    xl2tpd already present — skipping hwdsl2 installer"
else
    mkdir -p /opt/src
    wget -qO /opt/src/vpnsetup.sh \
        https://raw.githubusercontent.com/hwdsl2/setup-ipsec-vpn/master/vpnsetup.sh
    log "    running hwdsl2 vpnsetup.sh (this builds Libreswan; takes a few min)"
    VPN_IPSEC_PSK="$VPN_PSK" \
    VPN_USER="${VPN_USER:-vpnuser}" \
    VPN_PASSWORD="${VPN_PASSWORD:-$(openssl rand -hex 8)}" \
        bash /opt/src/vpnsetup.sh
    log "    VPN core installed"
fi

# =========================================================================
log "[3/8] Installing PPP hooks (session accounting + shaping)"
install -d -m 0755 /etc/ppp/ip-up.d /etc/ppp/ip-down.d
install -m 0755 "$REPO_DIR/configs/ppp-ip-up.sh"   /etc/ppp/ip-up.d/vpn-panel
install -m 0755 "$REPO_DIR/configs/ppp-ip-down.sh" /etc/ppp/ip-down.d/vpn-panel
# Ensure pppd actually runs the ip-up.d / ip-down.d directories
# pppd calls /etc/ppp/ip-up with 6 positional args + env. We must forward all
# of them to each hook ("$@"), which run-parts cannot do cleanly -> loop.
if [ ! -x /etc/ppp/ip-up ] || ! grep -q 'ip-up.d/\*' /etc/ppp/ip-up 2>/dev/null; then
    cat > /etc/ppp/ip-up <<'EOF'
#!/bin/sh
# Managed by vpn-panel: run every executable hook with pppd's args + env.
for s in /etc/ppp/ip-up.d/*; do
    [ -x "$s" ] && "$s" "$@"
done
exit 0
EOF
    chmod 0755 /etc/ppp/ip-up
fi
if [ ! -x /etc/ppp/ip-down ] || ! grep -q 'ip-down.d/\*' /etc/ppp/ip-down 2>/dev/null; then
    cat > /etc/ppp/ip-down <<'EOF'
#!/bin/sh
# Managed by vpn-panel: run every executable hook with pppd's args + env.
for s in /etc/ppp/ip-down.d/*; do
    [ -x "$s" ] && "$s" "$@"
done
exit 0
EOF
    chmod 0755 /etc/ppp/ip-down
fi
mkdir -p "$LOG_DIR" "$DB_DIR"
touch "$LOG_DIR/accounting.log" "$LOG_DIR/ip-up.log"
chmod 640 "$LOG_DIR"/*.log
log "    hooks installed to /etc/ppp/ip-up.d and ip-down.d"

# --- MSS clamp for the PPP path -----------------------------------------
# L2TP/IPsec (doubly-encapsulated over the backhaul TUN) lowers the effective
# MTU well below the link MTU. The ppp MTU is 1280 (accel-ppp), so clamp TCP
# MSS to 1240 in BOTH directions with an explicit value — clamp-to-pmtu keys off
# the OUTGOING iface and fails to shrink the client's outbound SYN, which is
# what governs DOWNLOAD segment size (the slow/stalling direction).
iptables -t mangle -F FORWARD 2>/dev/null || true
for spec in "-i ppp+" "-o ppp+"; do
    # shellcheck disable=SC2086
    iptables -t mangle -A FORWARD $spec -p tcp --tcp-flags SYN,RST SYN \
        -j TCPMSS --set-mss 1240
done
sysctl -w net.netfilter.nf_conntrack_tcp_be_liberal=1 >/dev/null 2>&1 || true
echo 'net.netfilter.nf_conntrack_tcp_be_liberal=1' > /etc/sysctl.d/99-vpn-conntrack.conf
apt-get install -y iptables-persistent >/dev/null 2>&1 || true
command -v netfilter-persistent >/dev/null 2>&1 && netfilter-persistent save >/dev/null 2>&1 || true
log "    MSS clamp 1240 + conntrack liberal applied for ppp+"

# Network throughput / stability tuning (BBR + fq + buffers + conntrack)
if [ -f "$REPO_DIR/tune-network.sh" ]; then
    bash "$REPO_DIR/tune-network.sh" >/dev/null 2>&1 || true
    log "    network tuning applied (BBR/fq, socket buffers)"
fi

# --- L2TP engine: accel-ppp (replaces xl2tpd) ----------------------------
log "    setting up accel-ppp L2TP engine"
if ! command -v accel-pppd >/dev/null 2>&1; then
    apt-get install -y build-essential cmake pkg-config git \
        libpcre3-dev libssl-dev libnl-3-dev libnl-route-3-dev libnl-genl-3-dev >/dev/null
    rm -rf /usr/local/src/accel-ppp
    git clone --depth 1 https://github.com/accel-ppp/accel-ppp.git /usr/local/src/accel-ppp
    mkdir -p /usr/local/src/accel-ppp/build
    pushd /usr/local/src/accel-ppp/build >/dev/null
    cmake -DBUILD_IPOE_DRIVER=FALSE -DBUILD_VLAN_MON_DRIVER=FALSE \
        -DCMAKE_INSTALL_PREFIX=/usr/local -DCMAKE_BUILD_TYPE=Release \
        -DLUA=FALSE -DSHAPER=TRUE -DRADIUS=FALSE .. >/dev/null
    make -j"$(nproc)" >/dev/null
    make install >/dev/null
    ldconfig
    popd >/dev/null
fi
mkdir -p /etc/accel-ppp /var/log/accel-ppp
install -m 0644 "$REPO_DIR/configs/accel-ppp.conf" /etc/accel-ppp/accel-ppp.conf
install -m 0644 "$REPO_DIR/deploy/systemd/accel-ppp.service" /etc/systemd/system/accel-ppp.service
# MASK xl2tpd (stubborn SysV service re-grabs UDP 1701 on boot otherwise)
systemctl stop xl2tpd 2>/dev/null || true
systemctl disable xl2tpd 2>/dev/null || true
systemctl mask xl2tpd 2>/dev/null || true
fuser -k 1701/udp 2>/dev/null || true
# Kernel L2TP modules live in linux-modules-extra on Ubuntu; without them
# accel-ppp can't build tunnels and clients can't connect.
if ! modprobe l2tp_ppp 2>/dev/null; then
    apt-get install -y "linux-modules-extra-$(uname -r)" >/dev/null 2>&1 \
        || apt-get install -y linux-modules-extra-generic >/dev/null 2>&1 || true
    modprobe l2tp_ppp 2>/dev/null || warn "    l2tp_ppp unavailable — reboot into the matching kernel after install"
fi
modprobe pppol2tp 2>/dev/null || true
modprobe l2tp_netlink 2>/dev/null || true
log "    accel-ppp installed; xl2tpd disabled; L2TP kernel modules ensured"

# --- Backhaul mode (entry-node -> TUN -> this server) --------------------
# IKE/L2TP packets forwarded by the entry node arrive with dst = BACKHAUL_ADDR
# (the local TUN address), not the public IP. Libreswan must use that same
# address as its local endpoint, otherwise IKE won't match it AND xl2tpd's L2TP
# replies (sourced from BACKHAUL_ADDR) won't match the IPsec policy and leak out
# in plaintext. So we point Libreswan's `left` at BACKHAUL_ADDR.
if [ -n "${BACKHAUL_ADDR:-}" ] && [ -f /etc/ipsec.conf ]; then
    if grep -q '^  left=%defaultroute' /etc/ipsec.conf; then
        sed -i "s/^  left=%defaultroute/  left=${BACKHAUL_ADDR}/" /etc/ipsec.conf
        ipsec restart 2>/dev/null || systemctl restart ipsec 2>/dev/null || true
        log "    backhaul: Libreswan left set to ${BACKHAUL_ADDR}"
    else
        log "    backhaul: Libreswan left already customized — leaving as-is"
    fi
fi

# =========================================================================
log "[4/8] Installing backend (Python venv)"
mkdir -p "$INSTALL_DIR"
rm -rf "$INSTALL_DIR/backend"
cp -r "$REPO_DIR/backend" "$INSTALL_DIR/"
cp "$REPO_DIR/.env" "$INSTALL_DIR/.env"
python3 -m venv "$INSTALL_DIR/backend/venv"
"$INSTALL_DIR/backend/venv/bin/pip" install --upgrade pip >/dev/null
"$INSTALL_DIR/backend/venv/bin/pip" install -r "$INSTALL_DIR/backend/requirements.txt" >/dev/null
log "    backend at $INSTALL_DIR/backend"

# =========================================================================
log "[5/8] Building frontend"
if ! command -v node >/dev/null 2>&1; then
    log "    installing Node.js 20.x"
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null 2>&1
    apt-get install -y nodejs >/dev/null
fi
pushd "$REPO_DIR/frontend" >/dev/null
npm install --no-audit --no-fund >/dev/null 2>&1
VITE_BASE="$VITE_BASE" npm run build >/dev/null
FRONTEND_DIST="$INSTALL_DIR/frontend-dist"
rm -rf "$FRONTEND_DIST"; mkdir -p "$FRONTEND_DIST"
cp -r dist/* "$FRONTEND_DIST/"
popd >/dev/null
log "    frontend -> $FRONTEND_DIST"

# =========================================================================
log "[6/8] Configuring nginx"
install -m 0644 "$REPO_DIR/deploy/nginx/vpn-panel.conf" /etc/nginx/sites-available/vpn-panel
sed -i "s|__PANEL_PORT__|${PANEL_PORT}|g" /etc/nginx/sites-available/vpn-panel
sed -i "s|__FRONTEND_DIST__|${FRONTEND_DIST}|g" /etc/nginx/sites-available/vpn-panel
sed -i "s|__PANEL_PATH__|${PANEL_PATH}|g" /etc/nginx/sites-available/vpn-panel
mkdir -p /var/www/html   # ACME webroot for Let's Encrypt (SSTP cert)
ln -sf /etc/nginx/sites-available/vpn-panel /etc/nginx/sites-enabled/vpn-panel
rm -f /etc/nginx/sites-enabled/default
nginx -t
log "    nginx configured"

# =========================================================================
log "[7/8] Installing systemd unit + logrotate"
install -m 0644 "$REPO_DIR/deploy/systemd/vpn-panel.service" /etc/systemd/system/vpn-panel.service
install -m 0644 "$REPO_DIR/deploy/logrotate/vpn-panel" /etc/logrotate.d/vpn-panel
systemctl daemon-reload
systemctl enable accel-ppp.service >/dev/null 2>&1 || true
systemctl restart accel-ppp.service
systemctl enable --now vpn-panel.service
systemctl restart nginx
log "    accel-ppp + vpn-panel services enabled"

# =========================================================================
log "[8/8] Verification"
sleep 2
for unit in ipsec accel-ppp vpn-panel nginx; do
    if systemctl is-active --quiet "$unit" 2>/dev/null; then
        printf '    \033[1;32m✓\033[0m %s active\n' "$unit"
    else
        printf '    \033[1;33m–\033[0m %s not active (check: systemctl status %s)\n' "$unit" "$unit"
    fi
done

IP="$(curl -fsS --max-time 4 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
PORT_SUFFIX=""; [ "$PANEL_PORT" != "80" ] && PORT_SUFFIX=":${PANEL_PORT}"

cat <<EOF

============================================================================
  VPN Panel installed (Libreswan + accel-ppp, native).
----------------------------------------------------------------------------
  Panel URL    : http://${IP}${PORT_SUFFIX}${PANEL_PATH}/
  Admin user   : ${ADMIN_USERNAME:-admin}
  Admin pass   : ${ADMIN_PASSWORD:-changeme}   (change in .env + restart)

  IPsec PSK    : ${VPN_PSK}
  Protocol     : L2TP/IPsec (PSK) — IKE udp/500, NAT-T udp/4500, L2TP udp/1701

  Windows client: standard L2TP/IPsec with the PSK above. Set
    HKLM\\SYSTEM\\CurrentControlSet\\Services\\PolicyAgent
      AssumeUDPEncapsulationContextOnSendRule = 2  (DWORD), then reboot.

  Manage users in the panel. They are written to /etc/ppp/chap-secrets and
  accel-ppp is reloaded automatically (accel-cmd reload).

  Useful commands:
    systemctl status vpn-panel accel-ppp ipsec nginx
    journalctl -u accel-ppp -f
    accel-cmd show sessions
    tail -f /var/log/vpn-panel/ip-up.log
    ipsec status ;  cat /etc/ppp/chap-secrets
============================================================================
EOF
