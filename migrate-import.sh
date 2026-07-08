#!/usr/bin/env bash
###############################################################################
# EASY-BUTTON restore on the NEW overseas server.
#
# Run AFTER the stack is installed once:
#     sudo bash install.sh && sudo bash revert-to-xl2tpd.sh
#
# Then this single script restores EVERYTHING:
#   * vpn.db        -> all customers / resellers / quota / usage
#   * .env          -> same PSK + JWT (clients & admin logins don't change)
#   * chap-secrets / ipsec.secrets / xl2tpd configs
#   * the FULL iptables data path (NAT MASQUERADE + FORWARD + MSS clamp)
#   * Libreswan left = 10.50.50.1  (backhaul local addr)
#
# It finds the files automatically from EITHER:
#   - /root/vpn-migration.tar.gz  (made by migrate-export.sh), OR
#   - files dropped next to this script:  ./vpn.db  ./.env
#
#     sudo bash migrate-import.sh
#
# You handle the backhaul tunnel + 10.50.50.1 yourself.
###############################################################################
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

[ -d /opt/vpn-panel ] || die "/opt/vpn-panel missing — run: bash install.sh && bash revert-to-xl2tpd.sh first"

# --- find the source files ------------------------------------------------
SRC=""
if [ -f /root/vpn-migration.tar.gz ]; then
    tar xzf /root/vpn-migration.tar.gz -C /root && SRC=/root/vpn-migration
    log "using tarball payload: $SRC"
fi
# helper: pick first existing path (always exits 0 so `set -e` won't kill us
# when an OPTIONAL file is simply absent)
pick() { for f in "$@"; do if [ -f "$f" ]; then echo "$f"; return 0; fi; done; return 0; }

DB=$(pick "$SRC/vpn.db" "$REPO_DIR/vpn.db")
ENVF=$(pick "$SRC/panel.env" "$REPO_DIR/.env")
CHAP=$(pick "$SRC/chap-secrets")
ISEC=$(pick "$SRC/ipsec.secrets")
OPTS=$(pick "$SRC/options.xl2tpd")
XLC=$(pick "$SRC/xl2tpd.conf")
ACCT=$(pick "$SRC/accounting.log")

[ -n "$DB" ] || die "vpn.db not found (put it at $REPO_DIR/vpn.db or use the tarball)"

# =========================================================================
log "stopping vpn-panel (xl2tpd / sessions stay up)"
systemctl stop vpn-panel 2>/dev/null || true

# --- customers ------------------------------------------------------------
install -d -m 0755 /var/lib/vpn-panel
rm -f /var/lib/vpn-panel/vpn.db-wal /var/lib/vpn-panel/vpn.db-shm
cp "$DB" /var/lib/vpn-panel/vpn.db
N=$(python3 - <<PY 2>/dev/null || echo '?'
import sqlite3;print(sqlite3.connect("/var/lib/vpn-panel/vpn.db").execute("select count(*) from users").fetchone()[0])
PY
)
log "vpn.db restored ($N users)"

# --- env (PSK + JWT) ------------------------------------------------------
if [ -n "$ENVF" ]; then
    cp "$ENVF" /opt/vpn-panel/.env; chmod 600 /opt/vpn-panel/.env
    grep -q '^BACKHAUL_ADDR=' /opt/vpn-panel/.env || echo 'BACKHAUL_ADDR=10.50.50.1' >> /opt/vpn-panel/.env
    log ".env restored (PSK/JWT preserved)"
else
    warn ".env not found — make sure VPN_PSK matches the old server or clients break!"
fi

# --- secrets / configs ----------------------------------------------------
[ -n "$CHAP" ] && { cp "$CHAP" /etc/ppp/chap-secrets; chmod 600 /etc/ppp/chap-secrets; log "chap-secrets restored"; }
[ -n "$ISEC" ] && { cp "$ISEC" /etc/ipsec.secrets; chmod 600 /etc/ipsec.secrets; log "ipsec.secrets restored"; }
[ -n "$OPTS" ] && { cp "$OPTS" /etc/ppp/options.xl2tpd; log "options.xl2tpd restored"; }
[ -n "$XLC" ]  && { install -d /etc/xl2tpd; cp "$XLC" /etc/xl2tpd/xl2tpd.conf; log "xl2tpd.conf restored"; }
[ -n "$ACCT" ] && { install -d /var/log/vpn-panel; cp "$ACCT" /var/log/vpn-panel/accounting.log; log "accounting history restored"; }

# --- Libreswan local endpoint = backhaul TUN ------------------------------
if [ -f /etc/ipsec.conf ] && grep -q '^  left=%defaultroute' /etc/ipsec.conf; then
    sed -i 's/^  left=%defaultroute/  left=10.50.50.1/' /etc/ipsec.conf
    log "ipsec.conf left = 10.50.50.1"
fi

# =========================================================================
# IPTABLES — the data path that makes client traffic reach the internet.
#   WAN auto-detected from the default route. L2TP client subnet = 192.168.0.0/16
#   (the only private /16 used by the ppp pool; docker uses 172.x and is untouched).
# =========================================================================
WAN=$(ip route get 8.8.8.8 2>/dev/null | grep -oP 'dev \K\S+' | head -1 || true)
WAN=${WAN:-eth0}
log "configuring iptables (WAN=$WAN, client pool 192.168.0.0/16)"

sysctl -w net.ipv4.ip_forward=1 >/dev/null
sed -i '/net.ipv4.ip_forward/d' /etc/sysctl.conf 2>/dev/null || true
echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf

addrule() {  # add only if not already present
    local tbl="$1"; shift
    iptables -t "$tbl" -C "$@" 2>/dev/null || iptables -t "$tbl" -A "$@"
}

# NAT: masquerade L2TP clients out to the internet
addrule nat POSTROUTING -s 192.168.0.0/16 -o "$WAN" -j MASQUERADE

# FORWARD: allow client <-> internet (table policy may be DROP)
addrule filter FORWARD -i ppp+ -o "$WAN" -j ACCEPT
addrule filter FORWARD -i "$WAN" -o ppp+ -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
addrule filter FORWARD -i ppp+ -o ppp+ -j ACCEPT

# MANGLE: clamp TCP MSS to fit the doubly-encapsulated backhaul path (download fix)
for spec in "-i ppp+" "-o ppp+"; do
    # shellcheck disable=SC2086
    iptables -t mangle -C FORWARD $spec -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1240 2>/dev/null \
        || iptables -t mangle -A FORWARD $spec -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss 1240
done

# conntrack liberal (helps asymmetric/encapsulated TCP)
sysctl -w net.netfilter.nf_conntrack_tcp_be_liberal=1 >/dev/null 2>&1 || true

# persist
apt-get install -y iptables-persistent >/dev/null 2>&1 || true
command -v netfilter-persistent >/dev/null 2>&1 && netfilter-persistent save >/dev/null 2>&1 || true
log "iptables applied + persisted"

# =========================================================================
log "restarting ipsec / xl2tpd / vpn-panel"
( ipsec restart 2>/dev/null || systemctl restart strongswan-starter 2>/dev/null || systemctl restart ipsec 2>/dev/null ) || true
systemctl restart xl2tpd 2>/dev/null || true
systemctl start vpn-panel
sleep 3

echo
log "VERIFY:"
ss -ulnp | grep -q 1701 && printf '   \033[1;32m✓\033[0m xl2tpd on udp/1701\n' || warn "nothing on udp/1701!"
for u in ipsec strongswan-starter xl2tpd vpn-panel nginx; do
    systemctl is-active --quiet "$u" 2>/dev/null && printf '   \033[1;32m✓\033[0m %s\n' "$u" || true
done
printf '   chap-secrets users: %s\n' "$(grep -c '^"' /etc/ppp/chap-secrets 2>/dev/null || echo 0)"
curl -fsS --max-time 4 http://127.0.0.1:8000/api/ping >/dev/null 2>&1 \
    && printf '   \033[1;32m✓\033[0m panel API up\n' || warn "panel API not answering (journalctl -u vpn-panel -n 40)"

cat <<EOF

============================================================================
  Done. Customers + resellers + usage restored; PSK/JWT identical; the full
  NAT/FORWARD/MSS data path is in place.

  YOUR part: bring the backhaul tunnel up to THIS server, TUN = 10.50.50.1.
  Then clients reconnect automatically — nothing changes on their devices.
============================================================================
EOF
