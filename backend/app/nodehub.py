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
import itertools
import json
import logging
import os
import signal
import time
from datetime import datetime, timezone

from sqlalchemy import select

from . import (
    credit as credit_mod, nodecredit, nodes as nodes_mod, nodesessions,
)
from .config import settings
from .database import AsyncSessionLocal
from .models import Node

log = logging.getLogger("vpn-panel.nodehub")

# Bumped only on an incompatible wire change.
PROTOCOL_VERSION = 1

# What the hub asks agents to use. Must stay well under nodes.STALE_AFTER_SECONDS
# (90s) or a healthy node would look silent between reports.
REPORT_INTERVAL_SECONDS = 15

# Newest report per node, kept in memory for the hub's own use. The same
# payload is written to Node.last_report so that any other process can read it
# — the hub's memory is not a place other tools can look.
LAST_REPORT: dict[int, dict] = {}

# Monotonic per hub process. A node only ever compares it for equality with
# what it last applied, so restarting the hub simply causes one extra sync.
_sync_ids = itertools.count(1)


def _listen_address() -> str:
    """Where the hub listens. Loopback unless told otherwise."""
    return os.environ.get("NODEHUB_LISTEN", "127.0.0.1:50051")


def _tls_enabled() -> bool:
    """mTLS with the panel's own CA (see pki.py for why not Let's Encrypt).

    Off only for a loopback-only hub, where the transport never leaves the
    machine. Any listener reachable from outside must have it on.
    """
    return os.environ.get("NODEHUB_TLS", "1") != "0"


def _server_credentials():
    import grpc

    from . import pki

    if not (pki.HUB_CRT.exists() and pki.HUB_KEY.exists()):
        raise RuntimeError(
            f"TLS is on but {pki.HUB_CRT} is missing — run node-pki.sh first"
        )
    return grpc.ssl_server_credentials(
        [(pki.HUB_KEY.read_bytes(), pki.HUB_CRT.read_bytes())],
        root_certificates=pki.CA_CRT.read_bytes(),
        # A client without a certificate signed by OUR CA never reaches the
        # application at all — authentication happens before any message is
        # parsed, so an unauthenticated peer cannot even probe the protocol.
        require_client_auth=True,
    )


def _cert_node_id(context) -> int | None:
    """Which node the presented client certificate claims to be."""
    from . import pki

    try:
        chain = context.auth_context().get("x509_pem_cert") or []
        if not chain:
            return None
        from cryptography import x509 as _x

        cert = _x.load_pem_x509_certificate(chain[0])
        return pki.node_id_from_cert(cert.public_bytes(_serialization().Encoding.DER))
    except Exception:  # noqa: BLE001
        return None


def _serialization():
    from cryptography.hazmat.primitives import serialization

    return serialization


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


async def _record_report(node_id: int, report):
    """Refresh the heartbeat, stash the payload, and mirror the node's sessions.

    Returns (reconnect_requested_at, sync_requested_at), or None if the node row
    has been deleted — a stream whose node no longer exists must not keep
    running, or a deleted node would go on being served until its agent happened
    to restart.

    The heartbeat is what nodes.authoritative_ids() reads to decide whether this
    node may close its own sessions. Writing it here, and only on a well-formed
    report, is what makes "silent node" a fact rather than a guess.

    Sessions are mirrored for DISPLAY only — see nodesessions for why billing
    stays entirely on the credit path.
    """
    now = datetime.now(timezone.utc)
    LAST_REPORT[node_id] = payload = {
        "at": now.isoformat(),
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
    # Absolute counters in, deltas derived here against the previous report.
    # This is the whole reason the wire carries absolutes: a resend contributes
    # nothing instead of double-counting, and a dropped report is made up for by
    # the next one instead of vanishing.
    counters: dict[str, tuple[int, int]] = {
        f"ppp:{s['ifname']}": (s["rx_bytes"], s["tx_bytes"]) for s in payload["ppp"]
    }
    counters.update(
        {f"wg:{p['public_key']}": (p["rx_bytes"], p["tx_bytes"]) for p in payload["wg"]}
    )

    async with AsyncSessionLocal() as db:
        node = await db.get(Node, node_id)
        if node is None:
            return None
        previous: dict[str, tuple[int, int]] = {}
        if node.last_report:
            try:
                old = json.loads(node.last_report)
                previous = {
                    f"ppp:{s['ifname']}": (s["rx_bytes"], s["tx_bytes"])
                    for s in old.get("ppp", [])
                }
                previous.update({
                    f"wg:{p['public_key']}": (p["rx_bytes"], p["tx_bytes"])
                    for p in old.get("wg", [])
                })
            except (ValueError, KeyError):
                previous = {}

        nodes_mod.accumulate_traffic(node, counters, previous, now)
        node.last_seen_at = now
        node.last_report = json.dumps(payload, separators=(",", ":"))
        # Deliberately after last_seen_at: if the session mirror ever raises,
        # the commit below is lost and the node looks silent for one interval,
        # which is the safe direction — a held session is a delayed ledger row,
        # while a wrongly-closed one is a wrong invoice.
        await nodesessions.apply_report(db, node_id, payload["ppp"], now)
        await db.commit()
        return node.reconnect_requested_at, node.sync_requested_at


def build_servicer():
    """Built lazily so importing this module never requires the generated stubs."""
    from .pb import nodehub_pb2, nodehub_pb2_grpc

    class NodeHubServicer(nodehub_pb2_grpc.NodeHubServicer):
        async def Connect(self, request_iterator, context):
            node: Node | None = None
            reports = 0
            peer = context.peer()
            connected_at = datetime.now(timezone.utc)
            last_sync_at: datetime | None = None
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
                        # The certificate says "one of ours", the token says
                        # which one. Requiring both to agree means a stolen
                        # certificate alone is not enough, and neither is a
                        # stolen token.
                        if _tls_enabled():
                            claimed = _cert_node_id(context)
                            if claimed != node.id:
                                log.warning(
                                    "%s: certificate is node %s but the token is node %d",
                                    peer, claimed, node.id,
                                )
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
                                credit_poll_ms=credit_mod.CREDIT_POLL_MS,
                            )
                        )
                        # A reconnecting agent has no idea what it missed, so it
                        # gets the whole list immediately rather than waiting for
                        # the next change. Cheap, and it removes a whole class of
                        # "the node was serving a deleted user" bug.
                        sync_id = next(_sync_ids)
                        yield nodehub_pb2.HubMessage(
                            user_sync=await nodecredit.build_sync(node.id, sync_id)
                        )
                        last_sync_at = datetime.now(timezone.utc)
                        continue

                    if node is None:
                        log.warning("%s: message before hello, dropping stream", peer)
                        return

                    if kind == "credit_request":
                        req = msg.credit_request
                        reason = nodehub_pb2.CreditRequest.Reason.Name(req.reason)
                        grant = await nodecredit.handle_credit_request(
                            node_id=node.id,
                            username=req.username,
                            reason=reason,
                            consumed_bytes=req.consumed_bytes,
                            grant_id=req.grant_id,
                            session_rx=req.session_rx_bytes,
                            session_tx=req.session_tx_bytes,
                            ifname=req.ifname,
                        )
                        yield nodehub_pb2.HubMessage(
                            credit_grant=nodehub_pb2.CreditGrant(
                                username=req.username,
                                grant_id=grant.grant_id,
                                granted_bytes=grant.granted_bytes,
                                threshold_bytes=grant.threshold_bytes,
                                validity_seconds=grant.validity_seconds,
                                final=grant.final,
                                refused=grant.refused,
                                refuse_reason=grant.refuse_reason,
                                on_hub_unreachable=(
                                    nodehub_pb2.CreditGrant.CONTINUE_THEN_TERMINATE
                                ),
                            )
                        )
                        continue

                    if kind == "sync_ack":
                        await nodecredit.mark_synced(
                            node.id, msg.sync_ack.sync_id,
                            msg.sync_ack.ok, msg.sync_ack.detail,
                        )
                        log.info(
                            "node %d applied sync %d: %d user(s)%s",
                            node.id, msg.sync_ack.sync_id, msg.sync_ack.users_applied,
                            "" if msg.sync_ack.ok else f" FAILED: {msg.sync_ack.detail}",
                        )
                        continue

                    if kind == "report":
                        recorded = await _record_report(node.id, msg.report)
                        if recorded is None:
                            log.warning("node %d no longer exists; closing its stream", node.id)
                            LAST_REPORT.pop(node.id, None)
                            return
                        requested, sync_requested = recorded
                        # An operator asked this node to reconnect. Dropping the
                        # stream is enough: the agent's own backoff brings it
                        # straight back, and it re-derives everything from the
                        # kernel, so nothing is lost by cutting mid-flight.
                        #
                        # Checked BEFORE the disconnect queue is drained: claiming
                        # a kick clears it, so draining into a stream that is
                        # about to close would throw the request away.
                        if requested is not None and requested > connected_at:
                            log.info("node %d: reconnect requested, dropping the stream", node.id)
                            return
                        # Operator-initiated kicks queued by the panel process.
                        # Drained here because a report is the one moment we
                        # know the stream is alive in both directions.
                        for username, why in await nodecredit.take_pending_disconnects(node.id):
                            log.info("node %d: disconnecting %s (%s)", node.id, username, why)
                            yield nodehub_pb2.HubMessage(
                                disconnect=nodehub_pb2.Disconnect(
                                    username=username, reason=why
                                )
                            )
                        # The panel writes a timestamp when an account changes;
                        # this is the only channel between the two processes.
                        if sync_requested is not None and (
                            last_sync_at is None or sync_requested > last_sync_at
                        ):
                            sync_id = next(_sync_ids)
                            log.info("node %d: pushing user sync %d", node.id, sync_id)
                            yield nodehub_pb2.HubMessage(
                                user_sync=await nodecredit.build_sync(node.id, sync_id)
                            )
                            last_sync_at = datetime.now(timezone.utc)
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
    tls = _tls_enabled()
    if tls:
        server.add_secure_port(addr, _server_credentials())
    else:
        if not addr.startswith(("127.0.0.1:", "localhost:", "[::1]:")):
            raise RuntimeError(
                f"refusing to listen on {addr} without TLS; "
                "set NODEHUB_TLS=1 or bind loopback"
            )
        server.add_insecure_port(addr)
    await server.start()
    log.info(
        "nodehub listening on %s (%s, protocol v%d, report interval %ds)",
        addr, "mTLS" if tls else "PLAINTEXT loopback",
        PROTOCOL_VERSION, REPORT_INTERVAL_SECONDS,
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
