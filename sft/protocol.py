"""Bounded, versioned framing over a persistent TLS byte stream.

13-byte fixed header: magic[4], version[1], JSON length[4], payload length[4].
All integers use network byte order. File data is never base64-encoded.
"""

import asyncio
import json
import os
import socket
import struct
from dataclasses import dataclass

MAGIC = b"SFT1"
VERSION = 1
HEADER = struct.Struct("!4sBII")
MAX_META = 16 * 1024
CHUNK_SIZE = 64 * 1024


class ProtocolError(Exception):
    pass


class RemoteError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class Frame:
    meta: dict
    payload: bytes = b""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("Duplicate JSON key")
        result[key] = value
    return result


def encode_frame(meta: dict, payload: bytes = b"") -> bytes:
    data = json.dumps(meta, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    if not 0 < len(data) <= MAX_META or len(payload) > CHUNK_SIZE:
        raise ProtocolError("Frame exceeds size limits")
    return HEADER.pack(MAGIC, VERSION, len(data), len(payload)) + data + payload


async def read_frame(reader: asyncio.StreamReader, timeout: float = 60) -> Frame:
    # One deadline covers the entire frame, so drip-fed data cannot reset the timeout.
    async with asyncio.timeout(timeout):
        header = await reader.readexactly(HEADER.size)
        magic, version, meta_size, payload_size = HEADER.unpack(header)
        if magic != MAGIC or version != VERSION:
            raise ProtocolError("Unsupported magic or protocol version")
        if not 0 < meta_size <= MAX_META or payload_size > CHUNK_SIZE:
            raise ProtocolError("Invalid frame length")
        try:
            meta = json.loads(
                await reader.readexactly(meta_size),
                object_pairs_hook=_unique_object,
                parse_constant=lambda value: (_ for _ in ()).throw(ProtocolError("Invalid number")),
            )
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise ProtocolError("Invalid JSON metadata") from exc
        if not isinstance(meta, dict):
            raise ProtocolError("Metadata must be an object")
        payload = await reader.readexactly(payload_size)
        return Frame(meta, payload)


async def write_frame(writer, meta: dict, payload: bytes = b"", timeout: float = 30):
    writer.write(encode_frame(meta, payload))
    async with asyncio.timeout(timeout):
        await writer.drain()


def enable_keepalive(sock):
    """OS TCP keepalive complements the application PING/PONG heartbeat."""
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    for option, value in (("TCP_KEEPIDLE", 30), ("TCP_KEEPINTVL", 10), ("TCP_KEEPCNT", 3)):
        if hasattr(socket, option):
            try:
                sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, option), value)
            except OSError:
                pass


def configure_listener(sock):
    # Windows SO_REUSEADDR can allow a second server to hijack a bound address.
    option = socket.SO_EXCLUSIVEADDRUSE if os.name == "nt" else socket.SO_REUSEADDR
    sock.setsockopt(socket.SOL_SOCKET, option, 1)


async def close_writer(writer):
    if writer is not None:
        writer.close()
        try:
            async with asyncio.timeout(3):
                await writer.wait_closed()
        except (OSError, TimeoutError):
            pass
