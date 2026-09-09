"""One closed Hub generation assignment, durable replay fence, no orchestration."""

import base64
import sqlite3
import threading
import time

from ananta_contracts.persona_generation import decode_result, recipe_digest, validate_assignment, validate_recipe


class PersonaGenerationExecutor:
    def __init__(self, replay_path, *, guard_factory, generator):
        self.path, self.guard_factory, self.generator = replay_path, guard_factory, generator
        self.lock = threading.Lock()
        with sqlite3.connect(replay_path, timeout=1) as db:
            db.execute("CREATE TABLE IF NOT EXISTS generation_leases (id TEXT PRIMARY KEY, deadline INTEGER NOT NULL)")

    def execute(self, request):
        if type(request) is not dict or set(request) != {"assignment", "recipe"}:
            raise ValueError("persona_generation_request_invalid")
        assignment = validate_assignment(request["assignment"], time.time())
        recipe = validate_recipe(request["recipe"])
        if recipe_digest(recipe) != assignment["source_sha256"]:
            raise ValueError("persona_generation_recipe_mismatch")
        if not self.lock.acquire(blocking=False):
            raise ValueError("persona_generation_worker_busy")
        try:
            guard = self.guard_factory(assignment)
            guard.require()
            with sqlite3.connect(self.path, timeout=1) as db:
                db.execute("DELETE FROM generation_leases WHERE deadline < ?", (int(time.time()) - 60,))
                try:
                    db.execute(
                        "INSERT INTO generation_leases VALUES (?, ?)", (assignment["lease_id"], assignment["deadline"])
                    )
                except sqlite3.IntegrityError:
                    raise ValueError("persona_generation_lease_replayed") from None
            content = self.generator(
                require_current=guard.require,
                deadline_monotonic=time.monotonic() + assignment["deadline"] - time.time(),
            ).generate(recipe)
            guard.require()
            validate_assignment(assignment, time.time())
            result = {
                "task_id": assignment["task_id"],
                "lease_id": assignment["lease_id"],
                "media_type": "image/png" if recipe["media_kind"] == "image" else "video/mp4",
                "content": base64.b64encode(content).decode(),
            }
            decode_result(result, assignment, recipe)
            return result
        finally:
            self.lock.release()
