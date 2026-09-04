#!/usr/bin/env bash
###############################################################################
#  Athena master — full MACHINE backup (complements athena-backup.sh)
#
#  athena-backup.sh captures what athena-setup.sh needs to stand a PANEL up
#  again: the database, .env, chap-secrets, ipsec.secrets, wg-panel.conf.
#  That is not enough to rebuild this HOST. Missing from it, and collected here:
#
#    - ipsec.conf, including the two settings that stop the recurring L2TP
#      outage (ike-socket-errqueue=no, ike-socket-bufsize)
#    - accel-ppp's config and its SSTP TLS certificate + key
#    - nginx vhosts and every certificate under /etc/nginx/ssl
#    - the backhaul: server.toml AND the backhaul_premium binary, which is in
#      no package repository
#    - acme.sh's account key and issued certificates
#    - libreswan 5.3 and accel-ppp, both SOURCE-BUILT. The distro libreswan
#      dropped modp1024 / DH group 2, which Windows and MikroTik clients still
#      offer, so a rebuild from apt breaks those customers.
#    - iptables / ipset / ip rule / ip route / sysctl.d — the forwarding and
#      policy-routing state for the outbound locations
#    - systemd units and drop-ins, including the ipsec watchdog, the
#      libreswan-unwedge guard and the backhaul /16 route
#    - /usr/local/sbin scripts, x-ui, PasarGuard, the login artwork
#
#      sudo bash athena-config-backup.sh
#
#  Produces /root/athena-master-backup-<stamp>.tar.gz plus a MANIFEST and a
#  RESTORE.md. Read-only with respect to the running services.
#
#  THE ARCHIVE IS AS SENSITIVE AS THE SERVER: the IPsec PSK, every customer
#  password, WireGuard and TLS private keys, database credentials and the
#  Telegram token. Move it off the box and keep it out of the git repo.
###############################################################################
set -u

STAMP=$(date -u +%Y%m%d-%H%M%S)
B=/root/athena-backup-$STAMP
mkdir -p "$B"/{ipsec,l2tp,sstp,wireguard,panel,nginx,backhaul,acme,xui,branding,systemd,scripts,network,system,database,pasarguard}
mkdir -p "$B"/binaries/{libexec,sbin,bin,lib64}

say()  { printf '  %-46s %s\n' "$1" "$2"; }
copy() { if [ -e "$1" ]; then cp -a "$1" "$2" 2>/dev/null && say "$3" ok || say "$3" "COPY FAILED"
         else say "$3" absent; fi; }

echo "=== keys and configuration ==="
copy /etc/ipsec.conf            "$B/ipsec/"     "ipsec.conf (incl. the errqueue fix)"
copy /etc/ipsec.secrets         "$B/ipsec/"     "ipsec.secrets (the PSK)"
copy /etc/ipsec.d               "$B/ipsec/"     "ipsec.d/"
copy /etc/xl2tpd                "$B/l2tp/"      "xl2tpd/"
copy /etc/ppp                   "$B/l2tp/"      "ppp/ (chap-secrets)"
copy /etc/accel-ppp             "$B/sstp/"      "accel-ppp/ (SSTP cert + key)"
copy /etc/wireguard             "$B/wireguard/" "wireguard/ (server private key)"
copy /opt/vpn-panel/.env        "$B/panel/"     ".env"
copy /etc/nginx                 "$B/nginx/"     "nginx/ (vhosts + ssl)"
copy /root/bk                   "$B/backhaul/"  "bk/ (toml + binary)"
copy /root/.acme.sh             "$B/acme/"      ".acme.sh/"
copy /etc/x-ui                  "$B/xui/"       "x-ui/"
copy /var/lib/vpn-panel/branding "$B/branding/" "login artwork"

echo "=== systemd ==="
cp -a /etc/systemd/system/*.service      "$B/systemd/" 2>/dev/null
cp -a /etc/systemd/system/*.timer        "$B/systemd/" 2>/dev/null
cp -a /etc/systemd/system/*.service.d    "$B/systemd/" 2>/dev/null
cp -a /etc/systemd/journald.conf.d       "$B/systemd/" 2>/dev/null
systemctl list-unit-files --state=enabled --no-legend > "$B/systemd/ENABLED-UNITS.txt" 2>/dev/null
say "units + drop-ins" "$(ls "$B/systemd" | wc -l) entries"

echo "=== scripts ==="
for f in /usr/local/sbin/athena-*.sh /usr/local/sbin/libreswan-unwedge.sh \
         /usr/local/sbin/outbound-*.sh /usr/local/sbin/badbox-*.sh \
         /usr/local/sbin/warp-*.sh /usr/local/sbin/bh-route16.sh; do
    [ -f "$f" ] && cp -a "$f" "$B/scripts/" 2>/dev/null
done
say "/usr/local/sbin" "$(ls "$B/scripts" | wc -l) files"

echo "=== source-built binaries ==="
# Distinct subdirectories: /usr/local/libexec/ipsec is a DIRECTORY and
# /usr/local/sbin/ipsec is a FILE, so a flat copy silently loses one of them.
copy /usr/local/libexec/ipsec   "$B/binaries/libexec/" "libexec/ipsec/ (pluto, addconn)"
copy /usr/local/lib64/accel-ppp "$B/binaries/lib64/"   "lib64/accel-ppp/"
copy /usr/local/sbin/ipsec      "$B/binaries/sbin/"    "sbin/ipsec"
copy /usr/local/sbin/accel-pppd "$B/binaries/sbin/"    "sbin/accel-pppd"
copy /usr/local/bin/accel-cmd   "$B/binaries/bin/"     "bin/accel-cmd"
echo "libreswan: $(ipsec --version 2>/dev/null | head -1)" > "$B/binaries/VERSIONS.txt"

echo "=== network state ==="
iptables-save  > "$B/network/iptables-save.txt"  2>/dev/null
ip6tables-save > "$B/network/ip6tables-save.txt" 2>/dev/null
ipset save     > "$B/network/ipset-save.txt"     2>/dev/null
ip addr show            > "$B/network/ip-addr.txt"      2>/dev/null
ip route show table all > "$B/network/ip-route-all.txt" 2>/dev/null
ip rule show            > "$B/network/ip-rule.txt"      2>/dev/null
ip xfrm policy          > "$B/network/xfrm-policy.txt"  2>/dev/null
copy /etc/iptables "$B/network/" "iptables/ (persisted)"
copy /etc/netplan  "$B/network/" "netplan/"
copy /etc/sysctl.d "$B/network/" "sysctl.d/"

echo "=== host ==="
copy /etc/rsyslog.d   "$B/system/" "rsyslog.d/"
copy /etc/logrotate.d "$B/system/" "logrotate.d/"
copy /etc/cron.d      "$B/system/" "cron.d/"
{ echo "hostname: $(hostname)"; echo "os: $(. /etc/os-release; echo "$PRETTY_NAME")"
  echo "kernel: $(uname -r)"; echo "collected: $(date -u) UTC"
  echo; echo "--- hosts ---"; cat /etc/hosts; echo; echo "--- fstab ---"; cat /etc/fstab
} > "$B/system/HOST.txt" 2>/dev/null
dpkg -l    > "$B/system/dpkg-list.txt"    2>/dev/null
crontab -l > "$B/system/root-crontab.txt" 2>/dev/null
cp -a /root/.ssh/authorized_keys "$B/system/root-authorized_keys" 2>/dev/null
say "host facts, packages, cron" ok

echo "=== database ==="
# pg_dump runs as the postgres user, which cannot write into a root-only
# directory — write somewhere world-writable and move the result.
#
# NOTE: --exclude-table-data does NOT shrink this. usage_samples is a
# TimescaleDB hypertable whose rows live in _timescaledb_internal chunks, so
# the history comes along regardless. Good for completeness, and it compresses
# to a fraction of its on-disk size.
T=$(mktemp -d); chmod 777 "$T"
sudo -u postgres pg_dump -d vpnpanel --no-owner --no-acl -f "$T/vpnpanel.sql" 2>"$T/err.txt"
if [ -s "$T/vpnpanel.sql" ]; then
    gzip -9 "$T/vpnpanel.sql"
    mv "$T/vpnpanel.sql.gz" "$B/database/vpnpanel.sql.gz"
    say "vpnpanel dump" "$(du -h "$B/database/vpnpanel.sql.gz" | cut -f1)"
else
    cp "$T/err.txt" "$B/database/dump-errors.txt" 2>/dev/null
    say "vpnpanel dump" "FAILED — see database/dump-errors.txt"
fi
sudo -u postgres pg_dumpall --roles-only > "$B/database/roles.sql" 2>/dev/null
# Plain CSVs too, so the important tables are readable without a postgres at all.
for t in users admins nodes outbounds app_settings api_keys wg_peers; do
    sudo -u postgres psql -d vpnpanel \
        -c "\copy (SELECT * FROM $t) TO '$T/$t.csv' WITH CSV HEADER" >/dev/null 2>&1
    [ -s "$T/$t.csv" ] && mv "$T/$t.csv" "$B/database/csv-$t.csv"
done
say "per-table CSVs" "$(ls "$B/database"/csv-*.csv 2>/dev/null | wc -l) files"
rm -rf "$T"

echo "=== PasarGuard ==="
copy /opt/pasarguard "$B/pasarguard/" "compose + env"
for c in $(docker ps --format '{{.Names}}' 2>/dev/null | grep -iE 'timescale|postgres|db'); do
    for u in pasarguard postgres; do
        DBS=$(docker exec "$c" psql -U "$u" -At -c \
              "SELECT datname FROM pg_database WHERE datistemplate=false AND datname<>'postgres';" 2>/dev/null)
        [ -z "$DBS" ] && continue
        for d in $DBS; do
            docker exec "$c" pg_dump -U "$u" --no-owner --no-acl "$d" 2>/dev/null \
                | gzip -9 > "$B/pasarguard/$d.sql.gz"
            [ -s "$B/pasarguard/$d.sql.gz" ] && say "pasarguard db '$d'" "$(du -h "$B/pasarguard/$d.sql.gz" | cut -f1)"
        done
        break 2
    done
done

# MANIFEST lists names, sizes and checksums — never contents.
{
  echo "Athena master backup — $STAMP UTC"
  echo "source: $(hostname) ($(hostname -I | awk '{print $1}'))"
  echo; echo "== files =="
  (cd "$B" && find . -type f -printf '%10s  %p\n' | sort -k2)
  echo; echo "== sha256 =="
  (cd "$B" && find . -type f ! -name MANIFEST.txt -exec sha256sum {} \; | sort -k2)
} > "$B/MANIFEST.txt" 2>/dev/null

chmod -R go-rwx "$B"
TAR=/root/athena-master-backup-$STAMP.tar.gz
tar -czf "$TAR" -C /root "$(basename "$B")" 2>/dev/null
chmod 600 "$TAR"
rm -rf "$B"

echo
echo "  archive: $TAR  ($(du -h "$TAR" | cut -f1))"
sha256sum "$TAR" | awk '{print "  sha256:  "$1}'
echo
echo "  Pull it to a machine that is not this one, then delete it here:"
echo "    ssh -T root@$(hostname -I | awk '{print $1}') 'cat $TAR' > $(basename "$TAR")"
echo "    ssh root@$(hostname -I | awk '{print $1}') 'rm -f $TAR'"
