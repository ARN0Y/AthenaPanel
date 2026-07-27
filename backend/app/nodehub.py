"""gRPC control plane: accepts agent connections and records what they report.

Runs as its OWN systemd service, not inside the panel process, on purpose:

  * grpc.aio brings its own C-core event handling and has a history of
    interacting badly with the uvloop policy uvicorn installs. The panel is a
    live earning service; a control-plane dependency must not be able to stop
    it from booting.
  * The two scale differently. The UI is bursty and human-paced; the hub holds
    one long-lived stream per node forever.

PHASE 1 IS DELIBERATELY READ-ONLY. Reports are recorded and the node's
heartbeat is refreshed, and nothing else. No accounting is credited, no session
is created, no user is disconnected. The point of this phase is to prove the
agent's numbers agree with what the panel already reads locally, BEFORE
anything is allowed to act on them.
"""

import asyncio
import logging
import os
import signal
import time
from datetime import datetime, timezone

from sqlalchemy import select

from .config import settings
from .database import AsyncSessionLocal
from .models import Node

log = logging.getLogger("vpn-panel.nodehub")

# Bumped only on an incompatible wire change.
PROTOCOL_VERSION = 1

# What the hub asks agents to use. Must stay well under nodes.STALE_AFTER_SECONDS
# (90s) or a healthy node would look silent between reports.
REPORT_INTERVAL_SECONDS = 15

# The last report from each node, in memory only. Phase 1 uses it purely for
# comparison against locally-read truth; it is deliberately not persisted,
# because persisting numbers nothing acts on is just noise in the DB.
LAST_REPORT: dict[int, dict] = {}


def _listen_address() -> str:
    """Where the hub listens.

    Defaults to loopback. Phase 1 validates against the local node, and a
    control plane should not be exposed to the internet before it has TLS and
    a hostname in front of it — nginx already terminates TLS for everything
    else on this host and can `grpc_pass` here when a remote node appears.
    """
    return os.environ.get("NODEHUB_LISTEN", "127.0.0.1:50051")


async def _authenticate(token: str) -> Node | None:
    """Resolve a token to its node. Returns None for anything unrecognised."""
    if not token or len(token) < 16:
        return None
    async with AsyncSessionLocal() as db:
        node = (
            await db.execute(select(Node).where(Node.token == token))
        ).scalar_one_or_none()
        if node is None or node.is_local:
            # is_local is refused on purpose: node 1 is read from sysfs
            # directly and must never depend on an agent claiming to be it.
            return None
        db.expunge(node)
        return node


async def _record_hello(node_id: int, hello) -> None:
    async with AsyncSessionLocal() as db:
        node = await db.get(Node, node_id)
        if node is None:
            return
        node.agent_version = (hello.agent_version or "")[:32]
        node.hostname = (hello.hostname or "")[:128]
        node.kernel = (hello.kernel or "")[:128]
        await db.commit()


async def _record_report(node_id: int, report) -> None:
    """Refresh the heartbeat and stash the payload. Nothing else — see module doc.

    The heartbeat is the one thing that matters right now: it is what
    nodes.authoritative_ids() reads to decide whether this node may close its
    own sessions. Writing it here, and only on a well-formed report, is what
    makes "silent node" a fact rather than a guess.
    """
    now = datetime.now(timezone.utc)
    LAST_REPORT[node_id] = {
        "at": now,
        "sent_at_unix_ms": report.sent_at_unix_ms,
        "ppp": [
            {
                "ifname": s.ifname,
                "rx_bytes": s.rx_bytes,
                "tx_bytes": s.tx_bytes,
                "username": s.username,
                "peer_ip": s.peer_ip,
                "pid": s.pid,
            }
            for s in report.ppp
        ],
        "wg": [
            {
                "public_key": p.public_key,
                "rx_bytes": p.rx_bytes,
                "tx_bytes": p.tx_bytes,
                "last_handshake_unix": p.last_handshake_unix,
                "address": p.address,
            }
            for p in report.wg
        ],
        "host": {
            "uptime_seconds": report.host.uptime_seconds,
            "load1": report.host.load1,
            "mem_total_bytes": report.host.mem_total_bytes,
            "mem_available_bytes": report.host.mem_available_bytes,
            "xl2tpd_ok": report.host.xl2tpd_ok,
            "ipsec_ok": report.host.ipsec_ok,
            "accel_ppp_ok": report.host.accel_ppp_ok,
            "wireguard_ok": report.host.wireguard_ok,
        },
    }
    async with AsyncSessionLocal() as db:
        node = await db.get(Node, node_id)
        if node is not None:
            node.last_seen_at = now
            await db.commit()


def build_servicer():
    """Built lazily so importing this module never requires the generated stubs."""
    from .pb import nodehub_pb2, nodehub_pb2_grpc

    class NodeHubServicer(nodehub_pb2_grpc.NodeHubServicer):
        async def Connect(self, request_iterator, context):
            node: Node | None = None
            reports = 0
            peer = context.peer()
            try:
                async for msg in request_iterator:
                    kind = msg.WhichOneof("payload")

                    if kind == "hello":
                        if node is not None:
                            log.warning("%s: duplicate hello, ignoring", peer)
                            continue
                        if msg.hello.protocol_version != PROTOCOL_VERSION:
                            log.warning(
                                "%s: refusing protocol v%d (hub speaks v%d)",
                                peer, msg.hello.protocol_version, PROTOCOL_VERSION,
                            )
                            return
                        node = await _authenticate(msg.hello.token)
                        if node is None:
                            # No detail in the reply: an unauthenticated caller
                            # learns nothing about which part was wrong.
                            log.warning("%s: rejected, unknown node token", peer)
                            return
                        await _record_hello(node.id, msg.hello)
                        log.info(
                            "node %d (%s) connected from %s — agent %s, %s",
                            node.id, node.name, peer,
                            msg.hello.agent_version or "?", msg.hello.hostname or "?",
                        )
                        yield nodehub_pb2.HubMessage(
                            welcome=nodehub_pb2.Welcome(
                                node_id=node.id,
                                node_name=node.name,
                                report_interval_seconds=REPORT_INTERVAL_SECONDS,
                                protocol_version=PROTOCOL_VERSION,
                            )
                        )
                        continue

                    if node is None:
                        log.warning("%s: message before hello, dropping stream", peer)
                        return

                    if kind == "report":
                        await _record_report(node.id, msg.report)
                        reports += 1
                        if reports == 1 or reports % 40 == 0:
                            r = LAST_REPORT[node.id]
                            log.info(
                                "node %d report #%d: %d ppp, %d wg peers",
                                node.id, reports, len(r["ppp"]), len(r["wg"]),
                            )
                        yield nodehub_pb2.HubMessage(
                            ack=nodehub_pb2.Ack(
                                received_at_unix_ms=int(time.time() * 1000), ok=True
                            )
                        )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("node %s stream failed", node.id if node else "?")
            finally:
                if node is not None:
                    log.info("node %d (%s) disconnected after %d report(s)",
                             node.id, node.name, reports)

    return NodeHubServicer()


async def serve() -> None:
    import grpc

    from .pb import nodehub_pb2_grpc

    server = grpc.aio.server()
    nodehub_pb2_grpc.add_NodeHubServicer_to_server(build_servicer(), server)
    addr = _listen_address()
    server.add_insecure_port(addr)
    await server.start()
    log.info(
        "nodehub listening on %s (protocol v%d, report interval %ds)",
        addr, PROTOCOL_VERSION, REPORT_INTERVAL_SECONDS,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    log.info("nodehub shutting down")
    # Give open streams a moment to finish their current message.
    await server.stop(grace=5)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log.info("database: %s", "postgres" if settings.is_postgres else "sqlite")
    asyncio.run(serve())


if __name__ == "__main__":
    main()
