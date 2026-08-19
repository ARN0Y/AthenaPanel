#!/usr/bin/env bash
#
# badbox-report.sh — turn "something behind our IP talked to a BADBOX C2" into
# "THIS account did", which is the only form of the fact anyone can act on.
#
# HOW EACH PROTOCOL IS IDENTIFIED
#
#   L2TP/IPsec, L2TP raw, SSTP   The kernel FORWARDs these, so the packet still
#                                carries the customer's pool address (192.168.x).
#                                sessions.peer_ip maps that to a username.
#
#   WireGuard                    Same, with the peer's 10.10.x address, mapped
#                                through wg_peers.address.
#
#   Xray (PasarGuard / x-ui)     NOT identifiable here, and it is worth being
#                                precise about why: Xray terminates the customer
#                                and opens its own connection, so the packet
#                                leaves OUTPUT with this server's address as the
#                                source. Every Xray customer looks identical to
#                                the kernel. Only Xray's own access log knows
#                                which UUID/email was on the other side, and it
#                                is off by default on both instances here.
#                                A BADBOX-OUT hit therefore means "an Xray
#                                customer is infected" and nothing narrower
#                                until that log is switched on.
#
# Reads the journal, not a log file of its own. The first version shipped an
# rsyslog rule writing /var/log/badbox.log; the rule matched but omfile never
# created the file, while journald plainly had every line. One source of truth
# that already exists, is already size-capped and is already rate-limited beats
# a second one that can fail quietly.
#
# Safe to run at any time; reads only.

set -uo pipefail

SINCE=${1:-24 hours ago}
PSQL="sudo -u postgres psql -d vpnpanel -At -F| -c"

hr() { printf '%s\n' "------------------------------------------------------------"; }

echo "BADBOX 2.0 detections since: $SINCE"
hr

LINES=$(journalctl --since "$SINCE" --no-pager 2>/dev/null | grep -F 'BADBOX' || true)

if [ -z "$LINES" ]; then
    echo "No detections in this window."
    echo
    echo "That is the expected state. The rules are armed and the blocklist is"
    echo "live; this fills in only when an infected device actually beacons."
    hr
    echo -n "  blocklist: "; ipset list badbox_c2 -terse 2>/dev/null | grep -oP 'Number of entries: \K[0-9]+' | tr -d '
'
    echo -n " v4 + "; ipset list badbox_c6 -terse 2>/dev/null | grep -oP 'Number of entries: \K[0-9]+' | tr -d '
'
    echo " v6 addresses"
    echo -n "  refreshed: "; cat /var/lib/badbox/last-refresh 2>/dev/null || echo never
    echo -n "  blocked so far: "
    iptables -L BADBOX_FWD -nv 2>/dev/null | awk '/DROP/{print $1}' | tr -d '
'
    echo -n " customer packets, "
    iptables -L BADBOX_OUT -nv 2>/dev/null | awk '/DROP/{print $1}' | tr -d '
'
    echo " via Xray"
    exit 0
fi

echo "$LINES" | grep -oP 'SRC=\K[0-9a-f.:]+' | sort | uniq -c | sort -rn > /tmp/badbox_src.$$

echo "Sources seen, most active first:"
echo
printf '  %-8s %-18s %-22s %s\n' HITS ADDRESS ACCOUNT HOW
while read -r hits src; do
    who=""; how=""
    case "$src" in
        192.168.4[0-9].*|192.168.[0-9]*.*)
            who=$($PSQL "SELECT username FROM sessions WHERE peer_ip='$src' LIMIT 1;" 2>/dev/null | head -1)
            [ -z "$who" ] && who=$($PSQL "SELECT username FROM accounting WHERE ifname IN (SELECT ifname FROM sessions WHERE peer_ip='$src') ORDER BY stopped_at DESC LIMIT 1;" 2>/dev/null | head -1)
            how="L2TP/SSTP  (live session -> peer_ip)"
            ;;
        10.10.*)
            who=$($PSQL "SELECT u.username FROM wg_peers p JOIN users u ON u.id=p.user_id WHERE p.address='$src' LIMIT 1;" 2>/dev/null | head -1)
            how="WireGuard  (wg_peers.address)"
            ;;
        91.98.237.167|::ffff:91.98.237.167|2a01:04f8:0c0c:fc8f*|2a01:4f8:c0c:fc8f*)
            # Our own address as the source means the connection was opened BY
            # this host - which here only ever means Xray relaying for someone.
            who="<an Xray customer>"
            how="Xray       (see the note below)"
            ;;
        *)
            who="<unmapped>"; how="source outside the known pools"
            ;;
    esac
    [ -z "$who" ] && who="<no longer connected>"
    printf '  %-8s %-18s %-22s %s\n' "$hits" "$src" "$who" "$how"
done < /tmp/badbox_src.$$
rm -f /tmp/badbox_src.$$

echo
hr
echo "Destinations they were reaching:"
echo "$LINES" | grep -oP 'DST=\K[0-9a-f.:]+' | sort | uniq -c | sort -rn | head -10 | sed 's/^/  /'

echo
hr
echo "Blocklist state:"
for s in badbox_c2 badbox_c6; do
    n=$(ipset list "$s" -terse 2>/dev/null | grep -oP 'Number of entries: \K[0-9]+')
    printf '  %-12s %s addresses\n' "$s" "${n:-absent}"
done
echo -n "  last refreshed: "; cat /var/lib/badbox/last-refresh 2>/dev/null || echo never
echo -n "  rules armed:    "; iptables -S | grep -c badbox_c2
echo
echo "  Every hit above was DROPPED as well as logged, so the device could not"
echo "  reach its controller. The infection is still on the customer's device."
