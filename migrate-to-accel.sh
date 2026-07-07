#!/usr/bin/env bash
###############################################################################
# Migrate the L2TP engine from xl2tpd to accel-ppp (keeps Libreswan for IPsec).
#
# Run as root from the repository root:
#     sudo bash migrate-to-accel.sh
#
# Safe to re-run. Builds accel-ppp from source if missing, writes its config,
# disables xl2tpd, enables accel-ppp, ensures MTU/MSS, and redeploys the panel
# backend (which now drives accel-cmd for terminate/reload).
###############################################################################
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR=/opt/vpn-panel
ACCEL_SRC=/usr/local/src/accel-ppp

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || die "Run as root (sudo bash migrate-to-accel.sh)"

export DEBIAN_FRONTEND=noninteractive

# --- 1) Build / install accel-ppp ----------------------------------------
if command -v accel-pppd >/dev/null 2>&1; then
    log "accel-pppd already installed ($(command -v accel-pppd)) — skipping build"
else
    log "Installing build dependencies for accel-ppp"
    apt-get update -qq
    apt-get install -y build-essential cmake pkg-config git \
        libpcre3-dev libssl-dev \
        libnl-3-dev libnl-route-3-dev libnl-genl-3-dev >/dev/null
    log "Cloning + building accel-ppp (a few minutes)"
    rm -rf "$ACCEL_SRC"
    git clone --depth 1 https://github.com/accel-ppp/accel-ppp.git "$ACCEL_SRC"
    mkdir -p "$ACCEL_SRC/build"
    pushd "$ACCEL_SRC/build" >/dev/null
    cmake \
        -DBUILD_IPOE_DRIVER=FALSE \
        -DBUILD_VLAN_MON_DRIVER=FALSE \
        -DCMAKE_INSTALL_PREFIX=/usr/local \
        -DCMAKE_BUILD_TYPE=Release \
        -DLUA=FALSE -DSHAPER=TRUE -DRADIUS=FALSE \
        .. >/dev/null
    make -j"$(nproc)" >/dev/null
    make install >/dev/null
    ldconfig
    popd >/dev/null
    log "accel-ppp installed to /usr/local"
fi

# --- 2) Config + log dir --------------------------------------------------
mkdir -p /etc/accel-ppp /var/log/accel-ppp
install -m 0644 "$REPO_DIR/configs/accel-ppp.conf" /etc/accel-ppp/accel-ppp.conf
log "wrote /etc/accel-ppp/accel-ppp.conf"

# --- 3) Ensure pppd-compat master hooks (loop -> ip-up.d/ip-down.d) -------
install -d -m 0755 /etc/ppp/ip-up.d /etc/ppp/ip-down.d
install -m 0755 "$REPO_DIR/configs/ppp-ip-up.sh"   /etc/ppp/ip-up.d/vpn-panel
install -m 0755 "$REPO_DIR/configs/ppp-ip-down.sh" /etc/ppp/ip-down.d/vpn-panel
cat > /etc/ppp/ip-up <<'EOF'
#!/bin/sh
for s in /etc/ppp/ip-up.d/*; do [ -x "$s" ] && "$s" "$@"; done
exit 0
EOF
cat > /etc/ppp/ip-down <<'EOF'
#!/bin/sh
for s in /etc/ppp/ip-down.d/*; do [ -x "$s" ] && "$s" "$@"; done
exit 0
EOF
chmod 0755 /etc/ppp/ip-up /etc/ppp/ip-down

# --- 4) MTU + MSS (download fix) -----------------------------------------
# accel-ppp enforces ppp MTU=1280 from its config. Clamp TCP MSS to 1240 on
# both directions so neither side oversizes packets for the backhaul path.
iptables -t mangle -F FORWARD 2>/dev/null || true
for spec in "-i ppp+" "-o ppp+"; do
    # shellcheck disable=SC2086
    iptables -t mangle -A FORWARD $spec -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1240
done
sysctl -w net.netfilter.nf_conntrack_tcp_be_liberal=1 >/dev/null 2>&1 || true
echo 'net.netfilter.nf_conntrack_tcp_be_liberal=1' > /etc/sysctl.d/99-vpn-conntrack.conf
apt-get install -y iptables-persistent >/dev/null 2>&1 || true
command -v netfilter-persistent >/dev/null 2>&1 && netfilter-persistent save >/dev/null 2>&1 || true
log "MTU 1280 (accel-ppp) + MSS clamp 1240 + conntrack liberal applied"

# --- 5) Switch services ---------------------------------------------------
install -m 0644 "$REPO_DIR/deploy/systemd/accel-ppp.service" /etc/systemd/system/accel-ppp.service
systemctl daemon-reload
log "disabling xl2tpd, enabling accel-ppp"
# xl2tpd is a stubborn SysV service that re-grabs UDP 1701 on boot -> MASK it
# so accel-ppp can own the port.
systemctl stop xl2tpd 2>/dev/null || true
systemctl disable xl2tpd 2>/dev/null || true
systemctl mask xl2tpd 2>/dev/null || true
fuser -k 1701/udp 2>/dev/null || true
# Ensure kernel L2TP modules (linux-modules-extra on Ubuntu) — required for
# accel-ppp to build tunnels; a kernel upgrade can leave them missing.
if ! modprobe l2tp_ppp 2>/dev/null; then
    apt-get install -y "linux-modules-extra-$(uname -r)" >/dev/null 2>&1 \
        || apt-get install -y linux-modules-extra-generic >/dev/null 2>&1 || true
    modprobe l2tp_ppp 2>/dev/null || warn "l2tp_ppp unavailable — reboot into the matching kernel"
fi
modprobe pppol2tp 2>/dev/null || true
modprobe l2tp_netlink 2>/dev/null || true
systemctl enable accel-ppp.service >/dev/null 2>&1 || true
# restart (not just enable --now) so config changes are picked up on re-run
systemctl restart accel-ppp.service

# --- 6) Redeploy panel backend (new accel-cmd terminate/reload) ----------
if [ -d "$INSTALL_DIR/backend" ]; then
    cp -r "$REPO_DIR/backend/app" "$INSTALL_DIR/backend/"
    systemctl restart vpn-panel 2>/dev/null || true
    log "panel backend updated + restarted"
else
    warn "panel not found at $INSTALL_DIR — run install.sh first"
fi

# --- 7) Verify ------------------------------------------------------------
sleep 2
log "verification:"
for unit in ipsec accel-ppp vpn-panel nginx; do
    if systemctl is-active --quiet "$unit" 2>/dev/null; then
        printf '    \033[1;32m✓\033[0m %s active\n' "$unit"
    else
        printf '    \033[1;31m✗\033[0m %s NOT active (journalctl -u %s -n 50)\n' "$unit" "$unit"
    fi
done
if systemctl is-enabled --quiet xl2tpd 2>/dev/null; then
    warn "xl2tpd still enabled — disable it: systemctl disable --now xl2tpd"
fi

cat <<EOF

============================================================================
  Migrated to accel-ppp. Libreswan/IPsec unchanged.

  Reconnect the Windows client and watch:
    tail -f /var/log/vpn-panel/ip-up.log        # session-up + shaper
    accel-cmd show sessions                     # live accel-ppp sessions
    journalctl -u accel-ppp -f

  If a client can't connect, confirm:
    ss -ulnp | grep 1701        # accel-pppd listening (not xl2tpd/docker)
    ipsec status                # IPsec still up
============================================================================
EOF
