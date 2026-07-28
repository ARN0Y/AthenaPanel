"""A private CA for the node control plane.

Deliberately NOT Let's Encrypt. A public CA proves "you reached the host named
X", which is not the question here. The question is "is this our panel, and is
this one of our agents" — and a private CA the panel owns, pinned on both
sides, answers exactly that and nothing else. Concretely it also means:

  * no DNS record and no hostname needed — a node can dial a raw IP
  * no port-80 challenge, so it works on a node where everything is filtered
  * no renewal cliff; we choose the lifetime
  * mutual TLS: the agent proves who it is with a certificate, not only a token
  * a burned IP costs nothing, because nothing is bound to an address

Even someone who can mis-issue from a public CA cannot impersonate either side,
because neither side trusts a public CA for this connection.

Key material lives in PKI_DIR, mode 0600, owned by root. It is part of the
panel's state: losing it means re-issuing every node certificate (recoverable),
leaking it means anyone can join as a node (not recoverable — rotate the CA).
"""

from __future__ import annotations

import datetime as _dt
import ipaddress
import logging
import os
from pathlib import Path

log = logging.getLogger("vpn-panel.pki")

PKI_DIR = Path(os.environ.get("PKI_DIR", "/var/lib/vpn-panel/pki"))
CA_KEY = PKI_DIR / "ca.key"
CA_CRT = PKI_DIR / "ca.crt"
HUB_KEY = PKI_DIR / "hub.key"
HUB_CRT = PKI_DIR / "hub.crt"

# Long-lived on purpose. This is infrastructure the operator controls; an
# expiry surprise at 3am is a worse failure than a long-lived private key kept
# at 0600 on a box that already holds every user's credentials.
CA_DAYS = 3650
HUB_DAYS = 3650
NODE_DAYS = 3650


def _x509():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    return x509, hashes, serialization, ec


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create with the right mode from the start; never widen-then-narrow.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


def _write_public(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o644)


def ca_exists() -> bool:
    return CA_KEY.exists() and CA_CRT.exists()


def ensure_ca() -> None:
    """Create the CA if it is missing. Idempotent; never replaces an existing one."""
    if ca_exists():
        return
    x509, hashes, serialization, ec = _x509()

    key = ec.generate_private_key(ec.SECP256R1())
    now = _dt.datetime.now(_dt.timezone.utc)
    name = x509.Name([
        x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, "Athena Node CA"),
        x509.NameAttribute(x509.oid.NameOID.ORGANIZATION_NAME, "Athena"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(minutes=5))
        .not_valid_after(now + _dt.timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    _write_private(CA_KEY, key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    _write_public(CA_CRT, cert.public_bytes(serialization.Encoding.PEM))
    log.info("created the node CA at %s", CA_CRT)


def _load_ca():
    x509, _h, serialization, _ec = _x509()
    key = serialization.load_pem_private_key(CA_KEY.read_bytes(), password=None)
    cert = x509.load_pem_x509_certificate(CA_CRT.read_bytes())
    return key, cert


def _san_entries(hosts: list[str]):
    """Build SANs, sorting each entry into IP or DNS as appropriate.

    Both matter: a node may be told to dial a bare address or a name, and a
    certificate that only carries one of them fails verification for the other.
    """
    x509, *_ = _x509()
    out = []
    for h in hosts:
        h = (h or "").strip()
        if not h:
            continue
        try:
            out.append(x509.IPAddress(ipaddress.ip_address(h)))
        except ValueError:
            out.append(x509.DNSName(h))
    return out


def issue_hub(hosts: list[str]) -> None:
    """(Re)issue the hub's server certificate for the given addresses.

    Called whenever the set of addresses agents dial changes — adding an
    address is a normal operation, so this overwrites rather than refusing.
    """
    ensure_ca()
    x509, hashes, serialization, ec = _x509()
    ca_key, ca_cert = _load_ca()

    key = ec.generate_private_key(ec.SECP256R1())
    now = _dt.datetime.now(_dt.timezone.utc)
    san = _san_entries(hosts) or [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([
            x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, "athena-hub"),
        ]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(minutes=5))
        .not_valid_after(now + _dt.timedelta(days=HUB_DAYS))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    _write_private(HUB_KEY, key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    _write_public(HUB_CRT, cert.public_bytes(serialization.Encoding.PEM))
    log.info("issued the hub certificate for %s", ", ".join(hosts) or "127.0.0.1")


def issue_node(node_id: int, name: str) -> tuple[str, str, str]:
    """Mint a client certificate for one node.

    Returns (client_cert_pem, client_key_pem, ca_cert_pem). Nothing is written
    to disk: the material goes straight into the node's install bundle, and the
    panel keeps only the CA. The node id lives in the CN so the hub can tell
    which node a connection claims to be before reading a single message.
    """
    ensure_ca()
    x509, hashes, serialization, ec = _x509()
    ca_key, ca_cert = _load_ca()

    key = ec.generate_private_key(ec.SECP256R1())
    now = _dt.datetime.now(_dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([
            x509.NameAttribute(x509.oid.NameOID.COMMON_NAME, f"node-{node_id}"),
            x509.NameAttribute(x509.oid.NameOID.ORGANIZATIONAL_UNIT_NAME, name[:64] or "node"),
        ]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(minutes=5))
        .not_valid_after(now + _dt.timedelta(days=NODE_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM).decode(),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
        CA_CRT.read_text(),
    )


def node_id_from_cert(der: bytes) -> int | None:
    """Read the node id out of a presented client certificate.

    Only ever used to CROSS-CHECK the token the agent sends. The certificate
    says "you are one of ours"; the token says which one. Requiring both means
    a stolen certificate is not enough, and neither is a stolen token.
    """
    x509, *_ = _x509()
    try:
        cert = x509.load_der_x509_certificate(der)
        cn = cert.subject.get_attributes_for_oid(x509.oid.NameOID.COMMON_NAME)
        if not cn:
            return None
        value = str(cn[0].value)
        if not value.startswith("node-"):
            return None
        return int(value[5:])
    except Exception:  # noqa: BLE001
        return None
