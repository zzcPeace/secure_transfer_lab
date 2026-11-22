import asyncio
import hashlib
import os
import re
import time
from collections import OrderedDict
from pathlib import Path


class ServiceError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def integer(value, name, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ServiceError("BAD_REQUEST", f"Invalid {name}")
    return value


def object_id(value):
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{32}", value) is None:
        raise ServiceError("BAD_REQUEST", "Invalid object identifier")
    return value


def digest_value(value):
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise ServiceError("BAD_REQUEST", "Invalid SHA-256 digest")
    return value


def username_value(value):
    if not isinstance(value, str) or re.fullmatch(r"[a-zA-Z0-9_-]{1,32}", value) is None:
        raise ServiceError("BAD_REQUEST", "Username must use 1–32 letters, digits, _ or -")
    return value


def filename_value(value):
    # Display names are never concatenated with server filesystem paths.
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 180
        or value in (".", "..")
        or any(c in value for c in '/\\:*?"<>|')
        or any(ord(c) < 32 for c in value)
        or value.endswith((".", " "))
    ):
        raise ServiceError("BAD_REQUEST", "Invalid or unsafe filename")
    stem = value.split(".")[0].upper()
    if stem in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        raise ServiceError("BAD_REQUEST", "Reserved filename")
    return value


def private_directory(path: Path):
    if path.is_symlink():
        raise ValueError(f"Symlink is not allowed for a private directory: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        path.chmod(0o700)


def open_private(path: Path, flags: int, mode=0o600):
    # Parent directories are private to the service account; O_NOFOLLOW adds a
    # final-component guard on Unix. Windows deployments must apply NTFS ACLs.
    if path.is_symlink():
        raise ServiceError("UNSAFE_PATH", "Symlink is not allowed")
    return os.open(path, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), mode)


def file_hash(path: Path):
    with os.fdopen(open_private(path, os.O_RDONLY), "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fsync_directory(path: Path):
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class TokenBucket:
    """One shared bucket per user and direction, across ALL connections."""

    def __init__(self, rate: int, capacity=64 * 1024):
        self.rate = rate
        self.capacity = capacity
        self.tokens = 0.0
        self.updated = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self, amount):
        if not 0 <= amount <= self.capacity:
            raise ValueError("Amount exceeds bucket capacity")
        async with self.lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= amount:
                    self.tokens -= amount
                    return
                await asyncio.sleep((amount - self.tokens) / self.rate)


class RateGate:
    """Bounded LRU token map for connection/request admission (no awaits)."""

    def __init__(self, rate, burst=None, max_keys=2048):
        self.rate = rate
        self.burst = burst or rate * 2
        self.max_keys = max_keys
        self.entries = OrderedDict()

    def allow(self, key):
        now = time.monotonic()
        tokens, updated = self.entries.pop(key, (float(self.burst), now))
        tokens = min(self.burst, tokens + (now - updated) * self.rate)
        allowed = tokens >= 1
        self.entries[key] = (tokens - 1 if allowed else tokens, now)
        if len(self.entries) > self.max_keys:
            self.entries.popitem(last=False)
        return allowed
