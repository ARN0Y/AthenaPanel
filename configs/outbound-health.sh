#!/usr/bin/env bash
#
# outbound-health.sh — keep only healthy egress locations in the routing policy.
#
# Runs from a systemd timer every 30s. For each ob-* tunnel:
#
#   healthy   -> make sure its route and `ip rule` are in place, so its users
#                egress there
#   unhealthy -> remove the rule, so its users' marked packets fall through to
#                the main table and go out directly
#
# That is the whole failure model, and it is the right way round: a customer
# whose egress location died gets a different IP, not a dead connection. Nothing
# touches the ipset, so when the location returns its users go back to it on the
# next tick with no bookkeeping.
#
# It rebuilds the ROUTE as well as the rule, which is not optional. wg-quick
# runs with Table = off, so the default route in the location's own table is
# ours alone — and the kernel deletes it the moment the interface goes down.
# Restoring only the rule would point it at an empty table, and the location
# would be permanently dead despite a perfectly healthy tunnel. The restart
# below is itself an interface bounce, so this script would otherwise break
# exactly the locations it was trying to repair.
#
# mark/table/priority come from the conf file rather than from the live routing
# state, because after a bounce the live state is precisely what is missing.
#
# After FAIL_THRESHOLD consecutive failures the tunnel is restarted, on the
# theory that by then it is not a blip. Restarting sooner would tear down a
# tunnel that is merely having a bad ten seconds.
#
# This is the same design as warp-health.sh, which stays separate: WARP's health
# is "does Cloudflare say warp=on", which is not a question that generalises.

set -u

FAIL_THRESHOLD=3
HANDSHAKE_MAX_AGE=180   # wireguard rekeys about every 120s
STATE_DIR=/run/athena-outbound
mkdir -p "$STATE_DIR"

for CONF in /etc/wireguard/ob-*.conf; do
    [ -e "$CONF" ] || continue
    IFACE="$(basename "$CONF" .conf)"
    NAME="${IFACE#ob-}"

    MARK="$(sed -n 's/^# mark: //p' "$CONF" | head -1)"
    TABLE="$(sed -n 's/^# table: //p' "$CONF" | head -1)"
    PRIO="$(sed -n 's/^# priority: //p' "$CONF" | head -1)"
    [ -n "$MARK" ] && [ -n "$TABLE" ] && [ -n "$PRIO" ] || continue

    STATE="$STATE_DIR/$NAME.fails"

    # Reachability, not internet-wideness: a recent handshake means the far end
    # is answering. Curling out through the tunnel would also be true, but it
    # costs a request per location every 30s and it fails for reasons that have
    # nothing to do with the tunnel.
    healthy=0
    if ip link show "$IFACE" >/dev/null 2>&1; then
        HS="$(wg show "$IFACE" latest-handshakes 2>/dev/null | awk '{print $2}' | sort -rn | head -1)"
        NOW="$(date +%s)"
        if [ -n "$HS" ] && [ "$HS" -gt 0 ] && [ $((NOW - HS)) -lt "$HANDSHAKE_MAX_AGE" ]; then
            healthy=1
        fi
    fi

    if [ "$healthy" = "1" ]; then
        # Route first: a rule pointing at an empty table blackholes, which is
        # the one outcome this script exists to prevent.
        ip route show table "$TABLE" 2>/dev/null | grep -q "default dev $IFACE" \
            || ip route replace default dev "$IFACE" table "$TABLE"
        # The mangle rule matches on the ipset, so it survives a bounce — but
        # assert it anyway; it is cheap and it is what actually marks packets.
        iptables -t mangle -C PREROUTING -m set --match-set "ob-${NAME}" src -j MARK --set-mark "$MARK" 2>/dev/null \
            || iptables -t mangle -A PREROUTING -m set --match-set "ob-${NAME}" src -j MARK --set-mark "$MARK"
        ip rule show | grep -q "fwmark $MARK lookup $TABLE" \
            || { ip rule add fwmark "$MARK" lookup "$TABLE" priority "$PRIO"
                 logger -t outbound-health "$NAME healthy - routing restored"; }
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
