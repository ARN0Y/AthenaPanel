#!/usr/bin/env bash
###############################################################################
# Revert the L2TP engine from accel-ppp back to xl2tpd (Libreswan unchanged).
# The panel is daemon-agnostic (chap-secrets + ip-up.d hooks), so it keeps
# working. xl2tpd re-reads chap-secrets on every auth, so new users connect
# immediately with no reload needed.
#
#     sudo bash revert-to-xl2tpd.sh
###############################################################################
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

log "stopping accel-ppp"
systemctl stop accel-ppp 2>/dev/null || true
systemctl disable accel-ppp 2>/dev/null || true

log "freeing UDP 1701"
fuser -k 1701/udp 2>/dev/null || true
sleep 1

log "bringing back xl2tpd"
systemctl unmask xl2tpd 2>/dev/null || true
systemctl enable xl2tpd 2>/dev/null || true
systemctl restart xl2tpd

# Ensure pppd MTU matches the backhaul path (download fix) for xl2tpd too.
if [ -f /etc/ppp/options.xl2tpd ]; then
    grep -q '^mtu'  /etc/ppp/options.xl2tpd && sed -i 's/^mtu.*/mtu 1280/'  /etc/ppp/options.xl2tpd || echo 'mtu 1280' >> /etc/ppp/options.xl2tpd
    grep -q '^mru'  /etc/ppp/options.xl2tpd && sed -i 's/^mru.*/mru 1280/'  /etc/ppp/options.xl2tpd || echo 'mru 1280' >> /etc/ppp/options.xl2tpd
    systemctl restart xl2tpd
fi

sleep 1
log "status:"
ss -ulnp | grep 1701 || true
for unit in ipsec xl2tpd vpn-panel nginx; do
    systemctl is-active --quiet "$unit" 2>/dev/null \
        && printf '    \033[1;32m✓\033[0m %s\n' "$unit" \
        || printf '    \033[1;31m✗\033[0m %s\n' "$unit"
done

cat <<EOF

============================================================================
  Reverted to xl2tpd. Reconnect from Windows.
  New users added in the panel work immediately (xl2tpd re-reads
  /etc/ppp/chap-secrets on every auth — no reload needed).
============================================================================
EOF
