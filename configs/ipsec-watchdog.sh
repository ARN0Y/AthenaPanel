#!/bin/bash
# Detect and clear a wedged pluto.
#
# Libreswan's pluto can get stuck spinning on the socket error queue of the
# backhaul interface:
#
#   ERROR: recvmsg(,, MSG_ERRQUEUE) on bh2 failed (noticed before
#   udp_read_packet) (attempt 32): Resource temporarily unavailable (errno 11)
#
# The name of that error is the whole problem: it happens *before* the UDP read.
# pluto burns its event loop retrying a phantom error and never drains the
# socket, so udp/500's receive queue fills to the buffer limit and stays there.
# Every new customer's first IKE packet is then dropped by the kernel, and
# Windows reports "the security layer encountered a processing error during
# initial negotiations". Sessions that already hold an SA keep working, because
# their traffic is ESP on udp/4500 whose queue is still being read — which is
# what makes this look like "some people can connect and some cannot" rather
# than an outage.
#
# On 2026-08-29 this ran for roughly 22 hours before anyone noticed: L2TP fell
# from ~100 new sessions an hour to zero while SSTP carried on. A restart clears
# it in seconds. This checks for it every minute so the next one lasts a minute.
#
# Deliberately NOT triggered on the log flood: pluto can be noisy without being
# wedged. A receive queue that stays full is the symptom that actually matters.
set -u

PORT=500
# The buffer caps at ~1 MB. A quarter of it is far above anything normal
# traffic leaves sitting there, and far below the wedged value.
THRESHOLD=262144
SETTLE=20
# Never restart more often than this, so a fault we cannot fix does not become
# a restart loop that disconnects customers every minute.
COOLDOWN=900
STAMP=/run/athena-ipsec-watchdog.last

recvq() {
    ss -lnup 2>/dev/null | awk -v p=":$PORT " '$5 ~ p {print $2; exit}'
}

q1=$(recvq)
[ -z "${q1:-}" ] && exit 0                 # pluto not listening; not our call
[ "$q1" -lt "$THRESHOLD" ] 2>/dev/null && exit 0

# A burst can fill the queue for an instant. A wedge keeps it full, so look
# again before doing anything disruptive.
sleep "$SETTLE"
q2=$(recvq)
[ -z "${q2:-}" ] && exit 0
[ "$q2" -lt "$THRESHOLD" ] 2>/dev/null && exit 0

now=$(date +%s)
if [ -r "$STAMP" ]; then
    last=$(cat "$STAMP" 2>/dev/null || echo 0)
    if [ $((now - last)) -lt "$COOLDOWN" ]; then
        logger -t athena-ipsec-watchdog \
            "udp/$PORT still wedged (Recv-Q=$q2) but restarted $((now - last))s ago; holding off"
        exit 0
    fi
fi

echo "$now" > "$STAMP"
logger -t athena-ipsec-watchdog \
    "udp/$PORT receive queue stuck at $q1 -> $q2 bytes over ${SETTLE}s; pluto is not draining it, restarting libreswan"
systemctl restart libreswan
sleep 15
q3=$(recvq)
logger -t athena-ipsec-watchdog "after restart: Recv-Q=${q3:-unknown}, unit=$(systemctl is-active libreswan)"
