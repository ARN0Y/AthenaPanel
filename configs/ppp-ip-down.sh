#!/bin/bash
###############################################################################
# /etc/ppp/ip-down.d/vpn-panel
#
# Run by pppd when an L2TP/PPP session ends. Reports final byte counters to the
# panel (which commits them to the user's quota + writes the audit CSV).
#
# pppd args: $1=iface ...
# pppd env : PEERNAME, CONNECT_TIME, BYTES_SENT (to peer / download),
#            BYTES_RCVD (from peer / upload)
###############################################################################
set -u

IFACE="$1"
USERNAME="${PEERNAME:-}"
SENT="${BYTES_SENT:-0}"      # to client   -> download (out_octets)
RCVD="${BYTES_RCVD:-0}"      # from client -> upload   (in_octets)
DURATION="${CONNECT_TIME:-0}"

API="http://127.0.0.1:8000/api/internal"
LOG=/var/log/vpn-panel/ip-up.log

# Clean up any tc qdiscs (interface is usually already gone -> harmless)
tc qdisc del dev "$IFACE" root 2>/dev/null
tc qdisc del dev "$IFACE" ingress 2>/dev/null

[ -z "$USERNAME" ] && exit 0

curl -fsS --max-time 3 -X POST "$API/session-down" \
    -H 'Content-Type: application/json' \
    -d "{\"username\":\"$USERNAME\",\"ifname\":\"$IFACE\",\"in_octets\":$RCVD,\"out_octets\":$SENT,\"session_time\":$DURATION}" \
    >>"$LOG" 2>&1 || echo "$(date) ip-down session-down POST failed for $USERNAME" >>"$LOG"

exit 0
