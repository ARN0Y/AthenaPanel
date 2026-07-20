#!/bin/bash
###############################################################################
# /etc/ppp/ip-up.d/vpn-panel
#
# Run by pppd (via /etc/ppp/ip-up -> run-parts) when an L2TP/PPP session comes
# up. Registers the session with the panel and applies tc rate limiting.
#
# pppd args: $1=iface $2=tty $3=speed $4=local-ip $5=remote-ip $6=ipparam
# pppd env : PEERNAME = authenticated username
###############################################################################
set -u

IFACE="$1"
PEERIP="${5:-}"
USERNAME="${PEERNAME:-}"

API="http://127.0.0.1:8000/api/internal"
LOG=/var/log/vpn-panel/ip-up.log
ts() { date '+%Y-%m-%dT%H:%M:%S%z'; }
log() { echo "$(ts) ip-up[$USERNAME/$IFACE] $*" >>"$LOG" 2>/dev/null; }

[ -z "$USERNAME" ] && exit 0
[ -z "$IFACE" ] && exit 0

# pppd writes its PID file as /var/run/<iface>.pid (e.g. /var/run/ppp0.pid)
PID="$(cat "/var/run/${IFACE}.pid" 2>/dev/null || echo 0)"
case "$PID" in (*[!0-9]*) PID=0;; esac

# 1) Register the live session with the panel. The reply carries a verdict:
#    "allowed":false means this account's l2tp_mode does not match the endpoint
#    the client dialled (e.g. an IPsec account on the raw, unencrypted port), so
#    the link must be dropped immediately.
UP_RESP="$(curl -fsS --max-time 3 -X POST "$API/session-up" \
    -H 'Content-Type: application/json' \
    -d "{\"username\":\"$USERNAME\",\"ifname\":\"$IFACE\",\"peer_ip\":\"$PEERIP\",\"pid\":$PID}" \
    2>>"$LOG")" || log "session-up POST failed"
log "session-up -> ${UP_RESP:-<no response>}"

# FAIL OPEN: only a response that explicitly says allowed:false drops the link.
# A panel that is down, slow or returns garbage leaves the session untouched —
# an outage of the panel must never disconnect anyone.
case "$UP_RESP" in
    *'"allowed":false'*|*'"allowed": false'*)
        REASON="$(echo "$UP_RESP" | sed -n 's/.*"reason"[: ]*"\([^"]*\)".*/\1/p')"
        log "REFUSED (${REASON:-l2tp mode mismatch}) — dropping link"
        # Re-read the pid file: at this point pppd has certainly written it, even
        # if it hadn't when we sampled PID above.
        KPID="$PID"
        [ "$KPID" -gt 0 ] 2>/dev/null || KPID="$(cat "/var/run/${IFACE}.pid" 2>/dev/null || echo 0)"
        case "$KPID" in (*[!0-9]*) KPID=0;; esac
        if [ "$KPID" -gt 0 ] 2>/dev/null; then
            kill -TERM "$KPID" 2>>"$LOG" && log "sent SIGTERM to pppd $KPID"
        else
            # No pid file -> leave it to the panel's enforcer (<=1 poll interval).
            log "no pid for $IFACE; enforcer will drop it"
        fi
        exit 0
        ;;
esac

# 2) Fetch rate limits and apply via tc
RESP="$(curl -fsS --max-time 3 "$API/rate/$USERNAME" 2>/dev/null || true)"
DOWN="$(echo "$RESP" | sed -n 's/.*"rate_down_kbps"[: ]*\([0-9]*\).*/\1/p')"
UP="$(echo "$RESP"   | sed -n 's/.*"rate_up_kbps"[: ]*\([0-9]*\).*/\1/p')"
DOWN="${DOWN:-0}"
UP="${UP:-0}"

# Download: egress shaping on the ppp interface (traffic to the client)
if [ "$DOWN" -gt 0 ] 2>/dev/null; then
    tc qdisc del dev "$IFACE" root 2>/dev/null
    tc qdisc add dev "$IFACE" root handle 1: htb default 10 2>>"$LOG" && \
    tc class add dev "$IFACE" parent 1: classid 1:10 htb \
        rate "${DOWN}kbit" ceil "${DOWN}kbit" burst 15k 2>>"$LOG" && \
    log "down shaper ${DOWN}kbit applied" || log "down shaper FAILED"
fi

# Upload: ingress policing on the ppp interface (traffic from the client)
if [ "$UP" -gt 0 ] 2>/dev/null; then
    tc qdisc del dev "$IFACE" ingress 2>/dev/null
    tc qdisc add dev "$IFACE" handle ffff: ingress 2>>"$LOG" && \
    tc filter add dev "$IFACE" parent ffff: protocol ip prio 1 u32 \
        match u32 0 0 police rate "${UP}kbit" burst "${UP}kbit" mtu 1500 drop flowid :1 \
        2>>"$LOG" && log "up policer ${UP}kbit applied" || log "up policer FAILED"
fi

[ "$DOWN" = "0" ] && [ "$UP" = "0" ] && log "unlimited, no shaping"
exit 0
