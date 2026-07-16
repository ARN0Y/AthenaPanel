#!/usr/bin/env bash
###############################################################################
# Enable SSTP (TCP/443) on the overseas server, alongside the existing
# xl2tpd L2TP/IPsec. Same users (same /etc/ppp/chap-secrets) — every account
# works over BOTH L2TP and SSTP automatically.
#
#   sudo bash setup-sstp.sh                 # uses SSTP_ADDRESS from .env
#   sudo SSTP_DOMAIN=sstp.topmeli.com bash setup-sstp.sh
#
# Requirements:
#   * accel-pppd present (built by athena-setup.sh).
#   * The SSTP domain must resolve to a server whose :80 reaches THIS box, so
#     Let's Encrypt's HTTP-01 challenge succeeds. If the domain points at the
#     Iran relay, the relay must forward tcp/80 (and tcp/443) to 10.50.50.1.
###############################################################################
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE=/opt/vpn-panel/.env
log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

# --- domain (env override, else repo .env, else deployed .env) ------------
DOMAIN="${SSTP_DOMAIN:-}"
if [ -z "$DOMAIN" ]; then
    for f in "$REPO_DIR/.env" "$ENV_FILE"; do
        [ -f "$f" ] || continue
        DOMAIN="$(grep -E '^SSTP_ADDRESS=' "$f" 2>/dev/null | head -1 | cut -d= -f2- | tr -d ' \"' || true)"
        [ -n "$DOMAIN" ] && break
    done
fi
[ -n "$DOMAIN" ] || die "no SSTP domain (set SSTP_ADDRESS in .env or run: SSTP_DOMAIN=sstp.example.com bash setup-sstp.sh)"
log "SSTP domain: $DOMAIN"

command -v accel-pppd >/dev/null 2>&1 || die "accel-pppd not found — run athena-setup.sh first"

# --- TLS cert (Let's Encrypt, webroot via nginx) --------------------------
mkdir -p /var/www/html /var/log/accel-ppp
if ! command -v certbot >/dev/null 2>&1; then
    log "installing certbot"
    apt-get update -qq && apt-get install -y certbot >/dev/null
fi
LIVE="/etc/letsencrypt/live/$DOMAIN"
# MikroTik (and other RouterOS) SSTP only negotiates ECDHE-RSA ciphers over
# TLS1.2, so the server cert MUST be RSA. An ECDSA cert (certbot's default on
# newer distros) makes the server reject MikroTik with a TLS handshake_failure
# while Windows (TLS1.3) still works. Force RSA so BOTH work.
need_cert=1
if [ -f "$LIVE/cert.pem" ] && openssl x509 -in "$LIVE/cert.pem" -noout -text 2>/dev/null | grep -qi 'rsaEncryption'; then
    need_cert=0
fi
if [ "$need_cert" = 1 ]; then
    log "requesting RSA certificate (HTTP-01 via /var/www/html)"
    certbot certonly --webroot -w /var/www/html -d "$DOMAIN" \
        --cert-name "$DOMAIN" --key-type rsa --rsa-key-size 2048 --force-renewal \
        --non-interactive --agree-tos --register-unsafely-without-email \
        || die "certbot failed — make sure $DOMAIN:80 reaches this server (relay must forward tcp/80 to 10.50.50.1) and retry"
fi
[ -f "$LIVE/fullchain.pem" ] || die "cert still missing at $LIVE"
log "RSA certificate ready: $LIVE"

# --- accel-ppp SSTP config ------------------------------------------------
# accel-ppp's sstp wants ONE PEM with the full chain AND the private key.
# Separate cert/key files make TLS negotiate no cipher on many builds.
cat "$LIVE/fullchain.pem" "$LIVE/privkey.pem" > /etc/accel-ppp/sstp-combined.pem
chmod 600 /etc/accel-ppp/sstp-combined.pem
install -d -m 0755 /etc/accel-ppp
install -m 0644 "$REPO_DIR/configs/accel-ppp-sstp.conf" /etc/accel-ppp/sstp.conf
sed -i "s|__SSL_PEM__|/etc/accel-ppp/sstp-combined.pem|g; s|__SSTP_HOST__|$DOMAIN|g" /etc/accel-ppp/sstp.conf
install -m 0644 "$REPO_DIR/deploy/systemd/accel-ppp-sstp.service" /etc/systemd/system/accel-ppp-sstp.service

# kernel ppp modules (accel needs them to build ppp interfaces)
modprobe ppp_generic 2>/dev/null || true

systemctl daemon-reload
systemctl enable accel-ppp-sstp >/dev/null 2>&1 || true
systemctl restart accel-ppp-sstp
sleep 2

# --- iptables: allow 443 in + masquerade the SSTP pool --------------------
WAN=$(ip route get 8.8.8.8 2>/dev/null | grep -oP 'dev \K\S+' | head -1 || true); WAN=${WAN:-eth0}
iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || iptables -I INPUT -p tcp --dport 443 -j ACCEPT
iptables -t nat -C POSTROUTING -s 192.168.0.0/16 -o "$WAN" -j MASQUERADE 2>/dev/null \
    || iptables -t nat -A POSTROUTING -s 192.168.0.0/16 -o "$WAN" -j MASQUERADE
command -v netfilter-persistent >/dev/null 2>&1 && netfilter-persistent save >/dev/null 2>&1 || true

# --- auto-renew hook (rebuild combined pem + reload sstp) -----------------
mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/accel-sstp.sh <<EOF
#!/bin/sh
cat "$LIVE/fullchain.pem" "$LIVE/privkey.pem" > /etc/accel-ppp/sstp-combined.pem
chmod 600 /etc/accel-ppp/sstp-combined.pem
systemctl restart accel-ppp-sstp
EOF
chmod +x /etc/letsencrypt/renewal-hooks/deploy/accel-sstp.sh

# --- flip the panel toggle on ---------------------------------------------
if [ -f "$ENV_FILE" ]; then
    grep -q '^SSTP_ENABLED=' "$ENV_FILE" \
        && sed -i 's|^SSTP_ENABLED=.*|SSTP_ENABLED=true|' "$ENV_FILE" \
        || echo 'SSTP_ENABLED=true' >> "$ENV_FILE"
fi

# --- verify ---------------------------------------------------------------
echo
log "status:"
systemctl is-active --quiet accel-ppp-sstp && printf '   \033[1;32m✓\033[0m accel-ppp-sstp\n' || warn "accel-ppp-sstp not active (journalctl -u accel-ppp-sstp -n 40)"
ss -tlnp | grep -q ':443' && printf '   \033[1;32m✓\033[0m listening on tcp/443\n' || warn "nothing on tcp/443"

cat <<EOF

============================================================================
  SSTP enabled on tcp/443 for $DOMAIN. Same users as L2TP (chap-secrets).

  In the panel: Settings > Server shows SSTP enabled. Toggle it there, OR it
  is on now (SSTP_ENABLED=true in .env — restart panel to read it).

  >>> ON THE IRAN RELAY (your part), forward TCP to this server:
        iptables -t nat -A PREROUTING -p tcp --dport 443 -j DNAT --to 10.50.50.1:443
        iptables -t nat -A PREROUTING -p tcp --dport 80  -j DNAT --to 10.50.50.1:80
      (80 is only needed for the cert challenge + auto-renewal.)

  Windows: built-in SSTP VPN, server = $DOMAIN, user/pass from the profile.
  MikroTik: /interface sstp-client, connect-to=$DOMAIN, verify-cert=yes.
============================================================================
EOF
