#!/usr/bin/env bash
###############################################################################
# Turn a bare server into an Athena termination node.
#
#   bash node-bootstrap.sh [--wg-only] [--no-l2tp] [--no-wg] [--no-sstp]
#                          [--ipsec-psk SECRET] [--ext-ip ADDR]
#                          [--agent-url URL]
#
# Selective on purpose: a small node has no business running daemons it will
# never serve, and the panel already knows which protocols a node offers because
# the agent reports what is actually bound.
#
# WHAT THIS DOES AND DOES NOT DO
#   WireGuard  fully configured here — interface, keys, NAT, routing. Peers are
#              NOT written to disk; the panel pushes them to the agent.
#   L2TP/IPsec fully configured here. Needs --ipsec-psk, the SAME secret every
#              other server uses, or existing customer configs will not
#              authenticate.
#   SSTP       NOT configured here, and the script will say so rather than
#              pretend. The master runs accel-ppp built from source with a
#              per-host TLS certificate; no apt package reproduces that, and a
#              certificate cannot be invented by a bootstrap script.
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
WANT_SSTP=0          # off by default: see above, it cannot be done from here
WANT_WG=1
AGENT_URL="${AGENT_URL:-}"
IPSEC_PSK="${IPSEC_PSK:-}"
EXT_IP="${EXT_IP:-}"

while [ $# -gt 0 ]; do
    case "$1" in
        --wg-only)    WANT_L2TP=0; WANT_SSTP=0; WANT_WG=1 ;;
        --no-l2tp)    WANT_L2TP=0 ;;
        --no-wg)      WANT_WG=0 ;;
        --no-sstp)    WANT_SSTP=0 ;;
        --sstp)       WANT_SSTP=1 ;;
        --ipsec-psk)  IPSEC_PSK="$2"; shift ;;
        --ext-ip)     EXT_IP="$2"; shift ;;
        --agent-url)  AGENT_URL="$2"; shift ;;
        -h|--help)
            sed -n '2,30p' "$0" | sed 's/^# \?//'
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

# Refuse early rather than half-installing L2TP that can never authenticate.
if [ "$WANT_L2TP" = 1 ] && [ -z "$IPSEC_PSK" ]; then
    die "L2TP needs --ipsec-psk (the same secret the other servers use), or pass --no-l2tp"
fi

WAN=$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'dev \K\S+' | head -1)
[ -n "$WAN" ] || die "could not determine the WAN interface"
WAN_IP=$(ip -4 addr show "$WAN" | grep -oP 'inet \K[0-9.]+' | head -1)
[ -n "$EXT_IP" ] || EXT_IP="$WAN_IP"

L2TP_POOL_NET="${L2TP_POOL_NET:-192.168.42}"
WG_IFACE="${WG_IFACE:-wg-panel}"
WG_PORT="${WG_PORT:-51820}"
# The interface address MUST be the gateway of the pool the panel allocates peer
# addresses from (backend config `wg_pool`, default 10.10.0.0/16). Not a
# stylistic choice: the peer's address is what the node has to route and NAT, and
# a peer outside the interface's subnet gets neither. Configuring a peer over
# netlink does not create a route the way wg-quick does, so the connected route
# from THIS address is what makes the tunnel carry traffic at all. Get it wrong
# and the handshake still completes, which is what makes it so hard to spot.
WG_ADDR="${WG_ADDR:-10.10.0.1/16}"
WG_POOL="${WG_POOL:-10.10.0.0/16}"

log "installing packages"
export DEBIAN_FRONTEND=noninteractive
PKGS="iptables iptables-persistent"
[ "$WANT_L2TP" = 1 ] && PKGS="$PKGS xl2tpd ppp libreswan"
[ "$WANT_WG" = 1 ]   && PKGS="$PKGS wireguard-tools"
# Answer iptables-persistent's prompts before it can ask: an interactive
# debconf question in a bootstrap script hangs forever on a headless box.
echo iptables-persistent iptables-persistent/autosave_v4 boolean false | debconf-set-selections
echo iptables-persistent iptables-persistent/autosave_v6 boolean false | debconf-set-selections
apt-get update -qq >/dev/null 2>&1 || warn "apt update failed; continuing with what is cached"
apt-get install -y -qq --no-install-recommends $PKGS >/dev/null 2>&1 \
    || warn "some packages failed to install; check them individually"

log "kernel settings"
cat > /etc/sysctl.d/99-athena-node.conf <<'EOF'
net.ipv4.ip_forward = 1
# Loose reverse-path checking. A node forwards traffic that arrives on one
# interface and leaves by another, which strict mode drops.
net.ipv4.conf.all.rp_filter = 2
net.ipv4.conf.default.rp_filter = 2
# Buffers and BBR. A node terminates hundreds of tunnels over a long fat path;
# the defaults are sized for a desktop and cost real throughput. Same values the
# master runs.
net.core.rmem_max = 268435456
net.core.wmem_max = 268435456
net.ipv4.tcp_rmem = 4096 87380 67108864
net.ipv4.tcp_wmem = 4096 65536 67108864
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
# Hundreds of tunnels means hundreds of thousands of conntrack entries.
net.netfilter.nf_conntrack_max = 524288
EOF
sysctl -q --system 2>/dev/null || warn "some sysctl keys were rejected (older kernel?)"

log "ppp hooks -> the local agent"
# On the master these call the panel directly. Here they call the agent on
# loopback: putting a WAN round trip inside the authentication path would make a
# slow link delay or fail customer logins, which is exactly backwards. The
# timeout is 1s and not 3 for the same reason — this runs while a customer waits.
install -d -m 0755 /etc/ppp/ip-up.d /etc/ppp/ip-down.d
cat > /etc/ppp/ip-up.d/athena <<'EOF'
#!/bin/sh
# $1 iface  $5 peer-ip  $PEERNAME username
[ -n "$PEERNAME" ] || exit 0
PID=$(cat "/var/run/$1.pid" 2>/dev/null || echo 0)
curl -s -m 1 --connect-timeout 1 -X POST "http://127.0.0.1:8711/session-up" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$PEERNAME\",\"ifname\":\"$1\",\"peer_ip\":\"$5\",\"pid\":$PID}" \
  >/dev/null 2>&1 || true
exit 0
EOF
cat > /etc/ppp/ip-down.d/athena <<'EOF'
#!/bin/sh
[ -n "$PEERNAME" ] || exit 0
curl -s -m 1 --connect-timeout 1 -X POST "http://127.0.0.1:8711/session-down" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$PEERNAME\",\"ifname\":\"$1\",\"in_octets\":${BYTES_RCVD:-0},\"out_octets\":${BYTES_SENT:-0},\"session_time\":${CONNECT_TIME:-0}}" \
  >/dev/null 2>&1 || true
exit 0
EOF
chmod 0755 /etc/ppp/ip-up.d/athena /etc/ppp/ip-down.d/athena

if [ "$WANT_L2TP" = 1 ]; then
    log "l2tp/ipsec"
    # The account file is the agent's to own; create it only so pppd has
    # something to read before the first sync arrives.
    [ -f /etc/ppp/chap-secrets ] || {
        printf '# Managed by athena-agent. DO NOT EDIT BY HAND.\n' > /etc/ppp/chap-secrets
        chmod 600 /etc/ppp/chap-secrets
    }
    install -d -m 0755 /etc/xl2tpd
    cat > /etc/xl2tpd/xl2tpd.conf <<EOF
[global]
port = 1701

[lns default]
ip range = ${L2TP_POOL_NET}.10-${L2TP_POOL_NET}.250
local ip = ${L2TP_POOL_NET}.1
require chap = yes
refuse pap = yes
require authentication = yes
name = l2tpd
pppoptfile = /etc/ppp/options.xl2tpd
length bit = yes
EOF
    # mtu/mru 1280: the customer's packets are already inside L2TP, inside
    # IPsec, and often inside a backhaul on top of that. Anything larger
    # fragments, and fragmented ESP is dropped by many Iranian carriers.
    cat > /etc/ppp/options.xl2tpd <<'EOF'
+mschap-v2
ipcp-accept-local
ipcp-accept-remote
noccp
auth
mtu 1280
mru 1280
proxyarp
lcp-echo-failure 4
lcp-echo-interval 30
connect-delay 5000
ms-dns 8.8.8.8
ms-dns 8.8.4.4
EOF
    cat > /etc/ipsec.conf <<EOF
version 2.0

config setup
  ikev1-policy=accept
  virtual-private=%v4:10.0.0.0/8,%v4:192.168.0.0/16,%v4:172.16.0.0/12,%v4:!${L2TP_POOL_NET}.0/24,%v6:fc00::/7
  uniqueids=no

conn shared
  replay-window=0
  left=%defaultroute
  # The identity customers authenticate against is the address they DIAL, which
  # for a relayed node is the relay's, not this machine's.
  leftid=${EXT_IP}
  right=%any
  encapsulation=yes
  authby=secret
  pfs=no
  rekey=no
  dpddelay=30
  dpdtimeout=300
  ikev2=never
  ike=aes256-sha2;modp2048,aes128-sha2;modp2048,aes256-sha1;modp2048,aes128-sha1;modp2048
  phase2alg=aes_gcm-null,aes128-sha1,aes256-sha1,aes256-sha2_512,aes128-sha2,aes256-sha2
  ikelifetime=24h
  salifetime=24h
  sha2-truncbug=no

conn l2tp-psk
  auto=add
  leftprotoport=17/1701
  rightprotoport=17/%any
  type=transport
  also=shared

include /etc/ipsec.d/*.conf
EOF
    printf '%%any %%any : PSK "%s"\n' "$IPSEC_PSK" > /etc/ipsec.secrets
    chmod 600 /etc/ipsec.secrets
    systemctl enable xl2tpd ipsec >/dev/null 2>&1 || true
    systemctl restart ipsec  || warn "ipsec failed to start — check 'ipsec verify'"
    systemctl restart xl2tpd || warn "xl2tpd failed to start"
    printf '  l2tp pool: %s.10-%s.250   ipsec identity: %s\n' \
        "$L2TP_POOL_NET" "$L2TP_POOL_NET" "$EXT_IP"
fi

if [ "$WANT_SSTP" = 1 ]; then
    warn "SSTP cannot be configured by this script."
    warn "  The master runs accel-ppp BUILT FROM SOURCE with a per-host TLS"
    warn "  certificate at /etc/accel-ppp/sstp-combined.pem. No apt package"
    warn "  reproduces that, and a certificate cannot be invented here."
    warn "  Copy the build and issue a certificate for this node by hand, then"
    warn "  the agent will report SSTP as available on its own."
fi

if [ "$WANT_WG" = 1 ]; then
    log "wireguard interface $WG_IFACE"
    # PEERS ARE NOT WRITTEN HERE. The interface is infrastructure and belongs to
    # the machine; the peer list belongs to the panel and arrives over the
    # control stream, so the agent applies it and can revoke a key the moment
    # credit runs out. A peer written into a config file could only be changed by
    # re-running this script.
    install -d -m 0700 /etc/wireguard
    if [ ! -s "/etc/wireguard/$WG_IFACE.key" ]; then
        (umask 077; wg genkey > "/etc/wireguard/$WG_IFACE.key")
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
    printf '  server key: %s   port: %s\n' "$(cat "/etc/wireguard/$WG_IFACE.pub")" "$WG_PORT"
    sed -i '/^ATHENA_WG_IFACE=/d' "$ENV_FILE"
    echo "ATHENA_WG_IFACE=$WG_IFACE" >> "$ENV_FILE"
else
    # An agent told to watch an interface that does not exist would report a
    # WireGuard capability this node cannot serve.
    sed -i '/^ATHENA_WG_IFACE=/d' "$ENV_FILE"
    echo "ATHENA_WG_IFACE=" >> "$ENV_FILE"
fi

log "firewall + NAT"
add_rule() {  # table chain rule...
    local t="$1" c="$2"; shift 2
    iptables -t "$t" -C "$c" "$@" 2>/dev/null || iptables -t "$t" -A "$c" "$@"
}
# 192.168/16 covers the ppp pools; WG_POOL is where the panel allocates
# WireGuard peers. A pool that is not masqueraded means customers hand-shake
# successfully and then reach nothing, which reads as "the VPN is broken" with
# no error anywhere to explain it.
for net in 192.168.0.0/16 "$WG_POOL"; do
    add_rule nat POSTROUTING -s "$net" -o "$WAN" -j MASQUERADE
done

# MSS clamping. Without it a tunnel connects, small requests work, and any page
# large enough to need a full-size segment hangs forever — the single most
# common "it connects but the internet does not work" report. The master pins
# 1240 to match its 1280 ppp MTU rather than relying on PMTU discovery, which
# ICMP-filtering networks break.
add_rule filter FORWARD -i ppp+ -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1240
add_rule filter FORWARD -o ppp+ -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1240
if [ "$WANT_WG" = 1 ]; then
    add_rule filter FORWARD -i "$WG_IFACE" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
    add_rule filter FORWARD -o "$WG_IFACE" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
fi

# Explicit accepts for what this node serves. A no-op where INPUT already
# defaults to ACCEPT, and the difference between working and silently dead where
# it does not.
[ "$WANT_L2TP" = 1 ] && {
    add_rule filter INPUT -p udp --dport 1701 -j ACCEPT
    add_rule filter INPUT -p udp --dport 500  -j ACCEPT
    add_rule filter INPUT -p udp --dport 4500 -j ACCEPT
    add_rule filter INPUT -p esp -j ACCEPT
}
[ "$WANT_WG" = 1 ] && add_rule filter INPUT -p udp --dport "$WG_PORT" -j ACCEPT

# Persist. Adding rules that vanish on the next reboot is worse than not adding
# them, because nothing fails until the box restarts months later and nobody
# connects the two events.
install -d -m 0755 /etc/iptables
iptables-save  > /etc/iptables/rules.v4
ip6tables-save > /etc/iptables/rules.v6 2>/dev/null || true
systemctl enable netfilter-persistent >/dev/null 2>&1 \
    || warn "netfilter-persistent is not available — rules are saved but will NOT be restored at boot"

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
StartLimitIntervalSec=60
StartLimitBurst=10

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
# Its key material lives in /etc/athena-agent, so /root can stay hidden — and
# should, because a node is a machine other people also log into.
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now athena-agent
systemctl restart athena-agent

sleep 6
echo
log "result"
printf '  agent:      %s (%s)\n' "$(systemctl is-active athena-agent)" "$(/usr/local/bin/athena-agent -version)"
printf '  protocols:  l2tp=%s wireguard=%s sstp=%s\n' "$WANT_L2TP" "$WANT_WG" \
       "$([ "$WANT_SSTP" = 1 ] && echo 'requested, NOT configured' || echo 0)"
printf '  wan:        %s (%s)   external identity: %s\n' "$WAN" "$WAN_IP" "$EXT_IP"
[ "$WANT_L2TP" = 1 ] && printf '  xl2tpd:     %s   ipsec: %s\n' \
    "$(systemctl is-active xl2tpd)" "$(systemctl is-active ipsec)"
[ "$WANT_WG" = 1 ] && printf '  %s:   %s on port %s\n' \
    "$WG_IFACE" "$(ip -br link show "$WG_IFACE" 2>/dev/null | awk '{print $2}')" "$WG_PORT"
printf '  hook probe: %s\n' "$(curl -s -m 2 http://127.0.0.1:8711/healthz || echo UNREACHABLE)"
printf '  persisted:  netfilter-persistent %s\n' "$(systemctl is-enabled netfilter-persistent 2>/dev/null || echo MISSING)"
echo
journalctl -u athena-agent --no-pager -n 6 | sed 's/^/  /'
echo
log "Now set this node's EXTERNAL PROXY in the panel, and forward these on the relay:"
[ "$WANT_WG" = 1 ]   && printf '    udp %s -> %s:%s   (WireGuard)\n' "$WG_PORT" "$WAN_IP" "$WG_PORT"
[ "$WANT_L2TP" = 1 ] && printf '    udp 500,4500,1701 -> %s   (L2TP/IPsec)\n' "$WAN_IP"
echo
log "The public port on the relay need not match the port here — the panel's"
log "external proxy field takes an explicit host:port and the node card shows"
log "exactly what to forward."
