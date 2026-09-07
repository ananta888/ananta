"""Single-flight inspection with injected codecs and durable replay fencing."""

import base64
import hashlib
import sqlite3
import threading
import time


class PersonaInspectionExecutor:
    def __init__(self, replay_path, *, guard_factory, wire, inspector):
        self.wire, self.inspector = wire, inspector
        if type(wire.kind) is not str or wire.kind not in ("image", "video"):
            raise ValueError("persona_inspection_kind_invalid")
        self.table = f"{wire.kind}_leases"
        self.replay_path, self.guard_factory = replay_path, guard_factory
        self.lock = threading.Lock()
        with sqlite3.connect(replay_path, timeout=1) as db:
            db.execute(f"CREATE TABLE IF NOT EXISTS {self.table} (id TEXT PRIMARY KEY, deadline INTEGER NOT NULL)")

    def execute(self, request):
        if not isinstance(request, dict) or set(request) != {"assignment", "content", "media_type"}:
            raise ValueError(f"persona_{self.wire.kind}_request_invalid")
        assignment = self.wire.validate_assignment(request["assignment"], time.time())
        if (
            request["media_type"] not in self.wire.media_types
            or not isinstance(request["content"], str)
            or len(request["content"]) > 4 * ((self.wire.input_limit + 2) // 3)
        ):
            raise ValueError(f"persona_{self.wire.kind}_input_invalid")
        if not self.lock.acquire(blocking=False):
            raise ValueError(f"persona_{self.wire.kind}_worker_busy")
        try:
            content = base64.b64decode(request["content"], validate=True)
            if (
                not 0 < len(content) <= self.wire.input_limit
                or hashlib.sha256(content).hexdigest() != assignment["source_sha256"]
            ):
                raise ValueError(f"persona_{self.wire.kind}_source_mismatch")
            guard = self.guard_factory(assignment)
            guard.require()
            with sqlite3.connect(self.replay_path, timeout=1) as db:
                db.execute(f"DELETE FROM {self.table} WHERE deadline < ?", (int(time.time()) - 60,))
                try:
                    db.execute(
                        f"INSERT INTO {self.table} VALUES (?, ?)", (assignment["lease_id"], assignment["deadline"])
                    )
                except sqlite3.IntegrityError:
                    raise ValueError(f"persona_{self.wire.kind}_lease_replayed") from None
            inspected = self.inspector(
                require_current=guard.require,
                deadline_monotonic=time.monotonic() + assignment["deadline"] - time.time(),
            ).inspect(content, request["media_type"])
            guard.require()
            self.wire.validate_assignment(assignment, time.time())
            return {
                "task_id": assignment["task_id"],
                "lease_id": assignment["lease_id"],
                self.wire.kind: self.wire.encode_inspection(inspected),
            }
        finally:
            self.lock.release()
