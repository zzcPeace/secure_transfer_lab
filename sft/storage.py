"""SQLite metadata and private, opaque-ID file storage.

Methods run in worker threads. The RLock serializes filesystem/DB transitions;
the asyncio loop never waits synchronously for disk I/O or whole-file hashing.
An upload ACK is sent only after fsync + committed metadata.
"""

import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .protocol import CHUNK_SIZE
from .security import (
    ServiceError,
    digest_value,
    file_hash,
    filename_value,
    fsync_directory,
    integer,
    object_id,
    open_private,
    private_directory,
    username_value,
)


class Storage:
    def __init__(self, root: Path, max_file_bytes=256 * 1024 * 1024):
        self.root = Path(root)
        self.max_file_bytes = max_file_bytes
        self.objects = self.root / "objects"
        self.partials = self.root / "partials"
        for path in (self.root, self.objects, self.partials):
            private_directory(path)
        db_path = self.root / "state.sqlite3"
        if db_path.is_symlink():
            raise ValueError("Database must not be a symlink")
        self.lock = threading.RLock()
        self.db = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
        if os.name == "posix":
            db_path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE,
                role TEXT NOT NULL CHECK(role IN ('normal','vip')),
                upload_bps INTEGER NOT NULL, download_bps INTEGER NOT NULL,
                quota_bytes INTEGER NOT NULL, disabled INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS uploads (
                id TEXT PRIMARY KEY, username TEXT NOT NULL REFERENCES users(username),
                name TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
                offset INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL,
                created REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS uploads_owner ON uploads(username,state,name);
            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY, username TEXT NOT NULL REFERENCES users(username),
                name TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
                created REAL NOT NULL, UNIQUE(username,name)
            );
        """)
        self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    def add_user(
        self,
        username,
        fingerprint,
        role="normal",
        upload_bps=524288,
        download_bps=524288,
        quota_bytes=1024**3,
    ):
        username_value(username)
        digest_value(fingerprint)
        if role not in ("normal", "vip"):
            raise ValueError("Invalid role")
        integer(upload_bps, "upload_bps", 1024, 1024**3)
        integer(download_bps, "download_bps", 1024, 1024**3)
        integer(quota_bytes, "quota_bytes", 0)
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO users VALUES (?,?,?,?,?,?,0)",
                (username, fingerprint, role, upload_bps, download_bps, quota_bytes),
            )

    def user_for_fingerprint(self, fingerprint):
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM users WHERE fingerprint=? AND disabled=0", (fingerprint,)
            ).fetchone()
            return dict(row) if row else None

    def get_user(self, username):
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM users WHERE username=? AND disabled=0", (username,)
            ).fetchone()
            if not row:
                raise ServiceError("UNAUTHORIZED", "Account is disabled or unknown")
            return dict(row)

    def users(self):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM users ORDER BY username")]

    def update_user(self, username, *, disabled=None, upload_bps=None, download_bps=None):
        with self.lock, self.db:
            if not self.db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                raise ServiceError("NOT_FOUND", "Unknown user")
            if disabled is not None:
                self.db.execute(
                    "UPDATE users SET disabled=? WHERE username=?", (int(disabled), username)
                )
            if upload_bps is not None:
                integer(upload_bps, "upload_bps", 1024, 1024**3)
                self.db.execute(
                    "UPDATE users SET upload_bps=? WHERE username=?", (upload_bps, username)
                )
            if download_bps is not None:
                integer(download_bps, "download_bps", 1024, 1024**3)
                self.db.execute(
                    "UPDATE users SET download_bps=? WHERE username=?", (download_bps, username)
                )

    def _upload(self, username, transfer_id):
        object_id(transfer_id)
        row = self.db.execute(
            "SELECT * FROM uploads WHERE id=? AND username=?", (transfer_id, username)
        ).fetchone()
        if not row:
            raise ServiceError("NOT_FOUND", "Upload does not exist")
        return dict(row)

    @staticmethod
    def _public(row):
        return {k: row[k] for k in ("id", "name", "size", "sha256", "created")}

    def begin_upload(self, username, name, size, sha256):
        filename_value(name)
        integer(size, "file size", 0, self.max_file_bytes)
        digest_value(sha256)
        with self.lock:
            user = self.get_user(username)
            existing = self.db.execute(
                "SELECT * FROM files WHERE username=? AND name=?", (username, name)
            ).fetchone()
            if existing:
                if existing["sha256"] != sha256 or existing["size"] != size:
                    raise ServiceError("EXISTS", "A different file already has this name")
                return {"complete": True, "file": self._public(existing), "offset": size}
            row = self.db.execute(
                "SELECT * FROM uploads WHERE username=? AND name=? AND state='uploading'",
                (username, name),
            ).fetchone()
            if row:
                row = dict(row)
                if row["size"] != size or row["sha256"] != sha256:
                    raise ServiceError("CONFLICT", "A different upload already uses this name")
                part = self.partials / (row["id"] + ".part")
                # Recover a crash between atomic rename and final metadata commit.
                if not part.exists() and (self.objects / row["id"]).exists():
                    info = self.finish_upload(username, row["id"])
                    return {"complete": True, "file": info, "offset": size}
                fd = open_private(part, os.O_RDWR | os.O_CREAT)
                with os.fdopen(fd, "r+b") as stream:
                    offset = min(os.fstat(stream.fileno()).st_size, row["offset"])
                    stream.truncate(offset)
                    stream.flush()
                    os.fsync(stream.fileno())
                with self.db:
                    self.db.execute("UPDATE uploads SET offset=? WHERE id=?", (offset, row["id"]))
                return {"complete": False, "transfer_id": row["id"], "offset": offset}
            pending = self.db.execute(
                "SELECT count(*),coalesce(sum(size),0) FROM uploads WHERE username=? AND state='uploading'",
                (username,),
            ).fetchone()
            finished = self.db.execute(
                "SELECT count(*),coalesce(sum(size),0) FROM files WHERE username=?", (username,)
            ).fetchone()
            if pending[0] >= 16 or pending[0] + finished[0] >= 1000:
                raise ServiceError("QUOTA", "Too many files or unfinished uploads")
            if pending[1] + finished[1] + size > user["quota_bytes"]:
                raise ServiceError(
                    "QUOTA", "Storage quota exceeded (including pending reservations)"
                )
            transfer_id = uuid.uuid4().hex
            fd = open_private(
                self.partials / (transfer_id + ".part"), os.O_WRONLY | os.O_CREAT | os.O_EXCL
            )
            os.fsync(fd)
            os.close(fd)
            fsync_directory(self.partials)
            with self.db:
                self.db.execute(
                    "INSERT INTO uploads VALUES (?,?,?,?,?,0,'uploading',?)",
                    (transfer_id, username, name, size, sha256, time.time()),
                )
            return {"complete": False, "transfer_id": transfer_id, "offset": 0}

    def upload_info(self, username, transfer_id):
        with self.lock:
            row = self._upload(username, transfer_id)
            if row["state"] != "uploading":
                raise ServiceError("CONFLICT", "Upload is not active")
            return row

    def write_chunk(self, username, transfer_id, offset, payload):
        integer(offset, "offset")
        if not 0 < len(payload) <= CHUNK_SIZE:
            raise ServiceError("BAD_REQUEST", "Invalid chunk size")
        with self.lock:
            row = self.upload_info(username, transfer_id)
            if row["offset"] != offset or offset + len(payload) > row["size"]:
                raise ServiceError("OFFSET", "Resume upload to obtain the committed offset")
            with os.fdopen(
                open_private(self.partials / (transfer_id + ".part"), os.O_RDWR), "r+b"
            ) as stream:
                stream.seek(offset)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            offset += len(payload)
            with self.db:
                self.db.execute("UPDATE uploads SET offset=? WHERE id=?", (offset, transfer_id))
            return {"offset": offset}

    def finish_upload(self, username, transfer_id):
        with self.lock:
            row = self._upload(username, transfer_id)
            if row["state"] == "completed":
                return self.get_info(username, transfer_id)
            if row["state"] != "uploading" or row["offset"] != row["size"]:
                raise ServiceError("INCOMPLETE", "Not all file bytes have been committed")
            part = self.partials / (transfer_id + ".part")
            target = self.objects / transfer_id
            source = part if part.exists() else target
            if source.stat().st_size != row["size"] or file_hash(source) != row["sha256"]:
                if source == part:
                    with os.fdopen(open_private(part, os.O_WRONLY | os.O_TRUNC), "wb") as stream:
                        stream.flush()
                        os.fsync(stream.fileno())
                    with self.db:
                        self.db.execute("UPDATE uploads SET offset=0 WHERE id=?", (transfer_id,))
                raise ServiceError("INTEGRITY", "SHA-256 mismatch; upload must restart")
            if source == part:
                os.replace(part, target)
                fsync_directory(self.objects)
                fsync_directory(self.partials)
            with self.db:
                self.db.execute(
                    "INSERT INTO files VALUES (?,?,?,?,?,?)",
                    (transfer_id, username, row["name"], row["size"], row["sha256"], time.time()),
                )
                self.db.execute("UPDATE uploads SET state='completed' WHERE id=?", (transfer_id,))
            return self.get_info(username, transfer_id)

    def abort_upload(self, username, transfer_id):
        with self.lock:
            row = self._upload(username, transfer_id)
            if row["state"] == "completed":
                raise ServiceError("CONFLICT", "Completed files must be deleted explicitly")
            (self.partials / (transfer_id + ".part")).unlink(missing_ok=True)
            with self.db:
                self.db.execute("UPDATE uploads SET state='cancelled' WHERE id=?", (transfer_id,))
            return {"cancelled": True}

    def get_info(self, username, file_id):
        object_id(file_id)
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM files WHERE id=? AND username=?", (file_id, username)
            ).fetchone()
            if not row:
                raise ServiceError("NOT_FOUND", "File does not exist")
            return self._public(row)

    def read_chunk(self, username, file_id, offset, length):
        integer(offset, "offset")
        integer(length, "length", 1, CHUNK_SIZE)
        with self.lock:
            info = self.get_info(username, file_id)
            if offset > info["size"]:
                raise ServiceError("OFFSET", "Offset exceeds file size")
            with os.fdopen(open_private(self.objects / file_id, os.O_RDONLY), "rb") as stream:
                stream.seek(offset)
                data = stream.read(min(length, info["size"] - offset))
            if len(data) != min(length, info["size"] - offset):
                raise ServiceError("INTEGRITY", "Stored file size changed")
            return data

    def list_files(self, username, cursor="", limit=25):
        if cursor:
            object_id(cursor)
        integer(limit, "limit", 1, 25)
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM files WHERE username=? AND id>? ORDER BY id LIMIT ?",
                (username, cursor, limit + 1),
            ).fetchall()
            return {
                "files": [self._public(r) for r in rows[:limit]],
                "next_cursor": rows[limit - 1]["id"] if len(rows) > limit else "",
            }

    def delete_file(self, username, file_id):
        with self.lock:
            self.get_info(username, file_id)
            (self.objects / file_id).unlink()
            with self.db:
                self.db.execute("DELETE FROM files WHERE id=? AND username=?", (file_id, username))
                self.db.execute(
                    "UPDATE uploads SET state='deleted' WHERE id=? AND username=?",
                    (file_id, username),
                )
            return {"deleted": True}
