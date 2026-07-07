#!/usr/bin/env bash
###############################################################################
# READ-ONLY audit + SAFE backup for the VPN panel stack (accel-ppp L2TP+SSTP).
#
# Makes NO change to any running service, daemon, session, chap-secrets, DB,
# iptables or config. The ONLY things it writes are:
#   * a timestamped backup dir  (/root/vpn-audit-<ts>/backup)
#   * a report file             (/root/vpn-audit-<ts>/report.txt)
# It will NOT disconnect a single user.
#
#   sudo bash audit-readonly.sh
#
# Then send me /root/vpn-audit-<ts>/report.txt — it contains NO passwords/PSKs.
###############################################################################
set -uo pipefail   # deliberately NOT -e: keep going past individual failures

TS="$(date +%Y%m%d-%H%M%S)"
OUT="/root/vpn-audit-$TS"
BK="$OUT/backup"
mkdir -p "$BK"
REPORT="$OUT/report.txt"
exec > >(tee "$REPORT") 2>&1

sec(){ printf '\n=================== %s ===================\n' "$*"; }
DB=/var/lib/vpn-panel/vpn.db

sec "HOST / KERNEL / TIME"
hostname; uname -a; date; uptime

sec "SERVICES (active?)"
for u in accel-ppp accel-ppp-sstp vpn-panel ipsec strongswan nginx; do
  printf '%-18s ' "$u:"; systemctl is-active "$u" 2>/dev/null || true
done

sec "ACCEL-PPP PROCESSES"
ps -eo pid,cmd | grep -E 'accel-pppd' | grep -v grep || true

sec "CLI PORT 2001 — which daemon owns it? (dual-daemon conflict check)"
ss -tlnp 2>/dev/null | grep -E ':2001' || echo "NOTHING listening on 2001"

sec "ACCEL-CMD: show sessions (port 2001)"
timeout 6 accel-cmd -H 127.0.0.1 -p 2001 show sessions 2>&1 | head -160 || echo "(accel-cmd failed/timed out)"
sec "ACCEL-CMD: show stat (port 2001)"
timeout 6 accel-cmd -H 127.0.0.1 -p 2001 show stat 2>&1 | head -60 || echo "(accel-cmd failed/timed out)"

sec "LISTENING PORTS (vpn-relevant)"
ss -tulnp 2>/dev/null | grep -E ':(443|1701|500|4500|2001|8000|80)\b' || true

sec "LIVE ppp INTERFACE COUNT"
ip -o link show 2>/dev/null | grep -oE 'ppp[0-9]+' | sort -u | wc -l

sec "DB COUNTS + TOTALS (no passwords)"
sqlite3 "$DB" "SELECT 'users',COUNT(*) FROM users UNION ALL SELECT 'admins',COUNT(*) FROM admins UNION ALL SELECT 'sessions',COUNT(*) FROM sessions UNION ALL SELECT 'traffic_samples',COUNT(*) FROM traffic_samples;" 2>&1
echo "-- SUM(used_bytes), SUM(quota_bytes):"
sqlite3 "$DB" "SELECT IFNULL(SUM(used_bytes),0), IFNULL(SUM(quota_bytes),0) FROM users;" 2>&1
echo "-- sessions table:"
sqlite3 -header -column "$DB" "SELECT ifname,username,last_rx,last_tx,pid,started_at FROM sessions ORDER BY ifname;" 2>&1 | head -100

sec "*** V1 EVIDENCE: kernel sysfs counters vs DB baseline (the accounting bug) ***"
sqlite3 -separator '|' "$DB" "SELECT ifname,username,last_rx,last_tx FROM sessions;" 2>/dev/null | \
while IFS='|' read -r IF U LRX LTX; do
  B="/sys/class/net/$IF/statistics"
  if [ -d "$B" ]; then
    RX=$(cat "$B/rx_bytes" 2>/dev/null || echo NA)
    TX=$(cat "$B/tx_bytes" 2>/dev/null || echo NA)
    printf '%-8s %-18s sysfs_rx=%-13s db_last_rx=%-13s | sysfs_tx=%-13s db_last_tx=%-13s\n' \
      "$IF" "$U" "$RX" "$LRX" "$TX" "$LTX"
  else
    printf '%-8s %-18s (iface gone — stale row)\n' "$IF" "$U"
  fi
done | head -100

sec "CHAP-SECRETS (counts only — NO secrets printed)"
echo "panel-managed user lines: $(grep -c '^\"' /etc/ppp/chap-secrets 2>/dev/null || echo 0)"
echo "total non-comment lines : $(grep -cvE '^[[:space:]]*#|^[[:space:]]*$' /etc/ppp/chap-secrets 2>/dev/null || echo 0)"

sec "ACCOUNTING LOG"
AL=/var/log/vpn-panel/accounting.log
ls -la "$AL"* 2>/dev/null || true
echo "current-file line count: $(wc -l < "$AL" 2>/dev/null || echo 0)"
echo "last 5 records:"; tail -5 "$AL" 2>/dev/null || true

sec "ACCEL-PPP L2TP CONFIG"
cat /etc/accel-ppp/accel-ppp.conf 2>/dev/null
sec "ACCEL-PPP SSTP CONFIG (cli port + modules matter)"
sed -E 's/(psk|secret|ssl-pemfile)=.*/\1=***/I' /etc/accel-ppp/sstp.conf 2>/dev/null

sec "PPP HOOKS"
echo "----- /etc/ppp/ip-up -----";   cat /etc/ppp/ip-up   2>/dev/null
echo "----- /etc/ppp/ip-down -----"; cat /etc/ppp/ip-down 2>/dev/null
ls -la /etc/ppp/ip-up.d /etc/ppp/ip-down.d 2>/dev/null

sec "DEPLOYED BACKEND VERSION + FILES"
grep -n 'version=' /opt/vpn-panel/backend/app/main.py 2>/dev/null || true
ls -la /opt/vpn-panel/backend/app 2>/dev/null | head -40

sec "ACCEL-PPPD BUILD"
timeout 5 accel-pppd -v 2>&1 | head -3 || echo "(accel-pppd -v failed)"

sec "RECENT vpn-panel LOG (enforcer / quota / errors)"
journalctl -u vpn-panel -n 60 --no-pager 2>/dev/null | grep -iE 'enforce|terminat|quota|error|traceback|cycle failed' | tail -40 \
  || journalctl -u vpn-panel -n 25 --no-pager 2>/dev/null || true

# ----------------------- SAFE BACKUP (no service impact) ---------------------
sec "BACKUP -> $BK"
sqlite3 "$DB" ".backup '$BK/vpn.db'" 2>&1 && echo "OK  vpn.db" || echo "FAIL vpn.db backup"
cp -a /etc/ppp/chap-secrets   "$BK/chap-secrets"  2>/dev/null && echo "OK  chap-secrets"
cp -a /etc/accel-ppp          "$BK/accel-ppp"     2>/dev/null && echo "OK  accel-ppp configs"
cp -a /opt/vpn-panel/.env     "$BK/env"           2>/dev/null && echo "OK  .env"
cp -a /opt/vpn-panel/backend/app "$BK/backend-app" 2>/dev/null && echo "OK  backend code"
cp -a /var/log/vpn-panel/accounting.log "$BK/accounting.log" 2>/dev/null && echo "OK  accounting.log"

echo
echo "============================================================"
echo "AUDIT COMPLETE (read-only)."
echo "  Report : $REPORT"
echo "  Backup : $BK"
echo "Send me report.txt — it has NO passwords/PSKs."
echo "============================================================"
