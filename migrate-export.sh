#!/usr/bin/env bash
###############################################################################
# Run on the CURRENT overseas server. Safely snapshots all panel + VPN state
# into a tarball you scp to the new server. Does NOT disrupt the running server.
#
#     sudo bash migrate-export.sh
###############################################################################
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

OUT=/root/vpn-migration
rm -rf "$OUT"; mkdir -p "$OUT"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

# --- Panel database (online-safe backup; preserves users/admins/usage) ---
if [ -f /var/lib/vpn-panel/vpn.db ]; then
    sqlite3 /var/lib/vpn-panel/vpn.db ".backup '$OUT/vpn.db'"
    log "panel DB exported"
fi

# --- Secrets / config that must stay identical (so clients don't change) --
[ -f /opt/vpn-panel/.env ]        && cp /opt/vpn-panel/.env        "$OUT/panel.env"
[ -f /etc/ppp/chap-secrets ]      && cp /etc/ppp/chap-secrets      "$OUT/chap-secrets"
[ -f /etc/ipsec.secrets ]         && cp /etc/ipsec.secrets         "$OUT/ipsec.secrets"
[ -f /etc/ppp/options.xl2tpd ]    && cp /etc/ppp/options.xl2tpd    "$OUT/options.xl2tpd"
[ -f /etc/xl2tpd/xl2tpd.conf ]    && cp /etc/xl2tpd/xl2tpd.conf    "$OUT/xl2tpd.conf"
[ -f /var/log/vpn-panel/accounting.log ] && cp /var/log/vpn-panel/accounting.log "$OUT/accounting.log"
log "secrets + configs exported"

# --- Backhaul server (binary + server.toml) -------------------------------
BH=""
for d in /root/bk2 /root/bk /opt/backhaul /root/backhaul; do
    if [ -d "$d" ] && ls "$d" 2>/dev/null | grep -qiE 'server\.toml|backhaul'; then BH="$d"; break; fi
done
if [ -n "$BH" ]; then
    cp -r "$BH" "$OUT/backhaul"
    log "backhaul dir copied from $BH"
else
    log "backhaul dir not auto-found — copy it manually (the folder with server.toml + backhaul binary)"
fi
# capture how the backhaul is started (systemd unit, if any)
systemctl list-units --type=service 2>/dev/null | grep -iE 'backhaul|athena|bh-' > "$OUT/backhaul-services.txt" || true
for u in /etc/systemd/system/*backhaul* /etc/systemd/system/*athena*; do
    [ -f "$u" ] && cp "$u" "$OUT/" 2>/dev/null || true
done

# --- Record the current public IP for reference ---------------------------
curl -fsS --max-time 4 https://api.ipify.org > "$OUT/OLD_PUBLIC_IP.txt" 2>/dev/null || hostname -I | awk '{print $1}' > "$OUT/OLD_PUBLIC_IP.txt"

tar czf /root/vpn-migration.tar.gz -C /root vpn-migration
log "DONE -> /root/vpn-migration.tar.gz"
echo
echo "Copy it to the new server, e.g.:"
echo "    scp /root/vpn-migration.tar.gz root@NEW_SERVER_IP:/root/"
echo
echo "Contents:"; ls -la "$OUT"
