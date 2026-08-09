#!/usr/bin/env bash
#
# outbound-plumb.sh — bring one egress location up or down on the panel host.
#
# Installed to /usr/local/sbin/outbound-plumb.sh by deploy-panel.sh. The panel
# invokes it whenever an outbound is added, removed, enabled or disabled; it is
# also perfectly usable by hand to inspect or repair a location.
#
# It builds the same policy-routing chain setup-warp.sh builds for WARP, once
# per location:
#
#   ipset ob-<name>  --mangle PREROUTING-->  MARK  --ip rule-->  table  -->  dev ob-<name>  --> MASQUERADE
#
# The match is on the CLIENT'S SOURCE IP, which is why one of these covers
# L2TP/IPsec, L2TP raw, SSTP and WireGuard without knowing anything about them.
# The panel owns the ipset's contents; this script only creates it.
#
# Two properties are load-bearing:
#
#   Table = off on the interface. This tunnel must never become the host's own
#   default route — 130-odd customers and the panel itself egress through the
#   main table, and a wg-quick that helpfully installs a default route would
#   move all of them at once.
#
#   The ip rule is added ONLY while the tunnel is healthy. outbound-health.sh
#   withdraws it when the far end stops answering, and marked packets then fall
#   through to the main table, i.e. straight out. A broken location degrades a
#   user to direct; it never blackholes them.
#
# Idempotent: every rule is checked before it is added, so re-running converges
# rather than duplicating.

set -euo pipefail

ACTION="${1:-}"; NAME="${2:-}"
shift 2 2>/dev/null || true

ENDPOINT=""; PEER_KEY=""; PSK=""; PRIVATE_KEY=""; ADDRESS=""
MTU=1380; MARK=""; TABLE=""; PRIORITY=""

while [ $# -gt 0 ]; do
    case "$1" in
        --endpoint)    ENDPOINT="$2"; shift 2 ;;
        --peer-key)    PEER_KEY="$2"; shift 2 ;;
        --psk)         PSK="$2"; shift 2 ;;
        --private-key) PRIVATE_KEY="$2"; shift 2 ;;
        --address)     ADDRESS="$2"; shift 2 ;;
        --mtu)         MTU="$2"; shift 2 ;;
        --mark)        MARK="$2"; shift 2 ;;
        --table)       TABLE="$2"; shift 2 ;;
        --priority)    PRIORITY="$2"; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

die() { echo "outbound-plumb: $*" >&2; exit 1; }

[ "$(id -u)" = "0" ] || die "must run as root"
case "$NAME" in
    ''|*[!a-z0-9-]*) die "bad outbound name: '$NAME'" ;;
esac

IFACE="ob-${NAME}"
IPSET="ob-${NAME}"
CONF="/etc/wireguard/${IFACE}.conf"

have_rule() { ip rule show | grep -q "fwmark ${MARK} lookup ${TABLE}"; }
add() { iptables -t "$1" -C "${@:2}" 2>/dev/null || iptables -t "$1" -A "${@:2}"; }
del() { iptables -t "$1" -D "${@:2}" 2>/dev/null || true; }

case "$ACTION" in
up)
    [ -n "$PEER_KEY" ]    || die "--peer-key is required"
    [ -n "$PRIVATE_KEY" ] || die "--private-key is required"
    [ -n "$ADDRESS" ]     || die "--address is required"
    [ -n "$ENDPOINT" ]    || die "--endpoint is required"
    [ -n "$MARK" ] && [ -n "$TABLE" ] && [ -n "$PRIORITY" ] || die "--mark/--table/--priority are required"

    command -v wg >/dev/null 2>&1    || die "wireguard-tools is not installed"
    command -v ipset >/dev/null 2>&1 || die "ipset is not installed"

    umask 077
    mkdir -p /etc/wireguard
    psk_line=""
    [ -n "$PSK" ] && psk_line="PresharedKey = ${PSK}"

    # PersistentKeepalive because this side dials out: without it the remote's
    # NAT or stateful firewall forgets the flow after a minute or two of an idle
    # location and the next customer packet has nowhere to land.
    #
    # AllowedIPs 0.0.0.0/0 is what makes this a default route FOR THIS TABLE
    # only — combined with Table = off it steers nothing but marked packets.
    # The priority comment is not decoration: outbound-health.sh re-adds this
    # rule after an outage and has to put it back at the same priority, or the
    # locations would silently reorder relative to each other.
    cat > "$CONF" <<CONF
# Managed by the panel (outbound '${NAME}'). Hand edits will be overwritten.
# priority: ${PRIORITY}
[Interface]
PrivateKey = ${PRIVATE_KEY}
Address = ${ADDRESS}
MTU = ${MTU}
Table = off

[Peer]
PublicKey = ${PEER_KEY}
${psk_line}
Endpoint = ${ENDPOINT}
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
CONF
    chmod 600 "$CONF"

    systemctl enable "wg-quick@${IFACE}" >/dev/null 2>&1 || true
    systemctl restart "wg-quick@${IFACE}"
    ip link show "$IFACE" >/dev/null 2>&1 || die "interface ${IFACE} did not come up"

    ipset create "$IPSET" hash:ip -exist
    ip route replace default dev "$IFACE" table "$TABLE"
    add mangle PREROUTING -m set --match-set "$IPSET" src -j MARK --set-mark "$MARK"
    add nat POSTROUTING -o "$IFACE" -j MASQUERADE
    add mangle FORWARD -o "$IFACE" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
    iptables -C FORWARD -o "$IFACE" -j ACCEPT 2>/dev/null || iptables -I FORWARD -o "$IFACE" -j ACCEPT
    iptables -C FORWARD -i "$IFACE" -j ACCEPT 2>/dev/null || iptables -I FORWARD -i "$IFACE" -j ACCEPT

    # The health check owns the ip rule from here on. Add it now so a working
    # location starts carrying traffic immediately instead of at the next tick.
    have_rule || ip rule add fwmark "$MARK" lookup "$TABLE" priority "$PRIORITY"

    echo "outbound ${NAME}: up (iface=${IFACE} mark=${MARK} table=${TABLE} prio=${PRIORITY})"
    ;;

down)
    # Read back what we installed so `down` needs only the name. An outbound
    # being deleted from the panel must not depend on the panel still knowing
    # which mark it was given.
    if [ -z "$MARK" ] || [ -z "$TABLE" ]; then
        MARK="$(iptables -t mangle -S PREROUTING 2>/dev/null \
                | grep -- "--match-set ${IPSET} src" \
                | grep -oP '(?<=--set-xmark )0x[0-9a-f]+' | head -1 || true)"
        TABLE="$(ip rule show 2>/dev/null | grep -oP "fwmark ${MARK:-__none__} lookup \K\S+" | head -1 || true)"
    fi

    [ -n "${MARK:-}" ] && ip rule del fwmark "$MARK" lookup "${TABLE:-0}" 2>/dev/null || true
    [ -n "${MARK:-}" ] && del mangle PREROUTING -m set --match-set "$IPSET" src -j MARK --set-mark "$MARK"
    del nat POSTROUTING -o "$IFACE" -j MASQUERADE
    del mangle FORWARD -o "$IFACE" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
    iptables -D FORWARD -o "$IFACE" -j ACCEPT 2>/dev/null || true
    iptables -D FORWARD -i "$IFACE" -j ACCEPT 2>/dev/null || true
    [ -n "${TABLE:-}" ] && ip route flush table "$TABLE" 2>/dev/null || true

    systemctl disable --now "wg-quick@${IFACE}" >/dev/null 2>&1 || true
    ip link del "$IFACE" 2>/dev/null || true
    ipset destroy "$IPSET" 2>/dev/null || true
    rm -f "$CONF"

    echo "outbound ${NAME}: down"
    ;;

status)
    echo "outbound ${NAME}"
    echo "  iface  : $(ip -o link show "$IFACE" 2>/dev/null | cut -d: -f2 | xargs || echo 'absent')"
    echo "  wg     : $(wg show "$IFACE" 2>/dev/null | grep -E 'endpoint|handshake|transfer' | tr '\n' ' ' || echo 'down')"
    echo "  ipset  : $(ipset list "$IPSET" -terse 2>/dev/null | tr '\n' ' ' || echo 'absent')"
    echo "  members: $(ipset list "$IPSET" 2>/dev/null | sed -n '/Members:/,$p' | tail -n +2 | wc -l)"
    echo "  rule   : $(ip rule show | grep "lookup ${TABLE:-}" || echo '(absent — added while healthy)')"
    ;;

*)
    echo "usage: outbound-plumb.sh {up|down|status} <name> [options]" >&2
    exit 2
    ;;
esac
