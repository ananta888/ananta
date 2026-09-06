"""Single-flight image execution with durable lease replay fencing."""

import base64
import hashlib
import sqlite3
import threading
import time

from ananta_contracts.persona_image import MAX_INPUT_BYTES, encode_image, validate_assignment
from worker.meet_media.persona_image_inspector import PersonaImageInspector


class PersonaImageExecutor:
    def __init__(self, replay_path, *, guard_factory):
        self.replay_path, self.guard_factory = replay_path, guard_factory
        self.lock = threading.Lock()
        with sqlite3.connect(replay_path, timeout=1) as db:
            db.execute("CREATE TABLE IF NOT EXISTS image_leases (id TEXT PRIMARY KEY, deadline INTEGER NOT NULL)")

    def execute(self, request):
        if not isinstance(request, dict) or set(request) != {"assignment", "content", "media_type"}:
            raise ValueError("persona_image_request_invalid")
        assignment = validate_assignment(request["assignment"], time.time())
        if (
            request["media_type"] not in ("image/png", "image/jpeg")
            or not isinstance(request["content"], str)
            or len(request["content"]) > 4 * ((MAX_INPUT_BYTES + 2) // 3)
        ):
            raise ValueError("persona_image_input_invalid")
        if not self.lock.acquire(blocking=False):
            raise ValueError("persona_image_worker_busy")
        try:
            content = base64.b64decode(request["content"], validate=True)
            if (
                not 0 < len(content) <= MAX_INPUT_BYTES
                or hashlib.sha256(content).hexdigest() != assignment["source_sha256"]
            ):
                raise ValueError("persona_image_source_mismatch")
            guard = self.guard_factory(assignment)
            guard.require()
            with sqlite3.connect(self.replay_path, timeout=1) as db:
                db.execute("DELETE FROM image_leases WHERE deadline < ?", (int(time.time()) - 60,))
                try:
                    db.execute(
                        "INSERT INTO image_leases VALUES (?, ?)", (assignment["lease_id"], assignment["deadline"])
                    )
                except sqlite3.IntegrityError:
                    raise ValueError("persona_image_lease_replayed") from None
            image = PersonaImageInspector(
                require_current=guard.require,
                deadline_monotonic=time.monotonic() + assignment["deadline"] - time.time(),
            ).inspect(content, request["media_type"])
            guard.require()
            validate_assignment(assignment, time.time())
            return {"task_id": assignment["task_id"], "lease_id": assignment["lease_id"], "image": encode_image(image)}
        finally:
            self.lock.release()
