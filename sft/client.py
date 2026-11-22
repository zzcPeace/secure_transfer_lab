"""Headless client shared by CLI and GUI; one control + reusable data channels."""

import asyncio
import errno
import hashlib
import json
import os
import stat
import uuid
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from .config import ClientConfig
from .protocol import (
    CHUNK_SIZE,
    ProtocolError,
    RemoteError,
    close_writer,
    enable_keepalive,
    read_frame,
    write_frame,
)
from .security import file_hash, filename_value, fsync_directory, open_private

NETWORK_ERRORS = (OSError, TimeoutError, asyncio.IncompleteReadError)


class TransferPaused(Exception):
    """Data and checkpoints are retained; invoking the same transfer resumes it."""


async def disk_operation(function, *args):
    """Do not release a destination/file handle while a cancelled disk write is running."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


def _report(callback, **values):
    if callback:
        callback(values)


def _check_pause(pause):
    if pause is not None and pause.is_set():
        raise TransferPaused("Transfer paused; committed data has been retained")


@contextmanager
def destination_lock(destination):
    """Cross-process no-wait lock; keep its inode so no replacement-lock race occurs."""
    lock_path = destination.with_name("." + destination.name + ".sft-lock")
    fd = open_private(lock_path, os.O_RDWR | os.O_CREAT)
    locked = False
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"0")
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise ValueError("Another process is downloading to this destination") from exc
            raise
        locked = True
        yield
    finally:
        if locked and os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        os.close(fd)


def _source_info(path, max_size):
    with os.fdopen(open_private(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Only regular files can be uploaded")
        if before.st_size > max_size:
            raise ValueError("File exceeds the server's maximum file size")
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("Source file changed while hashing")
        return after.st_size, digest


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Checkpoint cannot be a symlink")
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with os.fdopen(
            open_private(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL), "w", encoding="utf-8"
        ) as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        fsync_directory(path.parent)
    finally:
        temp.unlink(missing_ok=True)


def _prepare_download(part, metadata_path, expected):
    if part.is_symlink() or metadata_path.is_symlink():
        raise ValueError("Download checkpoint cannot be a symlink")
    if metadata_path.exists():
        if metadata_path.stat().st_size > 8192:
            raise ValueError("Invalid checkpoint")
        saved = json.loads(metadata_path.read_text("utf-8"))
        if any(saved.get(k) != v for k, v in expected.items()):
            raise ValueError("Existing partial download belongs to a different file or server")
        offset = saved.get("offset", 0)
        if type(offset) is not int or not 0 <= offset <= expected["size"]:
            raise ValueError("Invalid download offset")
    elif part.exists():
        raise FileExistsError("Partial file without metadata; choose a different destination")
    else:
        offset = 0
    with os.fdopen(open_private(part, os.O_RDWR | os.O_CREAT), "r+b") as stream:
        offset = min(offset, os.fstat(stream.fileno()).st_size)
        stream.truncate(offset)
        stream.flush()
        os.fsync(stream.fileno())
    atomic_json(metadata_path, {**expected, "offset": offset})
    return offset


def _save_download_chunk(part, metadata_path, expected, offset, payload):
    with os.fdopen(open_private(part, os.O_RDWR), "r+b") as stream:
        stream.seek(offset)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    offset += len(payload)
    atomic_json(metadata_path, {**expected, "offset": offset})
    return offset


class Channel:
    def __init__(self, config, tls):
        self.config = config
        self.tls = tls
        self.reader = self.writer = None
        self.lock = asyncio.Lock()
        self.identity = None
        self.connection_count = 0

    async def _connect(self):
        if self.writer is not None and not self.writer.is_closing():
            return
        async with asyncio.timeout(self.config.timeout):
            self.reader, self.writer = await asyncio.open_connection(
                self.config.host,
                self.config.port,
                ssl=self.tls,
                server_hostname=self.config.server_name,
                ssl_handshake_timeout=5,
                ssl_shutdown_timeout=3,
                limit=2 * CHUNK_SIZE,
            )
        enable_keepalive(self.writer.get_extra_info("socket"))
        self.connection_count += 1
        self.identity = (await self._exchange("HELLO"))[0]

    async def _exchange(self, op, payload=b"", **meta):
        request_id = uuid.uuid4().hex
        await write_frame(
            self.writer, {**meta, "op": op, "id": request_id}, payload, self.config.timeout
        )
        frame = await read_frame(self.reader, self.config.timeout)
        if frame.meta.get("id") != request_id:
            raise ProtocolError("Response request ID mismatch")
        if frame.meta.get("op") == "ERROR":
            raise RemoteError(
                frame.meta.get("code", "ERROR"), frame.meta.get("message", "Request failed")
            )
        if frame.meta.get("op") != "OK":
            raise ProtocolError("Invalid response operation")
        return frame.meta, frame.payload

    async def start(self):
        async with self.lock:
            try:
                await self._connect()
                return self.identity
            except BaseException:
                await self.close()
                raise

    async def request(self, op, payload=b"", **meta):
        async with self.lock:
            try:
                await self._connect()
                return await self._exchange(op, payload, **meta)
            except RemoteError:
                raise  # Frame was fully consumed; the channel remains in sync.
            except BaseException:
                await self.close()
                raise

    async def close(self):
        writer, self.writer = self.writer, None
        self.reader = None
        await close_writer(writer)


class TransferClient:
    def __init__(self, config: ClientConfig):
        self.config = config
        self.tls = config.tls_context()
        self.control = Channel(config, self.tls)
        self.channels = [Channel(config, self.tls) for _ in range(config.pool_size)]
        self.available = asyncio.Queue()
        for channel in self.channels:
            self.available.put_nowait(channel)
        self.heartbeat_task = None
        self.identity = None
        self.active_downloads = set()

    async def connect(self):
        try:
            self.identity = await self.control.start()
            for channel in self.channels:
                await channel.start()
        except BaseException:
            await self.close()
            raise
        if self.heartbeat_task is None:
            self.heartbeat_task = asyncio.create_task(self._heartbeat(), name="sft-heartbeats")
        return self.identity

    async def close(self):
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            await asyncio.gather(self.heartbeat_task, return_exceptions=True)
            self.heartbeat_task = None
        await asyncio.gather(*(channel.close() for channel in [self.control, *self.channels]))

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def _heartbeat(self):
        while True:
            await asyncio.sleep(self.config.heartbeat_seconds)
            for channel in [self.control, *self.channels]:
                if not channel.lock.locked():
                    try:
                        await channel.request("PING")
                    except (
                        OSError,
                        TimeoutError,
                        asyncio.IncompleteReadError,
                        RemoteError,
                        ProtocolError,
                    ):
                        pass  # Data operations expose errors and have bounded reconnect retries.

    @asynccontextmanager
    async def data_channel(self):
        channel = await self.available.get()
        try:
            yield channel
        finally:
            self.available.put_nowait(channel)

    async def list_files(self):
        files, cursor = [], ""
        while True:
            reply, _ = await self.control.request("LIST", cursor=cursor)
            files.extend(reply["files"])
            cursor = reply["next_cursor"]
            if not cursor:
                return files

    async def delete_file(self, file_id):
        return (await self.control.request("DELETE", file_id=file_id))[0]

    async def abort_upload(self, transfer_id):
        return (await self.control.request("PUT_ABORT", transfer_id=transfer_id))[0]

    async def upload(self, source, *, name=None, progress=None, pause=None):
        source = Path(source).absolute()
        name = filename_value(name or source.name)
        _report(progress, status="hashing", done=0, total=0, name=name)
        if self.identity is None:
            raise RuntimeError("Connect the client before starting transfers")
        size, digest = await disk_operation(_source_info, source, self.identity["max_file_bytes"])
        _check_pause(pause)
        _report(progress, status="queued", done=0, total=size, name=name)
        async with self.data_channel() as channel:
            for attempt in range(4):
                _check_pause(pause)
                try:
                    reply, _ = await channel.request(
                        "PUT_BEGIN", name=name, size=size, sha256=digest
                    )
                    if reply["complete"]:
                        _report(
                            progress,
                            status="completed",
                            done=size,
                            total=size,
                            file_id=reply["file"]["id"],
                        )
                        return reply["file"]
                    transfer_id, offset = reply["transfer_id"], reply["offset"]
                    _report(
                        progress,
                        status="transferring",
                        done=offset,
                        total=size,
                        transfer_id=transfer_id,
                    )
                    with os.fdopen(open_private(source, os.O_RDONLY), "rb") as stream:
                        stream.seek(offset)
                        while offset < size:
                            _check_pause(pause)
                            credit, _ = await channel.request(
                                "PUT_CREDIT",
                                transfer_id=transfer_id,
                                offset=offset,
                                length=min(CHUNK_SIZE, size - offset),
                            )
                            length = credit["length"]
                            if type(length) is not int or not 0 < length <= min(
                                CHUNK_SIZE, size - offset
                            ):
                                raise ProtocolError("Invalid upload credit")
                            data = await disk_operation(stream.read, length)
                            if len(data) != length:
                                raise ValueError("Source file changed during upload")
                            ack, _ = await channel.request(
                                "PUT_CHUNK",
                                data,
                                transfer_id=transfer_id,
                                offset=offset,
                                ticket=credit["ticket"],
                            )
                            if ack.get("offset") != offset + length:
                                raise ProtocolError("Invalid committed upload offset")
                            offset += length
                            _report(
                                progress,
                                status="transferring",
                                done=offset,
                                total=size,
                                transfer_id=transfer_id,
                            )
                    _check_pause(pause)
                    _report(progress, status="verifying", done=size, total=size)
                    result, _ = await channel.request("PUT_END", transfer_id=transfer_id)
                    _report(
                        progress,
                        status="completed",
                        done=size,
                        total=size,
                        file_id=result["file"]["id"],
                    )
                    return result["file"]
                except RemoteError as exc:
                    if exc.code != "OFFSET" or attempt == 3:
                        raise
                    await asyncio.sleep(0.2)
                except NETWORK_ERRORS:
                    if attempt == 3:
                        raise
                    _report(
                        progress,
                        status="reconnecting",
                        message=f"Reconnect attempt {attempt + 1}/3",
                    )
                    await asyncio.sleep(0.5 * 2**attempt)
        raise RuntimeError("Upload retry budget exhausted")

    async def download(self, file_id, destination, *, progress=None, pause=None):
        destination = Path(destination).absolute()
        key = os.path.normcase(str(destination.resolve()))
        if key in self.active_downloads:
            raise ValueError("Another task is already writing this destination")
        self.active_downloads.add(key)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination_lock(destination):
                return await self._download(file_id, destination, progress, pause)
        finally:
            self.active_downloads.remove(key)

    async def _download(self, file_id, destination, progress, pause):
        destination.parent.mkdir(parents=True, exist_ok=True)
        part = destination.with_name(destination.name + ".sft-part")
        metadata_path = destination.with_name(destination.name + ".sft-meta.json")
        _report(progress, status="queued", done=0, total=0, file_id=file_id)
        async with self.data_channel() as channel:
            info = (await channel.request("GET_INFO", file_id=file_id))[0]["file"]
            if destination.exists():
                if (
                    destination.is_file()
                    and await disk_operation(file_hash, destination) == info["sha256"]
                ):
                    _report(progress, status="completed", done=info["size"], total=info["size"])
                    return info
                raise FileExistsError("Destination exists; choose a different path")
            expected = {
                "file_id": file_id,
                "sha256": info["sha256"],
                "size": info["size"],
                "server": self.config.server_name,
                "port": self.config.port,
                "username": self.identity["user"],
            }
            offset = await disk_operation(_prepare_download, part, metadata_path, expected)
            _report(progress, status="transferring", done=offset, total=info["size"])
            for attempt in range(4):
                try:
                    while offset < info["size"]:
                        _check_pause(pause)
                        header, data = await channel.request(
                            "GET_CHUNK",
                            file_id=file_id,
                            offset=offset,
                            length=min(CHUNK_SIZE, info["size"] - offset),
                        )
                        if (
                            header.get("offset") != offset
                            or not data
                            or len(data) != header.get("length")
                            or offset + len(data) > info["size"]
                        ):
                            raise ProtocolError("Invalid download chunk")
                        offset = await disk_operation(
                            _save_download_chunk, part, metadata_path, expected, offset, data
                        )
                        _report(progress, status="transferring", done=offset, total=info["size"])
                    break
                except NETWORK_ERRORS:
                    if attempt == 3:
                        raise
                    _report(
                        progress,
                        status="reconnecting",
                        message=f"Reconnect attempt {attempt + 1}/3",
                    )
                    await asyncio.sleep(0.5 * 2**attempt)
            _check_pause(pause)
            _report(progress, status="verifying", done=offset, total=info["size"])
            if await disk_operation(file_hash, part) != info["sha256"]:
                # Reset the checkpoint so Retry actually recovers from local corruption.
                await disk_operation(atomic_json, metadata_path, {**expected, "offset": 0})
                raise ValueError("Downloaded SHA-256 mismatch; retry will restart from byte zero")
            # Refuse overwrites, including a file created while this download ran.
            # Hard-link publication is atomic/no-clobber on NTFS and normal Unix filesystems.
            await disk_operation(os.link, part, destination)
            await disk_operation(part.unlink)
            await disk_operation(metadata_path.unlink)
            await disk_operation(fsync_directory, destination.parent)
            _report(progress, status="completed", done=offset, total=info["size"])
            return info
