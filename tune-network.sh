#!/usr/bin/env bash
###############################################################################
# Network tuning for the relay/VPN path (run on BOTH the overseas server and
# the Iranian entry node). Reduces packet loss, bufferbloat and connection
# stalls on the high-RTT backhaul. Safe + idempotent.
#
#     sudo bash tune-network.sh
###############################################################################
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

# BBR congestion control + fair-queue pacing (helps locally-originated TCP and
# smooths forwarding; fq removes bufferbloat on the egress path).
modprobe tcp_bbr 2>/dev/null || true
echo tcp_bbr > /etc/modules-load.d/bbr.conf

cat > /etc/sysctl.d/99-vpn-tuning.conf <<'EOF'
# --- queueing / congestion ---
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr

# --- socket buffers (high bandwidth-delay product over the backhaul) ---
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.core.rmem_default = 1048576
net.core.wmem_default = 1048576
net.ipv4.tcp_rmem = 4096 1048576 16777216
net.ipv4.tcp_wmem = 4096 1048576 16777216
net.core.netdev_max_backlog = 5000
net.core.somaxconn = 1024

# --- forwarding + conntrack (don't drop legit tunneled packets) ---
net.ipv4.ip_forward = 1
net.netfilter.nf_conntrack_max = 262144
net.netfilter.nf_conntrack_tcp_be_liberal = 1

# --- TCP robustness on lossy/jittery paths ---
net.ipv4.tcp_mtu_probing = 1
net.ipv4.tcp_sack = 1
net.ipv4.tcp_fack = 1
net.ipv4.tcp_slow_start_after_idle = 0
EOF

sysctl --system >/dev/null

# Bump txqueuelen on ppp + backhaul interfaces (more burst headroom).
for ifc in $(ls /sys/class/net | grep -E '^(ppp|bh-)'); do
    ip link set dev "$ifc" txqueuelen 1000 2>/dev/null || true
done

echo "Applied. Active congestion control: $(sysctl -n net.ipv4.tcp_congestion_control), qdisc: $(sysctl -n net.core.default_qdisc)"
