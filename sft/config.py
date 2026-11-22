import json
import ssl
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


def _path(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).absolute()


@dataclass
class ServerConfig:
    root: Path
    ca: Path
    cert: Path
    key: Path
    host: str = "127.0.0.1"
    port: int = 18765
    max_connections: int = 64
    max_per_ip: int = 24
    max_per_user: int = 8
    max_file_bytes: int = 256 * 1024 * 1024
    handshake_timeout: float = 5
    idle_timeout: float = 60
    request_rate: int = 500
    connection_rate: int = 20

    def __post_init__(self):
        if not 0 <= self.port <= 65535:
            raise ValueError("Invalid server port")
        for name in (
            "max_connections",
            "max_per_ip",
            "max_per_user",
            "max_file_bytes",
            "handshake_timeout",
            "idle_timeout",
            "request_rate",
            "connection_rate",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")

    @classmethod
    def load(cls, filename):
        path = Path(filename).absolute()
        values = tomllib.loads(path.read_text("utf-8"))
        allowed = {field.name for field in fields(cls)}
        if values.keys() - allowed:
            raise ValueError(f"Unknown configuration keys: {values.keys() - allowed}")
        for name in ("root", "ca", "cert", "key"):
            values[name] = _path(path.parent, values[name])
        return cls(**values)

    def tls_context(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_verify_locations(self.ca)
        context.load_cert_chain(self.cert, self.key)
        context.num_tickets = 0
        return context


@dataclass
class ClientConfig:
    host: str
    port: int
    server_name: str
    ca: Path
    cert: Path
    key: Path
    state_dir: Path
    pool_size: int = 3
    heartbeat_seconds: float = 15
    timeout: float = 60

    def __post_init__(self):
        if not 1 <= self.pool_size <= 6:
            raise ValueError("pool_size must be between 1 and 6")
        if not 0 < self.port <= 65535 or self.timeout <= 0 or self.heartbeat_seconds <= 0:
            raise ValueError("Invalid port or timeout")

    @classmethod
    def load(cls, filename):
        path = Path(filename).absolute()
        values = json.loads(path.read_text("utf-8"))
        for name in ("ca", "cert", "key", "state_dir"):
            values[name] = _path(path.parent, values[name])
        return cls(**values)

    def tls_context(self):
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=self.ca)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_cert_chain(self.cert, self.key)
        return context
