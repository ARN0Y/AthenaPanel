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

"$INSTALL_DIR/backend/venv/bin/python" - "$NAME" "$ADDRESS" "$NOTE" <<'PY'
import asyncio, sys
sys.path.insert(0, ".")
from app.database import AsyncSessionLocal
from app import nodes

name, address, note = sys.argv[1], sys.argv[2], sys.argv[3]


async def main():
    async with AsyncSessionLocal() as db:
        node = await nodes.register(db, name=name, address=address, note=note)
        await db.commit()
        print(f"NODE_ID={node.id}")
        print(f"NODE_NAME={node.name}")
        print(f"TOKEN={node.token}")


asyncio.run(main())
PY
