#!/usr/bin/env bash
###############################################################################
# setup-warp.sh — "warp" outbound for the foreign exit node.
#
# Brings up a Cloudflare WARP WireGuard interface ("warp") in its OWN routing
# table (Table=off, so it NEVER hijacks the host or existing users), and wires
# policy-based routing so ONLY the IPs in the `warp_users` ipset egress via WARP.
# The panel owns that ipset (per-user outbound=warp). Direct users are untouched.
#
# Idempotent + safe: with an empty ipset nothing is routed through WARP, so this
# can be run on a live node with zero impact until users are opted in.
#
#   sudo bash setup-warp.sh
###############################################################################
set -Eeuo pipefail
log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || die "run as root"

TABLE=200
MARK="0x2"
IPSET="warp_users"
RULE_PRIO=1000

log "ensuring tooling (wireguard, ipset, curl)..."
command -v wg >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y -qq wireguard-tools; }
command -v ipset >/dev/null 2>&1 || apt-get install -y -qq ipset
modprobe wireguard 2>/dev/null || true

# --- wgcf (registers a free WARP account, generates a WG profile) ------------
if ! command -v wgcf >/dev/null 2>&1; then
  log "installing wgcf..."
  VER="$(curl -fsSL https://api.github.com/repos/ViRb3/wgcf/releases/latest | grep -oP '"tag_name":\s*"\K[^"]+' || true)"
  [ -n "$VER" ] || VER="v2.2.22"
  curl -fsSL "https://github.com/ViRb3/wgcf/releases/download/${VER}/wgcf_${VER#v}_linux_amd64" -o /usr/local/bin/wgcf
  chmod +x /usr/local/bin/wgcf
fi

mkdir -p /etc/wireguard
cd /etc/wireguard
[ -f wgcf-account.toml ] || { log "registering WARP account..."; wgcf register --accept-tos; }
[ -f wgcf-profile.conf ] || { log "generating WARP profile..."; wgcf generate; }

# --- build /etc/wireguard/warp.conf (Table=off + our PBR hooks) -------------
PRIV="$(grep -m1 'PrivateKey' wgcf-profile.conf | awk '{print $3}')"
PUB="$(grep -m1 'PublicKey' wgcf-profile.conf | awk '{print $3}')"
EP="$(grep -m1 'Endpoint' wgcf-profile.conf | awk '{print $3}')"
# wgcf writes both addresses on one comma-separated line: "Address = <v4>, <v6>"
ADDRS="$(grep -m1 '^Address' wgcf-profile.conf | sed 's/^Address *= *//')"
ADDR4="$(printf '%s' "$ADDRS" | tr ',' '\n' | grep -m1 -E '[0-9]+\.[0-9]+' | tr -d ' ' || true)"
ADDR6="$(printf '%s' "$ADDRS" | tr ',' '\n' | grep -m1 ':' | tr -d ' ' || true)"
[ -n "$PRIV" ] && [ -n "$ADDR4" ] && [ -n "$PUB" ] && [ -n "$EP" ] || die "could not parse wgcf-profile.conf"

cat > /etc/wireguard/warp.conf <<EOF
# Managed by setup-warp.sh — Cloudflare WARP outbound (policy-routed only)
[Interface]
PrivateKey = $PRIV
Address = $ADDR4
${ADDR6:+Address = $ADDR6}
MTU = 1280
Table = off
PostUp = /usr/local/sbin/warp-pbr.sh up
PreDown = /usr/local/sbin/warp-pbr.sh down
[Peer]
PublicKey = $PUB
AllowedIPs = 0.0.0.0/0
AllowedIPs = ::/0
Endpoint = $EP
EOF
chmod 600 /etc/wireguard/warp.conf

# --- PBR plumbing (recreated on every warp up; ipset content owned by panel) -
cat > /usr/local/sbin/warp-pbr.sh <<'PBR'
#!/bin/bash
# Policy-based routing plumbing for the WARP outbound.
set -u
TABLE=200; MARK=0x2; IPSET=warp_users
add() { iptables -t "$1" -C "${@:2}" 2>/dev/null || iptables -t "$1" -A "${@:2}"; }
del() { iptables -t "$1" -D "${@:2}" 2>/dev/null || true; }
case "${1:-up}" in
  up)
    ipset create $IPSET hash:ip -exist
    ip route replace default dev warp table $TABLE
    add mangle PREROUTING -m set --match-set $IPSET src -j MARK --set-mark $MARK
    add nat POSTROUTING -o warp -j MASQUERADE
    add mangle FORWARD -o warp -p tcp -m tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
    # FORWARD policy is DROP on this node -> must explicitly allow forwarding
    # to/from warp, else NEW warp-user packets are dropped (no internet).
    iptables -C FORWARD -o warp -j ACCEPT 2>/dev/null || iptables -I FORWARD -o warp -j ACCEPT
    iptables -C FORWARD -i warp -j ACCEPT 2>/dev/null || iptables -I FORWARD -i warp -j ACCEPT
    ;;
  down)
    ip rule del fwmark $MARK lookup $TABLE 2>/dev/null || true
    del mangle PREROUTING -m set --match-set $IPSET src -j MARK --set-mark $MARK
    del nat POSTROUTING -o warp -j MASQUERADE
    del mangle FORWARD -o warp -p tcp -m tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
    iptables -D FORWARD -o warp -j ACCEPT 2>/dev/null || true
    iptables -D FORWARD -i warp -j ACCEPT 2>/dev/null || true
    ip route flush table $TABLE 2>/dev/null || true
    ;;
esac
PBR
chmod +x /usr/local/sbin/warp-pbr.sh

# --- health-check + automatic fallback to direct ----------------------------
cat > /usr/local/sbin/warp-health.sh <<'HC'
#!/bin/bash
# If WARP is healthy -> install the fwmark rule (warp_users egress via WARP).
# If WARP is down    -> remove it (warp_users fall back to the main table=direct)
# and try to bring WARP back.
set -u
TABLE=200; MARK=0x2; PRIO=1000
healthy() { curl -s --max-time 6 --interface warp https://1.1.1.1/cdn-cgi/trace 2>/dev/null | grep -q '^warp=on'; }
have_rule() { ip rule show | grep -q "fwmark $MARK lookup $TABLE"; }
if healthy; then
  have_rule || ip rule add fwmark $MARK lookup $TABLE priority $PRIO
else
  have_rule && ip rule del fwmark $MARK lookup $TABLE
  systemctl is-active --quiet wg-quick@warp && wg-quick down warp >/dev/null 2>&1
  systemctl start wg-quick@warp >/dev/null 2>&1 || true
fi
HC
chmod +x /usr/local/sbin/warp-health.sh

cat > /etc/systemd/system/warp-health.service <<'EOF'
[Unit]
Description=WARP outbound health-check + fallback to direct
After=wg-quick@warp.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/warp-health.sh
EOF
cat > /etc/systemd/system/warp-health.timer <<'EOF'
[Unit]
Description=Run WARP outbound health-check every 30s
[Timer]
OnBootSec=30
OnUnitActiveSec=30
AccuracySec=5s
[Install]
WantedBy=timers.target
EOF

log "starting WARP..."
systemctl daemon-reload
systemctl enable --now wg-quick@warp
systemctl enable --now warp-health.timer
sleep 4
/usr/local/sbin/warp-health.sh || true

# --- verify ------------------------------------------------------------------
log "=== verification ==="
echo "warp iface : $(wg show warp 2>/dev/null | grep -E 'interface|endpoint|transfer' | tr '\n' ' ')"
echo "direct  egress: $(curl -s --max-time 8 https://1.1.1.1/cdn-cgi/trace 2>/dev/null | grep -E '^ip=')"
echo "warp    egress: $(curl -s --max-time 8 --interface warp https://1.1.1.1/cdn-cgi/trace 2>/dev/null | grep -E '^ip=|^warp=' | tr '\n' ' ')"
echo "ipset      : $(ipset list $IPSET -terse 2>/dev/null | tr '\n' ' ')"
echo "fwmark rule: $(ip rule show | grep "fwmark $MARK" || echo '(absent — added when warp healthy)')"
echo "table $TABLE  : $(ip route show table $TABLE)"
echo "mangle mark: $(iptables -t mangle -S PREROUTING | grep -c warp_users) | masq: $(iptables -t nat -S POSTROUTING | grep -c '\-o warp') | mss: $(iptables -t mangle -S FORWARD | grep -c '\-o warp')"
log "WARP outbound infra ready. ipset is empty -> zero impact until the panel opts users in."
