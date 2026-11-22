"""Offline lab CA and per-user client profiles. No private key is bundled in source."""

import datetime as dt
import ipaddress
import json
import os
import shutil
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .config import ClientConfig, ServerConfig
from .security import filename_value, private_directory, username_value
from .storage import Storage


def _write(path: Path, data: bytes, private=False):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600 if private else 0o644)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)


def _key_bytes(key):
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def _cert_bytes(cert):
    return cert.public_bytes(serialization.Encoding.PEM)


def make_ca():
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SFT Lab Offline CA")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, None, None), critical=True
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def issue_certificate(ca_key, ca_cert, name, *, server=False, hosts=()):
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=90))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, False, False, None, None), critical=True
        )
        .add_extension(
            x509.ExtendedKeyUsage(
                [ExtendedKeyUsageOID.SERVER_AUTH if server else ExtendedKeyUsageOID.CLIENT_AUTH]
            ),
            critical=False,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
    )
    if server:
        sans = []
        for host in dict.fromkeys(hosts):
            try:
                sans.append(x509.IPAddress(ipaddress.ip_address(host)))
            except ValueError:
                sans.append(x509.DNSName(host))
        builder = builder.add_extension(x509.SubjectAlternativeName(sans), critical=False)
    return key, builder.sign(ca_key, hashes.SHA256())


def _write_client_profile(directory, ca_key, ca_cert, username, profile):
    key, cert = issue_certificate(ca_key, ca_cert, username)
    _write(directory / "client.key.pem", _key_bytes(key), private=True)
    _write(directory / "client.cert.pem", _cert_bytes(cert))
    _write(directory / "ca.cert.pem", _cert_bytes(ca_cert))
    profile = {
        **profile,
        "ca": "ca.cert.pem",
        "cert": "client.cert.pem",
        "key": "client.key.pem",
        "state_dir": "state",
    }
    _write(directory / "client.json", json.dumps(profile, indent=2).encode())
    return cert


def add_runtime_user(destination, username, *, role="normal", template=None):
    """Provision a distinct certificate/account in an existing lab runtime."""
    username_value(username)
    filename_value(username)  # Also reject reserved Windows directory names.
    if role not in ("normal", "vip"):
        raise ValueError("Invalid role")
    root = Path(destination).absolute()
    server = ServerConfig.load(root / "server/server.toml")
    if not (server.root / "state.sqlite3").is_file():
        raise FileNotFoundError("Existing server database is required; run init-demo first")
    template = Path(template) if template is not None else root / "clients/alice/client.json"
    client = ClientConfig.load(template)
    profile = json.loads(template.read_text("utf-8"))
    authority = root / "authority"
    ca_cert = x509.load_pem_x509_certificate((authority / "ca.cert.pem").read_bytes())
    for ca_path in (server.ca, client.ca):
        expected = x509.load_pem_x509_certificate(ca_path.read_bytes())
        if expected.fingerprint(hashes.SHA256()) != ca_cert.fingerprint(hashes.SHA256()):
            raise ValueError("The authority, server and template must use the same CA")
    ca_key = serialization.load_pem_private_key(
        (authority / "ca.key.pem").read_bytes(), password=None
    )
    public_format = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if ca_key.public_key().public_bytes(*public_format) != ca_cert.public_key().public_bytes(
        *public_format
    ):
        raise ValueError("CA private key does not match the CA certificate")

    directory = root / "clients" / username
    if directory.exists() or directory.is_symlink():
        raise FileExistsError(f"Refusing to overwrite existing client directory: {directory}")
    rate, quota = (512 * 1024, 1024**3) if role == "normal" else (4 * 1024**2, 4 * 1024**3)
    store = Storage(server.root)
    try:
        if any(user["username"] == username for user in store.users()):
            raise ValueError(f"User already exists: {username}")
        private_directory(directory.parent)
        # Exclusive creation also protects against concurrent provisioning.
        directory.mkdir(mode=0o700)
        try:
            cert = _write_client_profile(directory, ca_key, ca_cert, username, profile)
            store.add_user(
                username, cert.fingerprint(hashes.SHA256()).hex(), role, rate, rate, quota
            )
        except Exception:
            # Only this invocation's newly created directory is removed on failure.
            shutil.rmtree(directory)
            raise
    finally:
        store.close()
    return directory / "client.json"


def initialize_demo(destination, *, host="127.0.0.1", port=18765, bind="127.0.0.1"):
    destination = Path(destination).absolute()
    if destination.exists():
        raise FileExistsError("Initialization refuses to overwrite an existing runtime directory")
    if not 1 <= port <= 65535:
        raise ValueError("Invalid port")
    private_directory(destination)
    authority = destination / "authority"
    server = destination / "server"
    for directory in (authority, server):
        private_directory(directory)
    ca_key, ca_cert = make_ca()
    _write(authority / "ca.key.pem", _key_bytes(ca_key), private=True)
    _write(authority / "ca.cert.pem", _cert_bytes(ca_cert))
    key, cert = issue_certificate(
        ca_key, ca_cert, "SFT Server", server=True, hosts=["localhost", "127.0.0.1", "::1", host]
    )
    _write(server / "server.key.pem", _key_bytes(key), private=True)
    _write(server / "server.cert.pem", _cert_bytes(cert))
    _write(server / "ca.cert.pem", _cert_bytes(ca_cert))
    config = f"""# Paths are relative to this configuration file.
root = "data"
ca = "ca.cert.pem"
cert = "server.cert.pem"
key = "server.key.pem"
host = {json.dumps(bind)}
port = {port}
max_connections = 64
max_per_ip = 24
max_per_user = 8
max_file_bytes = 268435456
handshake_timeout = 5
idle_timeout = 60
request_rate = 500
connection_rate = 20
"""
    _write(server / "server.toml", config.encode())
    store = Storage(server / "data")
    try:
        for username, role, rate, quota in (
            ("alice", "normal", 512 * 1024, 1024**3),
            ("bob", "vip", 4 * 1024**2, 4 * 1024**3),
        ):
            directory = destination / "clients" / username
            private_directory(directory)
            profile = {
                "host": host,
                "port": port,
                "server_name": host,
                "pool_size": 3,
                "heartbeat_seconds": 15,
                "timeout": 60,
            }
            cert = _write_client_profile(directory, ca_key, ca_cert, username, profile)
            store.add_user(
                username, cert.fingerprint(hashes.SHA256()).hex(), role, rate, rate, quota
            )
    finally:
        store.close()
    return destination
