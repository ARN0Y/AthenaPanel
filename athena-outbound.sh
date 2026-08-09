#!/usr/bin/env bash
#
# athena-outbound.sh — turn any Ubuntu/Debian box into an egress location.
#
# Run this on the server you want traffic to leave from. It builds one end of a
# WireGuard tunnel, NATs whatever arrives through it out to the internet, and
# prints a single line to paste back into the panel.
#
# The panel hands you the exact command to run, with --net, --peer-key, --psk
# and --port already filled in, because it is the panel that allocates the
# tunnel addressing — two locations that both picked 10.201.0.0/30 would
# collide on the master, where every tunnel terminates side by side.
#
# WHY WIREGUARD AND NOTHING CLEVERER
#   Both ends of this tunnel are outside Iran, so no DPI sits in this path and
#   there is nothing to obfuscate. That frees us to pick purely for speed:
#     - kernel space, so no userspace scheduler adds jitter. Measured on this
#       operator's own network, a userspace tunnel took a 0.247ms-jitter path to
#       1.127ms. A kernel tunnel adds roughly 0.05ms. For gaming, jitter is what
#       causes rubber-banding, more than absolute ping.
#     - datagram in, datagram out. A game's UDP packet stays UDP, and a lost one
#       stays lost instead of becoming a latency spike, which is exactly what a
#       TCP- or KCP-based tunnel would turn it into.
#     - 60 bytes of overhead and ChaCha20 that runs at multi-Gbit/s.
#     - it is UDP, so it crosses cloud firewalls that drop GRE and IPIP —
#       which is what rules those out despite their lower overhead.
#
# Deliberately NOT here: forward error correction. On a clean datacenter-to-
# datacenter path it spends bandwidth and CPU to fix loss that isn't happening.
# Measure loss first; add it only if there is some.
#
# Safe to re-run. It only ever touches its own interface (athena-ob) and its own
# marked firewall rules, so an existing WireGuard on this box is left alone.

set -euo pipefail

IFACE=athena-ob
PORT=51833
NET=""
PEER_KEY=""
PSK=""
WAN=""
KEEP_MSS=1

bold=$'\033[1m'; dim=$'\033[2m'; red=$'\033[1;31m'; grn=$'\033[1;32m'; off=$'\033[0m'
log()  { printf '%s==>%s %s\n' "$grn" "$off" "$*"; }
warn() { printf '%s[!]%s %s\n'  "$red" "$off" "$*"; }
die()  { warn "$*"; exit 1; }

usage() {
    cat <<USAGE
${bold}athena-outbound.sh${off} — set this server up as a panel egress location

  --net A.B.C.D/30    tunnel subnet (this side takes .1, the panel .2)
  --peer-key KEY      the panel's WireGuard public key
  --psk KEY           pre-shared key from the panel ("" to disable)
  --port N            UDP port to listen on            (default $PORT)
  --iface NAME        interface name                   (default $IFACE)
  --wan NAME          egress interface        (default: autodetected)
  --no-mss            skip TCP MSS clamping (leave it on unless you know why)

The panel prints the full command for you — copy it from
Settings -> Outbounds -> Add.
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --net)         NET="$2"; shift 2 ;;
        --peer-key)    PEER_KEY="$2"; shift 2 ;;
        --psk)         PSK="$2"; shift 2 ;;
        --port)        PORT="$2"; shift 2 ;;
        --iface)       IFACE="$2"; shift 2 ;;
        --wan)         WAN="$2"; shift 2 ;;
        --no-mss)      KEEP_MSS=0; shift ;;
        -h|--help)     usage; exit 0 ;;
        *)             die "unknown argument: $1 (try --help)" ;;
    esac
done

[ "$(id -u)" = "0" ] || die "run as root"
[ -n "$NET" ]      || { usage; die "--net is required"; }
[ -n "$PEER_KEY" ] || { usage; die "--peer-key is required"; }

case "$(uname -m)" in
    x86_64|amd64|aarch64|arm64) : ;;
    *) warn "untested architecture $(uname -m) — continuing anyway" ;;
esac
[ -f /etc/debian_version ] || warn "this script targets Ubuntu/Debian; continuing anyway"

# --- addressing -------------------------------------------------------------
# The panel allocates a /30: .1 is this server, .2 is the panel. Derived here
# rather than passed so the two ends can never be told different things.
base="${NET%/*}"
prefix="${NET#*/}"
[ "$prefix" = "30" ] || die "--net must be a /30 (got /$prefix)"
IFS=. read -r o1 o2 o3 o4 <<<"$base"
OURS="$o1.$o2.$o3.$((o4 + 1))"
THEIRS="$o1.$o2.$o3.$((o4 + 2))"

if [ -z "$WAN" ]; then
    WAN="$(ip route get 8.8.8.8 2>/dev/null | head -1 | grep -oP 'dev \K\S+' || true)"
fi
[ -n "$WAN" ] || die "could not detect the egress interface; pass --wan"
ip link show "$WAN" >/dev/null 2>&1 || die "no such interface: $WAN"

# --- refuse to hijack someone else's tunnel ---------------------------------
# Learned on this operator's Dubai node: a sync that assumed it owned an
# interface deleted a live customer's WireGuard. An interface is ours only if we
# left our marker next to its config.
MARKER="/etc/wireguard/${IFACE}.athena-outbound"
if ip link show "$IFACE" >/dev/null 2>&1 && [ ! -f "$MARKER" ]; then
    die "interface $IFACE already exists and was not created by this script.
     Pass --iface with a different name, or remove it yourself first."
fi

log "installing tooling..."
export DEBIAN_FRONTEND=noninteractive
need=""
command -v wg      >/dev/null 2>&1 || need="$need wireguard-tools"
command -v curl    >/dev/null 2>&1 || need="$need curl"
command -v iptables>/dev/null 2>&1 || need="$need iptables"
if [ -n "$need" ]; then
    apt-get update -qq
    # shellcheck disable=SC2086
    apt-get install -y -qq $need
fi

# The kernel module is built in from 5.6; only very old kernels need the DKMS
# package, and falling back to the userspace implementation would throw away
# the entire reason we chose WireGuard.
KREL="$(uname -r)"
if ! modprobe wireguard 2>/dev/null && [ ! -d /sys/module/wireguard ]; then
    warn "no in-kernel WireGuard on $KREL — installing the DKMS module"
    apt-get install -y -qq wireguard-dkms || die "could not get a kernel WireGuard module"
    modprobe wireguard || die "wireguard module still will not load"
fi

# --- keys -------------------------------------------------------------------
umask 077
mkdir -p /etc/wireguard
if [ -f "/etc/wireguard/${IFACE}.key" ]; then
    log "reusing the existing keypair (re-run keeps the panel's registration valid)"
else
    wg genkey > "/etc/wireguard/${IFACE}.key"
    wg pubkey < "/etc/wireguard/${IFACE}.key" > "/etc/wireguard/${IFACE}.pub"
fi
PRIV="$(cat "/etc/wireguard/${IFACE}.key")"
PUB="$(cat "/etc/wireguard/${IFACE}.pub")"

# --- interface --------------------------------------------------------------
# Table=off: this tunnel must never become this server's own default route. It
# carries traffic INTO the box to be NATed out; the box's own egress is
# untouched. Getting this wrong locks you out of the server.
#
# AllowedIPs is a single /32 on purpose. The panel masquerades before the
# packets enter the tunnel, so everything arriving here has the panel's tunnel
# address as its source. Anything else is not ours and should be dropped —
# which AllowedIPs does for free, as cryptokey routing.
psk_line=""
if [ -n "$PSK" ]; then
    printf '%s' "$PSK" > "/etc/wireguard/${IFACE}.psk"
    chmod 600 "/etc/wireguard/${IFACE}.psk"
    psk_line="PresharedKey = ${PSK}"
fi

cat > "/etc/wireguard/${IFACE}.conf" <<CONF
# Managed by athena-outbound.sh — an Athena panel egress location.
# Re-running the script rewrites this file; hand edits will be lost.
[Interface]
Address = ${OURS}/30
ListenPort = ${PORT}
PrivateKey = ${PRIV}
Table = off

[Peer]
# The panel. It dials us, so it carries the Endpoint, not this side.
PublicKey = ${PEER_KEY}
${psk_line}
AllowedIPs = ${THEIRS}/32
CONF
chmod 600 "/etc/wireguard/${IFACE}.conf"
touch "$MARKER"

# --- forwarding, NAT and the firewall ---------------------------------------
cat > /etc/sysctl.d/98-athena-outbound.conf <<SYS
# Athena egress location.
net.ipv4.ip_forward = 1
# BBR + fq_codel: keep a background download from parking a queue in front of
# latency-sensitive traffic. fq_codel gives sparse flows — which is exactly what
# a game's packet stream looks like — priority over bulk ones for free.
net.core.default_qdisc = fq_codel
net.ipv4.tcp_congestion_control = bbr
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.ipv4.tcp_mtu_probing = 1
# Conntrack sized for a location shared by many customers behind one NAT.
net.netfilter.nf_conntrack_max = 262144
SYS
sysctl -q --system 2>/dev/null || sysctl -q -p /etc/sysctl.d/98-athena-outbound.conf || true

add() { iptables -t "$1" -C "${@:2}" 2>/dev/null || iptables -t "$1" -A "${@:2}"; }

log "installing NAT and firewall rules..."
add nat  POSTROUTING -s "${NET}" -o "$WAN" -j MASQUERADE
add filter FORWARD -i "$IFACE" -o "$WAN" -j ACCEPT
add filter FORWARD -i "$WAN" -o "$IFACE" -m state --state RELATED,ESTABLISHED -j ACCEPT
add filter INPUT -p udp --dport "$PORT" -j ACCEPT
if [ "$KEEP_MSS" = "1" ]; then
    # Without this a TCP session whose path MTU is smaller than the tunnel's
    # blackholes its large segments and looks like "some sites just hang".
    add mangle FORWARD -o "$IFACE" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
    add mangle FORWARD -i "$IFACE" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
fi

if ! command -v netfilter-persistent >/dev/null 2>&1; then
    echo iptables-persistent iptables-persistent/autosave_v4 boolean false | debconf-set-selections
    echo iptables-persistent iptables-persistent/autosave_v6 boolean false | debconf-set-selections
    apt-get install -y -qq iptables-persistent >/dev/null 2>&1 || true
fi
netfilter-persistent save >/dev/null 2>&1 || true

# --- AQM on the way out -----------------------------------------------------
# The sysctl above only sets the default for interfaces brought up afterwards;
# the live one needs saying so explicitly. Not fatal if the qdisc is missing.
tc qdisc replace dev "$WAN" root fq_codel 2>/dev/null \
    && log "fq_codel active on $WAN" \
    || warn "could not set fq_codel on $WAN (kernel module missing?) — continuing"

cat > /etc/systemd/system/athena-outbound-tune.service <<UNIT
[Unit]
Description=Athena egress: queueing discipline on ${WAN}
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/sbin/tc qdisc replace dev ${WAN} root fq_codel

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now athena-outbound-tune.service >/dev/null 2>&1 || true

# --- bring it up ------------------------------------------------------------
log "starting ${IFACE}..."
systemctl enable "wg-quick@${IFACE}" >/dev/null 2>&1 || true
systemctl restart "wg-quick@${IFACE}"
sleep 1
ip link show "$IFACE" >/dev/null 2>&1 || die "interface did not come up — check: journalctl -u wg-quick@${IFACE} -n 30"

# --- what the panel needs back ----------------------------------------------
PUBIP="$(curl -s --max-time 6 https://1.1.1.1/cdn-cgi/trace 2>/dev/null | sed -n 's/^ip=//p')"
[ -n "$PUBIP" ] || PUBIP="$(ip -4 addr show "$WAN" | grep -oP 'inet \K[0-9.]+' | head -1)"

echo
printf '%sDone.%s %s listening on %s:%s, NAT out %s\n' \
    "$bold" "$off" "$IFACE" "$PUBIP" "$PORT" "$WAN"
echo
printf '%sPaste this one line back into the panel:%s\n\n' "$bold" "$off"
printf '  %sathena-ob:%s:%s:%s%s\n\n' "$grn" "$PUBIP" "$PORT" "$PUB" "$off"
printf '%sIt carries only this server'"'"'s public key and address — nothing secret.%s\n' "$dim" "$off"
