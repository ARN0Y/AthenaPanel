#!/usr/bin/env bash
###############################################################################
# Phase 1 acceptance check.
#
#     bash node-verify.sh <node_id>
#
# Compares what the agent reported over gRPC against what the panel reads from
# this host's kernel directly. Only meaningful when the agent under test is
# running on THIS machine — which is exactly the Phase 1 setup: prove the new
# path returns the same numbers as the trusted one before anything is allowed
# to act on it.
#
# Counters move while we look at them, so an exact match is not the bar. The
# bar is: the same set of interfaces and peers, and every counter no lower than
# what the agent saw (they only ever increase) with drift consistent with how
# long ago the report arrived.
###############################################################################
set -euo pipefail

NODE_ID="${1:-}"
[ -n "$NODE_ID" ] || { echo "usage: bash node-verify.sh <node_id>" >&2; exit 1; }

INSTALL_DIR=/opt/vpn-panel
cd "$INSTALL_DIR/backend"
set -a; . "$INSTALL_DIR/.env"; set +a

"$INSTALL_DIR/backend/venv/bin/python" - "$NODE_ID" <<'PY'
import asyncio, json, sys
from datetime import datetime, timezone

sys.path.insert(0, ".")
from sqlalchemy import select                      # noqa: E402
from app.database import AsyncSessionLocal          # noqa: E402
from app.models import Node                         # noqa: E402
from app import pppd, wireguard                     # noqa: E402

node_id = int(sys.argv[1])
FAIL = []


def check(name, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"   [{detail}]" if detail else ""))
    if not ok:
        FAIL.append(name)


async def main():
    async with AsyncSessionLocal() as db:
        node = (await db.execute(select(Node).where(Node.id == node_id))).scalar_one_or_none()
    if node is None:
        print(f"no such node: {node_id}")
        return 2
    if not node.last_report:
        print(f"node {node_id} ({node.name}) has never reported")
        return 2

    rep = json.loads(node.last_report)
    reported_at = datetime.fromisoformat(rep["at"])
    age = (datetime.now(timezone.utc) - reported_at).total_seconds()
    print(f"node {node_id} ({node.name})  agent={node.agent_version}  "
          f"host={node.hostname}  report age={age:.1f}s\n")

    # ---- ppp ------------------------------------------------------------
    agent_ppp = {p["ifname"]: p for p in rep["ppp"]}
    scan_ok, live = pppd.scan_ppp_ifaces()
    check("the local ppp scan works", scan_ok)
    local_ppp = {i: pppd.read_iface_bytes(i) for i in live}

    only_agent = sorted(set(agent_ppp) - set(local_ppp))
    only_local = sorted(set(local_ppp) - set(agent_ppp))
    # A session can start or end in the gap between the two reads, so a small
    # difference is normal churn; a large one means the agent is not seeing the
    # same machine.
    check("the agent sees the same ppp interfaces the panel does",
          len(only_agent) + len(only_local) <= max(2, len(local_ppp) // 20),
          f"{len(agent_ppp)} agent / {len(local_ppp)} local, "
          f"only-agent={only_agent[:3]} only-local={only_local[:3]}")

    regressions, checked = [], 0
    for ifname, p in agent_ppp.items():
        if ifname not in local_ppp:
            continue
        lrx, ltx = local_ppp[ifname]
        checked += 1
        if lrx < p["rx_bytes"] or ltx < p["tx_bytes"]:
            regressions.append((ifname, p["rx_bytes"], p["tx_bytes"], lrx, ltx))
    check("every counter the agent reported is <= the live counter",
          not regressions, f"{checked} compared, {len(regressions)} regressed: {regressions[:2]}")

    if checked:
        drift = sum((local_ppp[i][0] - p["rx_bytes"]) + (local_ppp[i][1] - p["tx_bytes"])
                    for i, p in agent_ppp.items() if i in local_ppp)
        print(f"         total drift over {checked} interfaces in {age:.1f}s: "
              f"{drift/1e6:.2f} MB ({drift/max(age,0.1)/125000:.2f} Mbps)")

    # ---- wireguard -------------------------------------------------------
    agent_wg = {p["public_key"]: p for p in rep["wg"]}
    if wireguard.iface_up():
        dump = await wireguard.show_dump()
        check("the agent sees the same WireGuard peers",
              set(agent_wg) == set(dump),
              f"{len(agent_wg)} agent / {len(dump)} local")
        wg_reg = [k for k, p in agent_wg.items()
                  if k in dump and (dump[k]["rx"] < p["rx_bytes"] or dump[k]["tx"] < p["tx_bytes"])]
        check("every WireGuard counter is <= the live counter",
              not wg_reg, f"{len(wg_reg)} regressed")
        hs_ok = all(abs(dump[k]["handshake"] - p["last_handshake_unix"]) <= 180
                    for k, p in agent_wg.items() if k in dump and p["last_handshake_unix"])
        check("handshake times agree within one rekey window", hs_ok)
    else:
        print("  SKIP  WireGuard (interface not up here)")

    # ---- host ------------------------------------------------------------
    h = rep["host"]
    check("the agent reports the VPN engines as up",
          h["xl2tpd_ok"] and h["ipsec_ok"],
          f"xl2tpd={h['xl2tpd_ok']} ipsec={h['ipsec_ok']} "
          f"accel={h['accel_ppp_ok']} wg={h['wireguard_ok']}")
    check("the report is fresh", age < 60, f"{age:.1f}s old")

    print("\n" + ("PHASE 1 ACCEPTANCE: PASS" if not FAIL else f"FAILURES: {FAIL}"))
    return 1 if FAIL else 0


sys.exit(asyncio.run(main()))
PY
