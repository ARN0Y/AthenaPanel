#!/bin/bash
# Safety net for a wedged pluto.
#
# The underlying fault is fixed at the source in ipsec.conf
# (`ike-socket-errqueue=no`, plus an 8 MB `ike-socket-bufsize`): pluto used to
# latch on a POLLERR whose error queue was empty, spin on recvmsg/log forever,
# and never read udp/500 again. Every new customer's first IKE packet was then
# dropped, while anyone already holding an SA carried on over udp/4500 — which
# is why it read as "some people can connect" rather than an outage.
#
# This stays because "the receive queue stopped draining" is worth catching
# whatever the reason, and because the fix above is a mitigation for a daemon
# bug rather than a repair of it.
#
# A NOTE ON THE PARSER, because it is what made the first version useless: this
# ran every minute for three days and never once fired, because it read the
# wrong `ss` column and silently treated "cannot tell" as "healthy". Column
# layout is found by locating the field that ends in :<port> — Recv-Q is two
# fields to its left — rather than assuming a fixed index, and a parse that
# yields nothing now says so in the log instead of exiting quietly.
set -u

PORT=500
# The buffer is 16 MB now, so a queue sitting above 4 MB is far outside normal
# and far below a wedge holding the whole buffer.
THRESHOLD=4194304
SETTLE=20
# Never restart more often than this, so a fault this cannot fix does not turn
# into a restart loop that disconnects customers every minute.
COOLDOWN=900
STAMP=/run/athena-ipsec-watchdog.last
TAG=athena-ipsec-watchdog

# Recv-Q for the listening socket on $PORT.
# `ss -lnup` prints:  [Netid] State Recv-Q Send-Q Local:Port Peer:Port [Process]
# and whether Netid appears varies, so anchor on the local address instead.
recvq() {
    ss -lnup 2>/dev/null | awk -v port=":$PORT" '
        { for (i = 3; i <= NF; i++)
            if ($i ~ port "$") { print $(i - 2); exit } }'
}

q1=$(recvq)

if [ -z "${q1:-}" ]; then
    # Either pluto is not listening or the parse failed. Both are worth saying
    # out loud; neither is "everything is fine".
    if pidof pluto >/dev/null 2>&1; then
        logger -t "$TAG" "pluto is running but udp/$PORT was not found in ss output — cannot check the queue"
    fi
    exit 0
fi

case "$q1" in *[!0-9]*) logger -t "$TAG" "unparsable Recv-Q '$q1' for udp/$PORT"; exit 0 ;; esac
[ "$q1" -lt "$THRESHOLD" ] && exit 0

# A burst can fill the queue for an instant. A wedge keeps it full, so look
# again before doing anything disruptive.
sleep "$SETTLE"
q2=$(recvq)
[ -z "${q2:-}" ] && exit 0
case "$q2" in *[!0-9]*) exit 0 ;; esac
[ "$q2" -lt "$THRESHOLD" ] && exit 0

now=$(date +%s)
if [ -r "$STAMP" ]; then
    last=$(cat "$STAMP" 2>/dev/null || echo 0)
    case "$last" in *[!0-9]*) last=0 ;; esac
    if [ $((now - last)) -lt "$COOLDOWN" ]; then
        logger -t "$TAG" "udp/$PORT still wedged (Recv-Q=$q2) but restarted $((now - last))s ago; holding off"
        exit 0
    fi
fi

echo "$now" > "$STAMP"
logger -t "$TAG" "udp/$PORT receive queue stuck at $q1 -> $q2 bytes over ${SETTLE}s; pluto is not draining it, restarting libreswan"
systemctl restart libreswan
sleep 15
logger -t "$TAG" "after restart: Recv-Q=$(recvq), unit=$(systemctl is-active libreswan)"
