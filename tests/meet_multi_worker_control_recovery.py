"""One test-owned transient read failure per assigned Worker; no policy relaxation."""

import threading
import time

from agent.services.meet_contract import MeetError


class MultiWorkerControlRecovery:
    def __init__(self, enabled):
        self.enabled = enabled
        self.lock = threading.Lock()
        self.reads = {}
        self.interrupted, self.recovered = set(), set()

    def wrap(self, native):
        if not self.enabled:
            return native

        def exchange(payload):
            task = payload["task_id"]
            with self.lock:
                if task not in self.reads and len(self.reads) >= 2:
                    raise ValueError("test_control_recovery_scope_exceeded")
                count = self.reads[task] = self.reads.get(task, 0) + 1
                if count == 2:
                    self.interrupted.add(task)
                    raise MeetError("meet_authorization_unavailable", 503)
            result = native(payload)
            with self.lock:
                if count > 2 and task in self.interrupted:
                    self.recovered.add(task)
            return result

        return exchange

    def wait_recovered(self):
        if not self.enabled:
            return
        until = time.monotonic() + 3
        while time.monotonic() < until:
            with self.lock:
                if len(self.reads) == len(self.interrupted) == len(self.recovered) == 2:
                    return
            time.sleep(0.025)
        raise AssertionError("both assigned Workers did not complete bounded read recovery")

    def require(self, record_property):
        if self.enabled:
            assert len(self.reads) == len(self.interrupted) == len(self.recovered) == 2
            record_property(
                "packaged_control_read_recovery",
                {
                    "worker_containers": 2,
                    "injected_http_503": 2,
                    "fresh_signed_recoveries": 2,
                    "unchanged_control_freshness_ms": 2500,
                    "synthetic_policy": True,
                    "production_release_evidence": False,
                },
            )
