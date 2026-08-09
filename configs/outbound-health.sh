#!/usr/bin/env bash
#
# outbound-health.sh — keep only healthy egress locations in the routing policy.
#
# Runs from a systemd timer every 30s. For each ob-* tunnel:
#
#   healthy   -> make sure its `ip rule` is present, so its users egress there
#   unhealthy -> remove the rule, so its users' marked packets fall through to
#                the main table and go out directly
#
# That is the whole failure model, and it is the right way round: a customer
# whose egress location died gets a different IP, not a dead connection. Nothing
# needs to touch the ipset, so when the location comes back its users return to
# it on the next tick with no bookkeeping.
#
# After FAIL_THRESHOLD consecutive failures the tunnel is restarted, on the
# theory that by then it is not a blip. Restarting sooner would tear down a
# tunnel that is merely having a bad ten seconds.
#
# This is the same design as warp-health.sh, which stays separate: WARP's health
# is "does Cloudflare say warp=on", which is not a question that generalises.

set -u

FAIL_THRESHOLD=3
STATE_DIR=/run/athena-outbound
mkdir -p "$STATE_DIR"

for CONF in /etc/wireguard/ob-*.conf; do
    [ -e "$CONF" ] || continue
    IFACE="$(basename "$CONF" .conf)"
    NAME="${IFACE#ob-}"

    ip link show "$IFACE" >/dev/null 2>&1 || continue

    MARK="$(iptables -t mangle -S PREROUTING 2>/dev/null \
            | grep -- "--match-set ob-${NAME} src" \
            | grep -oP '(?<=--set-xmark )0x[0-9a-f]+' | head -1)"
    [ -n "$MARK" ] || continue
    TABLE="$(ip route show table all 2>/dev/null | grep -oP "default dev ${IFACE} table \K\S+" | head -1)"
    [ -n "$TABLE" ] || continue
    PRIO="$(sed -n 's/^# priority: //p' "$CONF" 2>/dev/null)"
    [ -n "$PRIO" ] || PRIO=1010

    STATE="$STATE_DIR/$NAME.fails"

    # Reachability, not internet-wideness: a handshake younger than three
    # minutes means the far end is answering. Curling out through the tunnel
    # would also be true, but it costs a request per location per 30s and it
    # fails for reasons that have nothing to do with the tunnel.
    HS="$(wg show "$IFACE" latest-handshakes 2>/dev/null | awk '{print $2}' | sort -rn | head -1)"
    NOW="$(date +%s)"
    if [ -n "$HS" ] && [ "$HS" -gt 0 ] && [ $((NOW - HS)) -lt 180 ]; then
        healthy=1
    else
        healthy=0
    fi

    if [ "$healthy" = "1" ]; then
        ip rule show | grep -q "fwmark $MARK lookup $TABLE" \
            || ip rule add fwmark "$MARK" lookup "$TABLE" priority "$PRIO"
        echo 0 > "$STATE"
    else
        if ip rule show | grep -q "fwmark $MARK lookup $TABLE"; then
            ip rule del fwmark "$MARK" lookup "$TABLE"
            logger -t outbound-health "$NAME unhealthy - users fall back to direct"
        fi
        fails=$(( $(cat "$STATE" 2>/dev/null || echo 0) + 1 ))
        echo "$fails" > "$STATE"
        if [ "$fails" -ge "$FAIL_THRESHOLD" ]; then
            logger -t outbound-health "$NAME unhealthy for $fails checks - restarting wg-quick@$IFACE"
            systemctl restart "wg-quick@$IFACE" || true
            echo 0 > "$STATE"
        fi
    fi
done
