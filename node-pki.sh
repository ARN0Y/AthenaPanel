#!/usr/bin/env bash
###############################################################################
# Create the panel's node CA and issue the hub's server certificate.
#
#     bash node-pki.sh <addr> [addr...]
#
# Each <addr> is something an agent will dial — a public IP, a private IP, a
# hostname. They all go into the certificate's SANs, so the same certificate
# works whichever one a given node was told to use. Re-running with a longer
# list is normal and safe: the CA is never replaced, only the hub certificate
# is reissued.
#
# The CA is deliberately private rather than Let's Encrypt: nodes dial raw IPs
# that change when one gets burned, and what matters is "is this our panel and
# our agent", not "does this name resolve here". See backend/app/pki.py.
###############################################################################
set -euo pipefail

[ $# -ge 1 ] || { echo "usage: bash node-pki.sh <addr> [addr...]" >&2; exit 1; }

INSTALL_DIR=/opt/vpn-panel
cd "$INSTALL_DIR/backend"
set -a; . "$INSTALL_DIR/.env"; set +a

"$INSTALL_DIR/backend/venv/bin/python" - "$@" <<'PY'
import sys
sys.path.insert(0, ".")
from app import pki

hosts = sys.argv[1:]
existed = pki.ca_exists()
pki.ensure_ca()
print(("CA already present at " if existed else "CA created at ") + str(pki.CA_CRT))
pki.issue_hub(hosts)
print("hub certificate issued for: " + ", ".join(hosts))
print("  " + str(pki.HUB_CRT))
PY

echo
echo "fingerprints:"
openssl x509 -in /var/lib/vpn-panel/pki/ca.crt  -noout -sha256 -fingerprint | sed 's/^/  CA  /'
openssl x509 -in /var/lib/vpn-panel/pki/hub.crt -noout -sha256 -fingerprint | sed 's/^/  hub /'
openssl x509 -in /var/lib/vpn-panel/pki/hub.crt -noout -text | grep -A1 'Subject Alternative Name' | tail -1 | sed 's/^/  SAN:/'
echo
echo "Restart the hub to pick up the certificate:  systemctl restart vpn-nodehub"
