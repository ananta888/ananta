"""Bounded assignment execution, not a scheduler. Hub owns all task lifecycles."""

import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time

from ananta_contracts.meet_dialog import validate_assignment
from worker.meet_media.assignment_input import dialog_assignment_input
from worker.meet_media.contract import encode


class DialogExecutor:
    def __init__(self, replay_path, slots=2):
        if type(slots) is not int or not 1 <= slots <= 4:
            raise ValueError("meet_dialog_slots_invalid")
        self.slots = threading.BoundedSemaphore(slots)
        self.replay_path = replay_path
        with sqlite3.connect(replay_path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS dialog_leases (id TEXT PRIMARY KEY, deadline INTEGER NOT NULL)")

    def start(self, assignment):
        validate_assignment(assignment, time.time())
        if not self.slots.acquire(blocking=False):
            raise ValueError("meet_dialog_worker_busy")
        process = None
        try:
            with sqlite3.connect(self.replay_path) as db:
                db.execute("DELETE FROM dialog_leases WHERE deadline < ?", (int(time.time()) - 120,))
                try:
                    db.execute(
                        "INSERT INTO dialog_leases VALUES (?, ?)", (assignment["lease_id"], assignment["deadline"])
                    )
                except sqlite3.IntegrityError:
                    raise ValueError("meet_dialog_replayed") from None
            with dialog_assignment_input(encode(assignment)) as source:
                process = subprocess.Popen(
                    [sys.executable, "-m", "worker.meet_media.dialog_runtime"],
                    stdin=source,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            threading.Thread(target=self._watch, args=(process, assignment["deadline"]), daemon=True).start()
        except Exception:
            try:
                if process is not None:
                    self._stop(process)
            finally:
                self.slots.release()
            raise
        return {"schema": "ananta.meet-dialog-accepted.v1", "task_id": assignment["task_id"],
                "lease_id": assignment["lease_id"], "runtime_id": assignment["runtime_id"], "status": "accepted"}

    def _watch(self, process, deadline):
        try:
            try:
                process.wait(timeout=max(0.01, min(7200, deadline - time.time()) + 5))
            except subprocess.TimeoutExpired:
                self._stop(process)
        finally:
            self.slots.release()

    @staticmethod
    def _stop(process):
        # Also handles a child which exited between timeout/failure and kill.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        finally:
            try:
                if process.stdin is not None:
                    process.stdin.close()
            finally:
                process.wait(timeout=5)
