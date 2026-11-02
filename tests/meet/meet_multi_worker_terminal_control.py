"""Arm one closed domain denial after real publication, without changing policy."""

import threading
import time

from agent.services.meet_contract import MeetError


class MultiWorkerTerminalControl:
    def __init__(self, enabled):
        self.enabled = enabled
        self.lock = threading.Lock()
        self.task_id = None
        self.failures = 0

    def wrap(self, native):
        if not self.enabled:
            return native

        def exchange(payload):
            with self.lock:
                if payload["task_id"] == self.task_id:
                    self.failures += 1
                    raise MeetError("meet_authorization_contract_invalid", 502)
            return native(payload)

        return exchange

    def arm(self, task_id):
        with self.lock:
            if not self.enabled or self.task_id is not None or not isinstance(task_id, str) or not task_id:
                raise ValueError("test_terminal_control_scope_invalid")
            self.task_id = task_id

    def wait_stopped(self, app, tasks):
        if not self.enabled:
            return
        until = time.monotonic() + 8
        while time.monotonic() < until:
            with app.app_context():
                task = tasks.get_by_id(self.task_id)
            if task.status == "failed":
                return
            time.sleep(0.05)
        raise AssertionError("terminal control denial did not finish the assigned Worker")

    def require(self, record_property):
        if self.enabled:
            assert self.task_id is not None and self.failures == 1, "known terminal failure acquired read retries"
            record_property(
                "packaged_terminal_control",
                {
                    "terminal_http_status": 502,
                    "failed_reads": 1,
                    "retries": 0,
                    "surviving_worker_continues": True,
                    "synthetic_policy": True,
                    "production_release_evidence": False,
                },
            )
