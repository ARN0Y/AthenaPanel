#!/usr/bin/env bash
###############################################################################
# Turn a bare server into an Athena termination node.
#
#     bash node-bootstrap.sh [--wg-only] [--no-sstp] [--no-l2tp] [--no-wg]
#
# Selective on purpose: a small node has no business running four daemons it
# will never serve, and the panel already knows which protocols a node offers
# because the agent reports what is actually bound.
#
# Expects the credentials from the panel to be in place first:
#     /etc/athena-agent/{ca.crt,node.crt,node.key}   (key mode 0600)
#     /etc/athena-agent.env                          (hub address + token)
#
# Re-runnable. Every step checks before it acts, so a partial run can simply be
# repeated rather than unpicked.
###############################################################################
set -euo pipefail

WANT_L2TP=1
WANT_SSTP=1
WANT_WG=1
AGENT_URL="${AGENT_URL:-}"

while [ $# -gt 0 ]; do
    case "$1" in
        --wg-only)  WANT_L2TP=0; WANT_SSTP=0; WANT_WG=1 ;;
        --no-l2tp)  WANT_L2TP=0 ;;
        --no-sstp)  WANT_SSTP=0 ;;
        --no-wg)    WANT_WG=0 ;;
        --agent-url) AGENT_URL="$2"; shift ;;
        -h|--help)
            sed -n '2,20p' "$0" | sed 's/^# \?//'
            exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
    shift
done

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root"

ENV_FILE=/etc/athena-agent.env
CERT_DIR=/etc/athena-agent
[ -f "$ENV_FILE" ] || die "$ENV_FILE missing — register the node in the panel first and copy its files here"
for f in ca.crt node.crt node.key; do
    [ -f "$CERT_DIR/$f" ] || die "$CERT_DIR/$f missing — copy the bundle from the panel"
done
chmod 600 "$CERT_DIR/node.key" "$ENV_FILE" 2>/dev/null || true

log "installing packages"
export DEBIAN_FRONTEND=noninteractive
PKGS="iptables"
[ "$WANT_L2TP" = 1 ] && PKGS="$PKGS xl2tpd ppp libreswan"
[ "$WANT_SSTP" = 1 ] && PKGS="$PKGS accel-ppp"
[ "$WANT_WG" = 1 ]   && PKGS="$PKGS wireguard-tools wireguard"
apt-get update -qq >/dev/null 2>&1 || warn "apt update failed; continuing with what is cached"
# --no-install-recommends keeps a small node small; none of the recommends are
# needed to terminate a session.
apt-get install -y -qq --no-install-recommends $PKGS >/dev/null 2>&1 \
    || warn "some packages failed to install; check them individually"

log "kernel forwarding"
cat > /etc/sysctl.d/99-athena-node.conf <<'EOF'
net.ipv4.ip_forward = 1
# Loose reverse-path checking. A node forwards traffic that arrives on one
# interface and leaves by another, which strict mode drops.
net.ipv4.conf.all.rp_filter = 2
EOF
sysctl -q --system

log "ppp hooks -> the local agent"
# On the master these call the panel directly. Here they call the agent on
# loopback: putting a WAN round trip inside the authentication path would make
# a slow link delay or fail customer logins, which is exactly backwards.
install -d -m 0755 /etc/ppp/ip-up.d /etc/ppp/ip-down.d
cat > /etc/ppp/ip-up.d/athena <<'EOF'
#!/bin/sh
# $1 iface  $5 peer-ip  $PEERNAME username
[ -n "$PEERNAME" ] || exit 0
PID=$(cat "/var/run/$1.pid" 2>/dev/null || echo 0)
curl -s -m 3 -X POST "http://127.0.0.1:8711/session-up" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$PEERNAME\",\"ifname\":\"$1\",\"peer_ip\":\"$5\",\"pid\":$PID}" \
  >/dev/null 2>&1 || true
exit 0
EOF
cat > /etc/ppp/ip-down.d/athena <<'EOF'
#!/bin/sh
[ -n "$PEERNAME" ] || exit 0
curl -s -m 3 -X POST "http://127.0.0.1:8711/session-down" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$PEERNAME\",\"ifname\":\"$1\",\"in_octets\":${BYTES_RCVD:-0},\"out_octets\":${BYTES_SENT:-0},\"session_time\":${CONNECT_TIME:-0}}" \
  >/dev/null 2>&1 || true
exit 0
EOF
chmod 0755 /etc/ppp/ip-up.d/athena /etc/ppp/ip-down.d/athena

WG_IFACE="${WG_IFACE:-wg-panel}"
WG_PORT="${WG_PORT:-51820}"
# The interface address MUST be the gateway of the pool the panel allocates peer
# addresses from (backend config `wg_pool`, default 10.10.0.0/16). Not a
# stylistic choice: the peer's address is what the node has to route and NAT, and
# a peer outside the interface's subnet gets neither. Configuring the peer over
# netlink does not create a route the way wg-quick does, so the connected route
# from THIS address is what makes the tunnel carry traffic at all. Get it wrong
# and the handshake still completes, which is exactly what makes it hard to spot.
WG_ADDR="${WG_ADDR:-10.10.0.1/16}"
WG_POOL="${WG_POOL:-10.10.0.0/16}"
if [ "$WANT_WG" = 1 ]; then
    log "wireguard interface $WG_IFACE"
    # PEERS ARE NOT WRITTEN HERE. The interface is infrastructure and belongs to
    # the machine; the peer list belongs to the panel and arrives over the
    # control stream, so the agent applies it and can revoke a key the moment
    # credit runs out. A peer written into a config file could only be changed
    # by re-running this script.
    install -d -m 0700 /etc/wireguard
    if [ ! -s "/etc/wireguard/$WG_IFACE.key" ]; then
        umask 077
        wg genkey > "/etc/wireguard/$WG_IFACE.key"
        log "  generated a new server key"
    fi
    wg pubkey < "/etc/wireguard/$WG_IFACE.key" > "/etc/wireguard/$WG_IFACE.pub"
    cat > "/etc/wireguard/$WG_IFACE.conf" <<EOF
# Managed by node-bootstrap.sh. Peers are pushed by the panel, not written here.
[Interface]
Address = $WG_ADDR
ListenPort = $WG_PORT
PrivateKey = $(cat "/etc/wireguard/$WG_IFACE.key")
EOF
    chmod 600 "/etc/wireguard/$WG_IFACE.conf" "/etc/wireguard/$WG_IFACE.key"
    # The marker the agent checks before it will ever rewrite this interface's
    # peer list. A node is usually not a blank machine — it may already carry a
    # backhaul or a WARP tunnel — and the agent replaces peers wholesale, so it
    # manages an interface it was GIVEN and never one it merely found.
    printf 'created by node-bootstrap.sh for the Athena panel\n' > "/etc/wireguard/$WG_IFACE.athena"
    systemctl enable "wg-quick@$WG_IFACE" >/dev/null 2>&1 || true
    # restart, not start: wg-quick is a oneshot that reports "active (exited)"
    # once it has run, so start is a no-op on a stale interface. That exact
    # mistake kept a dead WARP tunnel "healthy" for nine days.
    systemctl restart "wg-quick@$WG_IFACE" || warn "wg-quick@$WG_IFACE failed to start"
    printf '  server key: %s\n' "$(cat "/etc/wireguard/$WG_IFACE.pub")"
    grep -q "^ATHENA_WG_IFACE=" /etc/athena-agent.env 2>/dev/null \
        || echo "ATHENA_WG_IFACE=$WG_IFACE" >> /etc/athena-agent.env
else
    # An agent told to watch an interface that does not exist would report a
    # WireGuard capability this node cannot serve.
    grep -q "^ATHENA_WG_IFACE=" /etc/athena-agent.env 2>/dev/null \
        || echo "ATHENA_WG_IFACE=" >> /etc/athena-agent.env
fi

log "egress NAT"
WAN=$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'dev \K\S+' | head -1)
[ -n "$WAN" ] || die "could not determine the WAN interface"
# 192.168/16 covers the ppp pools (L2TP .42, raw .45, SSTP .44); WG_POOL is where
# the panel allocates WireGuard peers. A pool that is not masqueraded here means
# customers hand-shake successfully and then reach nothing, which reads as "the
# VPN is broken" with no error anywhere to explain it.
for net in 192.168.0.0/16 "$WG_POOL"; do
    iptables -t nat -C POSTROUTING -s "$net" -o "$WAN" -j MASQUERADE 2>/dev/null \
        || iptables -t nat -A POSTROUTING -s "$net" -o "$WAN" -j MASQUERADE
done

log "agent binary"
if [ -n "$AGENT_URL" ]; then
    curl -fsSL "$AGENT_URL" -o /usr/local/bin/athena-agent
    chmod 0755 /usr/local/bin/athena-agent
fi
[ -x /usr/local/bin/athena-agent ] \
    || die "/usr/local/bin/athena-agent missing — copy it here or pass --agent-url"
/usr/local/bin/athena-agent -version

log "agent service"
cat > /etc/systemd/system/athena-agent.service <<'EOF'
[Unit]
# Reports what this node is doing and enforces quota locally. NOT in the data
# path: pppd, xl2tpd, accel-ppp and kernel WireGuard carry the traffic, so
# restarting this disconnects nobody — which is what makes updates cheap.
Description=Athena node agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
EnvironmentFile=/etc/athena-agent.env
ExecStart=/usr/local/bin/athena-agent
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=15

NoNewPrivileges=true
PrivateTmp=true
# NOT ProtectHome: the agent reads its credentials from /etc, but hiding /root
# has bitten this setup before when files were left there by mistake.
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now athena-agent

sleep 6
echo
log "result"
printf '  agent:      %s\n' "$(systemctl is-active athena-agent)"
printf '  protocols:  l2tp=%s sstp=%s wireguard=%s\n' "$WANT_L2TP" "$WANT_SSTP" "$WANT_WG"
printf '  wan iface:  %s\n' "$WAN"
printf '  hook probe: %s\n' "$(curl -s -m 2 http://127.0.0.1:8711/healthz || echo UNREACHABLE)"
echo
journalctl -u athena-agent --no-pager -n 6 | sed 's/^/  /'
echo
log "If the agent connected, the node now appears online in the panel."
