#!/usr/bin/env bash
###############################################################################
# (Re)issue the identity bundle for a node that already exists.
#
#     bash node-bundle.sh <node_id>
#
# Use it to rotate a node's certificate, or to rebuild a bundle that was lost.
# The old certificate keeps working until the node is disabled or its token is
# rotated — the hub checks the database on every connect, so revocation is a
# row update, not a CRL.
#
# Prints the token again, since the bundle is useless without it.
###############################################################################
set -euo pipefail

NODE_ID="${1:-}"
[ -n "$NODE_ID" ] || { echo "usage: bash node-bundle.sh <node_id>" >&2; exit 1; }

INSTALL_DIR=/opt/vpn-panel
OUT_DIR="${OUT_DIR:-/root/node-bundles}"
mkdir -p "$OUT_DIR"; chmod 700 "$OUT_DIR"

cd "$INSTALL_DIR/backend"
set -a; . "$INSTALL_DIR/.env"; set +a

"$INSTALL_DIR/backend/venv/bin/python" - "$NODE_ID" "$OUT_DIR" <<'PY'
import asyncio, os, sys
sys.path.insert(0, ".")
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import Node
from app import pki

node_id, out_dir = int(sys.argv[1]), sys.argv[2]


async def main():
    async with AsyncSessionLocal() as db:
        node = (await db.execute(select(Node).where(Node.id == node_id))).scalar_one_or_none()
        if node is None:
            print(f"no such node: {node_id}", file=sys.stderr)
            raise SystemExit(1)
        if node.is_local:
            print("node 1 is this server; it has no agent and needs no bundle", file=sys.stderr)
            raise SystemExit(1)
        name, token = node.name, node.token

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
