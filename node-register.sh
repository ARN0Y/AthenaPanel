#!/usr/bin/env bash
###############################################################################
# Register a termination node with the panel and print its agent credentials.
#
#     bash node-register.sh <name> [address] [note]
#
# Prints the token ONCE. It is stored in the database and can be read back or
# rotated from there, but this is the only time it is shown as part of a
# ready-to-paste agent config.
###############################################################################
set -euo pipefail

NAME="${1:-}"
ADDRESS="${2:-}"
NOTE="${3:-}"
[ -n "$NAME" ] || { echo "usage: bash node-register.sh <name> [address] [note]" >&2; exit 1; }

INSTALL_DIR=/opt/vpn-panel
[ -d "$INSTALL_DIR/backend" ] || { echo "[x] $INSTALL_DIR/backend missing" >&2; exit 1; }

cd "$INSTALL_DIR/backend"
set -a; . "$INSTALL_DIR/.env"; set +a

OUT_DIR="${OUT_DIR:-/root/node-bundles}"
mkdir -p "$OUT_DIR"; chmod 700 "$OUT_DIR"

"$INSTALL_DIR/backend/venv/bin/python" - "$NAME" "$ADDRESS" "$NOTE" "$OUT_DIR" <<'PY'
import asyncio, os, sys
sys.path.insert(0, ".")
from app.database import AsyncSessionLocal
from app import nodes, pki

name, address, note, out_dir = sys.argv[1:5]


async def main():
    async with AsyncSessionLocal() as db:
        node = await nodes.register(db, name=name, address=address, note=note)
        await db.commit()
        node_id, token = node.id, node.token

    # The node's identity material. Issued once, handed over once, never kept
    # on the panel: only the CA stays here, so a panel compromise does not
    # hand over every node's private key as well.
    cert, key, ca = pki.issue_node(node_id, name)
    d = os.path.join(out_dir, f"node-{node_id}")
    os.makedirs(d, mode=0o700, exist_ok=True)
    for fname, data, mode in (
        ("node.crt", cert, 0o600),
        ("node.key", key, 0o600),
        ("ca.crt", ca, 0o644),
    ):
        p = os.path.join(d, fname)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, "w") as fh:
            fh.write(data)

    print(f"NODE_ID={node_id}")
    print(f"NODE_NAME={name}")
    print(f"TOKEN={token}")
    print(f"BUNDLE={d}")


asyncio.run(main())
PY
