#!/usr/bin/env bash
#
# badbox-refresh.sh — resolve the BADBOX 2.0 C2 domains and keep the kernel's
# blocklist in step with them.
#
# WHY THIS EXISTS
#   CERT-Bund reported this server's IP for android.badbox2 on 2026-08-17. The
#   server is Linux and cannot run Android malware; the connection came from a
#   CUSTOMER'S infected device, NATed out of our address. So the job here is two
#   things at once: stop the traffic, and work out WHOSE device it is.
#
#   Blocking one domain achieves nothing — the family runs ~108 of them, with
#   deliberate near-misses (ycxrl / yxcrl / pcxrl / ycxad). Hence a set, and
#   hence re-resolving on a timer, because C2 addresses rotate.
#
# WHAT IT INSTALLS
#   ipset badbox_c2 / badbox_c6   the resolved addresses
#   FORWARD  LOG + DROP           customer traffic. Source address identifies
#                                 the account (see badbox-report.sh).
#   OUTPUT   LOG + DROP           traffic this host originates, which for us
#                                 means Xray proxying on a customer's behalf.
#                                 The source is our own IP, so the account can
#                                 only be named from Xray's own log.
#
# THE LOG RULES ARE RATE-LIMITED, DELIBERATELY
#   On 2026-08-18 an unrelated daemon wrote 267 GB to auth.log in two days,
#   filled the disk, and took the database down with it. A LOG target on a
#   forwarding path is exactly that shape of risk. 30/minute is far more than
#   enough to notice an infection and nowhere near enough to hurt.

set -uo pipefail

DOMAINS=${DOMAINS:-/etc/badbox/domains.txt}
SET4=badbox_c2
SET6=badbox_c6
STATE=/var/lib/badbox
QUIET=${QUIET:-0}

log() { [ "$QUIET" = "1" ] || printf '%s\n' "$*"; }
die() { printf 'badbox-refresh: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "must run as root"
[ -f "$DOMAINS" ] || die "no domain list at $DOMAINS"
command -v ipset >/dev/null || die "ipset is not installed"

mkdir -p "$STATE"

# Addresses that must never enter the blocklist even if a C2 domain resolves
# there. A shared CDN address would take unrelated sites down with it for every
# customer, which is a far worse outcome than missing one C2.
is_excluded() {
    case "$1" in
        0.*|10.*|127.*|169.254.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*|192.168.*|224.*|255.*) return 0 ;;
        # Cloudflare — C2 behind it is real, but so is everything else there.
        104.1[6-9].*|104.2[0-7].*|172.6[4-9].*|172.7[0-1].*|162.15[89].*|173.245.4[89].*|103.21.244.*|103.22.200.*|103.31.4.*|141.101.*|108.162.*|190.93.*|188.114.*|198.41.*|172.67.*) return 0 ;;
    esac
    return 1
}

ipset create "$SET4" hash:ip -exist
ipset create "$SET6" hash:ip family inet6 -exist
ipset create "${SET4}_new" hash:ip -exist
ipset create "${SET6}_new" hash:ip family inet6 -exist
ipset flush "${SET4}_new"
ipset flush "${SET6}_new"

n4=0; n6=0; nres=0; nskip=0
while read -r d; do
    d="${d%%#*}"; d="$(echo "$d" | tr -d '[:space:]')"
    [ -z "$d" ] && continue
    nres=$((nres + 1))
    for ip in $(getent ahostsv4 "$d" 2>/dev/null | awk '{print $1}' | sort -u); do
        if is_excluded "$ip"; then nskip=$((nskip + 1)); log "  skip  $ip ($d) - shared or reserved"; continue; fi
        ipset add "${SET4}_new" "$ip" -exist && n4=$((n4 + 1))
    done
    for ip in $(getent ahostsv6 "$d" 2>/dev/null | awk '{print $1}' | grep ':' | sort -u); do
        case "$ip" in ::1|fe80:*|fc00:*|fd00:*) nskip=$((nskip + 1)); continue ;; esac
        ipset add "${SET6}_new" "$ip" -exist && n6=$((n6 + 1))
    done
done < "$DOMAINS"

# Addresses seen in an actual CERT-Bund report for this server. Kept even when
# the domain behind them stops resolving, which is exactly what happens once a
# domain is seized or sinkholed.
for ip in 85.17.70.16; do
    is_excluded "$ip" || { ipset add "${SET4}_new" "$ip" -exist && n4=$((n4 + 1)); }
done

# Swap atomically: at no point is the live set empty or half-built.
ipset swap "${SET4}_new" "$SET4" 2>/dev/null || true
ipset swap "${SET6}_new" "$SET6" 2>/dev/null || true
ipset destroy "${SET4}_new" 2>/dev/null || true
ipset destroy "${SET6}_new" 2>/dev/null || true

# Order matters and iptables -I prepends, so building the pair with two
# inserts puts DROP in front of LOG and nothing is ever logged — which is
# exactly the failure this had on first deploy: 14 packets dropped, an empty
# log, and no way to name the customer. A dedicated chain makes the order
# explicit and survives being rebuilt.
build_chain() {
    local ipt=$1 chain=$2 prefix=$3
    $ipt -N "$chain" 2>/dev/null || true
    $ipt -F "$chain"
    $ipt -A "$chain" -m limit --limit 30/min --limit-burst 10         -j LOG --log-prefix "$prefix " --log-level 4
    $ipt -A "$chain" -j DROP
}

for spec in "FORWARD BADBOX_FWD" "OUTPUT BADBOX_OUT"; do
    set -- $spec
    parent=$1; chain=$2
    build_chain iptables "$chain" "${chain/BADBOX_/BADBOX-}"
    iptables -C "$parent" -m set --match-set "$SET4" dst -j "$chain" 2>/dev/null         || iptables -I "$parent" -m set --match-set "$SET4" dst -j "$chain"

    build_chain ip6tables "${chain}6" "${chain/BADBOX_/BADBOX6-}" 2>/dev/null || true
    ip6tables -C "$parent" -m set --match-set "$SET6" dst -j "${chain}6" 2>/dev/null         || ip6tables -I "$parent" -m set --match-set "$SET6" dst -j "${chain}6" 2>/dev/null || true
done

date -u +%FT%TZ > "$STATE/last-refresh"
printf '%s %s\n' "$(date -u +%FT%TZ)" "v4=$n4 v6=$n6 domains=$nres skipped=$nskip" >> "$STATE/history"
tail -200 "$STATE/history" > "$STATE/history.tmp" && mv "$STATE/history.tmp" "$STATE/history"

log "badbox: $nres domains -> $(ipset list $SET4 -terse | grep -oP 'Number of entries: \K[0-9]+') v4 + $(ipset list $SET6 -terse | grep -oP 'Number of entries: \K[0-9]+') v6 addresses blocked ($nskip skipped)"
