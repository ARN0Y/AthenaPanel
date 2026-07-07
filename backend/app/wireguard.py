"""WireGuard peer management for the `wg-panel` interface.

The panel is the source of truth (the wg_peers table). Peers are applied to the
LIVE interface with `wg set` and re-synced on startup, so wg-panel.conf stays
static ([Interface] + PostUp NAT only) and we never risk `wg-quick save`
clobbering the NAT/FORWARD rules. The panel runs as root on the same host as the
interface, so it shells out to `wg` directly.

Client configs hand out the ENTRY-RELAY endpoint (settings/app_settings
wg_endpoint), never 106 or the overseas IP — the relay is the only public face.
"""

import asyncio
import io
import ipaddress
import os
import tempfile

from .config import settings

IFACE = settings.wg_iface


async def _run(*args: str, stdin: bytes | None = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(stdin)
    return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def iface_up() -> bool:
    return os.path.isdir(f"/sys/class/net/{IFACE}")


async def gen_keypair() -> tuple[str, str]:
    _rc, priv, _ = await _run("wg", "genkey")
    priv = priv.strip()
    _rc, pub, _ = await _run("wg", "pubkey", stdin=(priv + "\n").encode())
    return priv, pub.strip()


async def gen_psk() -> str:
    _rc, psk, _ = await _run("wg", "genpsk")
    return psk.strip()


async def server_pubkey() -> str:
    """The wg-panel server public key (auto-discovered from the live iface)."""
    _rc, out, _ = await _run("wg", "show", IFACE, "public-key")
    return out.strip()


async def add_peer(public_key: str, preshared_key: str, address: str) -> bool:
    """Apply a peer to the live interface (allowed-ips pins it to its /32)."""
    args = ["wg", "set", IFACE, "peer", public_key]
    psk_file = None
    try:
        if preshared_key:
            fd, psk_file = tempfile.mkstemp(prefix=".wgpsk.")
            os.write(fd, (preshared_key + "\n").encode())
            os.close(fd)
            args += ["preshared-key", psk_file]
        args += ["allowed-ips", f"{address}/32"]
        rc, _out, _err = await _run(*args)
        return rc == 0
    finally:
        if psk_file and os.path.exists(psk_file):
            os.unlink(psk_file)


async def remove_peer(public_key: str) -> bool:
    rc, _o, _e = await _run("wg", "set", IFACE, "peer", public_key, "remove")
    return rc == 0


async def show_dump() -> dict[str, dict]:
    """pubkey -> {rx, tx, handshake(epoch)} from `wg show <iface> dump`.

    dump peer columns: pubkey, psk, endpoint, allowed-ips, latest-handshake,
    rx, tx, keepalive (tab-separated). First line is the interface, skip it.
    """
    rc, out, _ = await _run("wg", "show", IFACE, "dump")
    peers: dict[str, dict] = {}
    if rc != 0:
        return peers
    for line in out.splitlines()[1:]:
        f = line.split("\t")
        if len(f) >= 8:
            try:
                peers[f[0]] = {"handshake": int(f[4]), "rx": int(f[5]), "tx": int(f[6])}
            except ValueError:
                continue
    return peers


def allocate_address(used: set[str]) -> str:
    """First free address in the pool, skipping the gateway (.1)."""
    net = ipaddress.ip_network(settings.wg_pool, strict=False)
    gw = next(net.hosts())
    for host in net.hosts():
        if host == gw:
            continue
        if str(host) not in used:
            return str(host)
    raise RuntimeError("WireGuard address pool exhausted")


def client_config(*, private_key: str, address: str, server_pub: str,
                  preshared_key: str, endpoint: str, dns: str, mtu: int) -> str:
    psk_line = f"PresharedKey = {preshared_key}\n" if preshared_key else ""
    return (
        "[Interface]\n"
        f"PrivateKey = {private_key}\n"
        f"Address = {address}/32\n"
        f"DNS = {dns}\n"
        f"MTU = {mtu}\n\n"
        "[Peer]\n"
        f"PublicKey = {server_pub}\n"
        f"{psk_line}"
        f"Endpoint = {endpoint}\n"
        "AllowedIPs = 0.0.0.0/0, ::/0\n"
        "PersistentKeepalive = 25\n"
    )


def qr_svg(data: str) -> str:
    """QR of the client config as an SVG string (pure-python, no Pillow)."""
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    svg = buf.getvalue().decode("utf-8")
    # strip the XML declaration so the SVG renders inline (innerHTML) in the UI
    if svg.lstrip().startswith("<?xml"):
        svg = svg[svg.index("?>") + 2:].lstrip()
    return svg


async def sync_from_db(peers) -> int:
    """Re-apply DB peers to the live interface (used on startup so a server/panel
    restart restores all peers). `peers` is an iterable of (public_key,
    preshared_key, address, enabled). Disabled peers are removed."""
    if not iface_up():
        return 0
    n = 0
    for public_key, preshared_key, address, enabled in peers:
        if enabled:
            if await add_peer(public_key, preshared_key, address):
                n += 1
        else:
            await remove_peer(public_key)
    return n
