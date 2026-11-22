"""GUI task journal: completed history + resumable tasks across application restarts."""

import json
import time
import uuid
from pathlib import Path

from .client import atomic_json
from .security import private_directory


class TaskJournal:
    def __init__(self, state_dir: Path):
        private_directory(state_dir)
        self.path = state_dir / "tasks.json"
        self.records = {}
        if self.path.exists():
            if self.path.is_symlink() or self.path.stat().st_size > 4 * 1024**2:
                raise ValueError("Unsafe or oversized task journal")
            for record in json.loads(self.path.read_text("utf-8")):
                if record["direction"] not in ("upload", "download"):
                    raise ValueError("Invalid task direction")
                if record["status"] not in ("completed", "cancelled"):
                    record["status"] = "paused"
                    record["message"] = "上次未完成；点击继续即可恢复"
                record["speed"] = 0
                self.records[record["id"]] = record

    def create(self, direction, local_path, *, name=None, file_id=None):
        if len(self.records) >= 500:
            # Retain all unfinished tasks and the newest completed history.
            removable = [
                k for k, v in self.records.items() if v["status"] in ("completed", "cancelled")
            ]
            for key in removable[:100]:
                del self.records[key]
            if len(self.records) >= 500:
                raise ValueError("Too many unfinished tasks")
        record = {
            "id": uuid.uuid4().hex,
            "direction": direction,
            "local_path": str(Path(local_path).absolute()),
            "name": name or Path(local_path).name,
            "file_id": file_id,
            "status": "queued",
            "done": 0,
            "total": 0,
            "speed": 0,
            "created": time.time(),
            "message": "",
        }
        self.records[record["id"]] = record
        self.save()
        return record

    def save(self):
        atomic_json(self.path, list(self.records.values()))
