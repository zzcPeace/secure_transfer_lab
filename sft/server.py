"""Bounded asyncio TLS server with pre-handshake admission control."""

import asyncio
import errno
import hashlib
import json
import logging
import socket
import sys
import time
import uuid
from collections import Counter

from . import __version__
from .config import ServerConfig
from .protocol import (
    CHUNK_SIZE,
    ProtocolError,
    close_writer,
    configure_listener,
    enable_keepalive,
    read_frame,
    write_frame,
)
from .security import RateGate, ServiceError, TokenBucket, integer, object_id
from .storage import Storage

log = logging.getLogger("sft.server")


class TransferServer:
    def __init__(self, config: ServerConfig):
        self.config = config
        self.storage = Storage(config.root, config.max_file_bytes)
        self.tls = config.tls_context()
        self.listener = None
        self.accept_task = None
        self.tasks = set()
        self.per_ip = Counter()
        self.per_user = Counter()
        self.connections = 0
        self.total_connections = 0
        self.rejected_connections = 0
        self.connection_gate = RateGate(config.connection_rate)
        self.request_gate = RateGate(config.request_rate)
        self.buckets = {}
        self.port = config.port
        self.closed = False

    async def start(self):
        family = socket.AF_INET6 if ":" in self.config.host else socket.AF_INET
        self.listener = socket.socket(family, socket.SOCK_STREAM)
        configure_listener(self.listener)
        self.listener.setblocking(False)
        try:
            self.listener.bind((self.config.host, self.config.port))
        except OSError as exc:
            self.listener.close()
            self.listener = None
            if exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", None) in (10013, 10048):
                inspect_command = (
                    f"Get-NetTCPConnection -LocalPort {self.config.port} -State Listen"
                    if sys.platform == "win32"
                    else f"ss -ltnp 'sport = :{self.config.port}'"
                )
                raise RuntimeError(
                    f"服务端地址 {self.config.host}:{self.config.port} 已被占用。\n"
                    f"请用 {inspect_command} 查看；"
                    "若已有本项目服务端，直接连接即可。\n"
                    "若是其他程序，请同时修改 server.toml 和客户端 client.json 的 port，"
                    "不要重复启动或关闭不相关进程。"
                ) from exc
            raise
        self.listener.listen(min(self.config.max_connections, 128))
        self.port = self.listener.getsockname()[1]
        self.accept_task = asyncio.create_task(self._accept_loop(), name="sft-accept")
        log.info(
            "Listening on %s:%s (TLS 1.3, mandatory client certificate)",
            self.config.host,
            self.port,
        )
        return self

    async def stop(self):
        if self.closed:
            return
        self.closed = True
        if self.accept_task:
            self.accept_task.cancel()
            await asyncio.gather(self.accept_task, return_exceptions=True)
        if self.listener:
            self.listener.close()
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Wait for any disk worker holding the lock before closing SQLite.
        await asyncio.to_thread(self.storage.close)

    async def _accept_loop(self):
        loop = asyncio.get_running_loop()
        while True:
            sock, address = await loop.sock_accept(self.listener)
            ip = address[0]
            if (
                self.connections >= self.config.max_connections
                or self.per_ip[ip] >= self.config.max_per_ip
                or not self.connection_gate.allow(ip)
            ):
                self.rejected_connections += 1
                sock.close()
                continue
            self.connections += 1
            self.total_connections += 1
            self.per_ip[ip] += 1
            task = asyncio.create_task(self._session(sock, ip), name="sft-peer")
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    def _bucket(self, user, direction):
        key = (user["username"], direction)
        rate = user[direction + "_bps"]
        if key not in self.buckets:
            self.buckets[key] = TokenBucket(rate)
        self.buckets[key].rate = rate
        return self.buckets[key]

    @staticmethod
    def _audit(event, **values):
        log.info(json.dumps({"event": event, **values}, ensure_ascii=True))

    async def _session(self, sock, ip):
        writer = None
        username = None
        counted_user = False
        try:
            enable_keepalive(sock)
            reader = asyncio.StreamReader(limit=2 * CHUNK_SIZE)
            protocol = asyncio.StreamReaderProtocol(reader)
            loop = asyncio.get_running_loop()
            transport, _ = await loop.connect_accepted_socket(
                lambda: protocol,
                sock,
                ssl=self.tls,
                ssl_handshake_timeout=self.config.handshake_timeout,
                ssl_shutdown_timeout=3,
            )
            writer = asyncio.StreamWriter(transport, protocol, reader, loop)
            der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
            fingerprint = hashlib.sha256(der).hexdigest()
            user = await asyncio.to_thread(self.storage.user_for_fingerprint, fingerprint)
            if user is None:
                self._audit("authentication_rejected", ip=ip, fingerprint=fingerprint[:12])
                return
            username = user["username"]
            if self.per_user[username] >= self.config.max_per_user:
                self._audit("user_connection_limit", username=username)
                return
            self.per_user[username] += 1
            counted_user = True
            self._audit("authenticated", username=username, ip=ip)
            state = {"credit": None}
            while True:
                frame = await read_frame(reader, timeout=self.config.idle_timeout)
                request_id = object_id(frame.meta.get("id"))
                op = frame.meta.get("op")
                if not isinstance(op, str) or len(op) > 32:
                    raise ProtocolError("Invalid operation")
                if frame.payload and op != "PUT_CHUNK":
                    raise ProtocolError("Unexpected payload")
                if not self.request_gate.allow(username):
                    await write_frame(
                        writer,
                        {
                            "id": request_id,
                            "op": "ERROR",
                            "code": "RATE_LIMIT",
                            "message": "Request rate exceeded",
                        },
                    )
                    break
                try:
                    # Disabling an account also invalidates already-open sessions.
                    user = await asyncio.to_thread(self.storage.get_user, username)
                    response, payload = await self._dispatch(user, frame, state)
                    await write_frame(writer, {"id": request_id, "op": "OK", **response}, payload)
                    if op in ("PUT_BEGIN", "PUT_END", "PUT_ABORT", "DELETE"):
                        self._audit(op, username=username, result="ok")
                    if op == "QUIT":
                        break
                except ServiceError as exc:
                    await write_frame(
                        writer,
                        {"id": request_id, "op": "ERROR", "code": exc.code, "message": exc.message},
                    )
                    self._audit("request_rejected", username=username, operation=op, code=exc.code)
                    if exc.code == "UNAUTHORIZED":
                        break
                except OSError:
                    log.exception("Storage error for %s", username)
                    await write_frame(
                        writer,
                        {
                            "id": request_id,
                            "op": "ERROR",
                            "code": "STORAGE",
                            "message": "Storage operation failed",
                        },
                    )
        except (
            OSError,
            TimeoutError,
            asyncio.IncompleteReadError,
            ProtocolError,
            ServiceError,
        ) as exc:
            log.debug("Connection ended: %s", type(exc).__name__)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Unexpected session error")
        finally:
            await close_writer(writer)
            if writer is None:
                sock.close()
            self.connections -= 1
            self.per_ip[ip] -= 1
            if not self.per_ip[ip]:
                del self.per_ip[ip]
            if counted_user:
                self.per_user[username] -= 1
                if not self.per_user[username]:
                    del self.per_user[username]

    async def _dispatch(self, user, frame, state):
        meta, payload = frame.meta, frame.payload
        op = meta["op"]
        username = user["username"]
        store = self.storage
        if op == "HELLO":
            return {
                "version": __version__,
                "user": username,
                "role": user["role"],
                "upload_bps": user["upload_bps"],
                "download_bps": user["download_bps"],
                "quota_bytes": user["quota_bytes"],
                "chunk_size": CHUNK_SIZE,
                "max_file_bytes": self.config.max_file_bytes,
            }, b""
        if op == "PING":
            return {"pong": True}, b""
        if op == "QUIT":
            return {"bye": True}, b""
        if op == "LIST":
            return await asyncio.to_thread(
                store.list_files, username, meta.get("cursor", ""), meta.get("limit", 25)
            ), b""
        if op == "GET_INFO":
            return {
                "file": await asyncio.to_thread(store.get_info, username, meta.get("file_id"))
            }, b""
        if op == "PUT_BEGIN":
            state["credit"] = None
            return await asyncio.to_thread(
                store.begin_upload, username, meta.get("name"), meta.get("size"), meta.get("sha256")
            ), b""
        if op == "PUT_CREDIT":
            row = await asyncio.to_thread(store.upload_info, username, meta.get("transfer_id"))
            offset = integer(meta.get("offset"), "offset")
            requested = integer(meta.get("length"), "length", 1, CHUNK_SIZE)
            if offset != row["offset"]:
                raise ServiceError("OFFSET", "Upload offset is stale; resume the upload")
            length = min(requested, user["upload_bps"], row["size"] - offset)
            if length <= 0:
                raise ServiceError("BAD_REQUEST", "Upload has no remaining data")
            # Grant permission BEFORE receiving a chunk. All this user's channels
            # share one upload bucket, so additional connections do not bypass it.
            await self._bucket(user, "upload").acquire(length)
            ticket = uuid.uuid4().hex
            state["credit"] = (row["id"], offset, length, ticket, time.monotonic() + 30)
            return {"ticket": ticket, "length": length}, b""
        if op == "PUT_CHUNK":
            credit = state["credit"]
            state["credit"] = None  # one use, even if validation fails
            if (
                credit is None
                or meta.get("transfer_id") != credit[0]
                or type(meta.get("offset")) is not int
                or meta["offset"] != credit[1]
                or len(payload) != credit[2]
                or meta.get("ticket") != credit[3]
                or time.monotonic() > credit[4]
            ):
                raise ServiceError("CREDIT", "A valid, connection-bound upload credit is required")
            return await asyncio.to_thread(
                store.write_chunk, username, credit[0], credit[1], payload
            ), b""
        if op == "PUT_END":
            return {
                "file": await asyncio.to_thread(
                    store.finish_upload, username, meta.get("transfer_id")
                )
            }, b""
        if op == "PUT_ABORT":
            state["credit"] = None
            return await asyncio.to_thread(
                store.abort_upload, username, meta.get("transfer_id")
            ), b""
        if op == "GET_CHUNK":
            info = await asyncio.to_thread(store.get_info, username, meta.get("file_id"))
            offset = integer(meta.get("offset"), "offset", 0, info["size"])
            length = min(
                integer(meta.get("length"), "length", 1, CHUNK_SIZE),
                user["download_bps"],
                info["size"] - offset,
            )
            await self._bucket(user, "download").acquire(length)
            data = await asyncio.to_thread(
                store.read_chunk, username, info["id"], offset, max(1, length)
            )
            return {"offset": offset, "length": len(data)}, data
        if op == "DELETE":
            return await asyncio.to_thread(store.delete_file, username, meta.get("file_id")), b""
        raise ServiceError("BAD_REQUEST", "Unknown operation")
