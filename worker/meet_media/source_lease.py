"""Current Hub-reserved source binding shared by receive execution adapters."""

import threading
import time
from copy import deepcopy

from ananta_contracts.meet_source_job import source_job_current


class SourceLease:
    def __init__(self, binding, job, *, error_code="meet_source_lease_revoked"):
        self.binding, self.job = deepcopy(binding), deepcopy(job)
        self.error_code = error_code
        self.lock = threading.Lock()
        self.closed = False
        self.checked_at = time.monotonic()

    def refresh(self, receipt, active_job):
        with self.lock:
            if active_job != self.job or not source_job_current(self.job, receipt, time.time()):
                self.closed = True
            else:
                self.checked_at = time.monotonic()

    def require(self, binding):
        with self.lock:
            if (
                self.closed
                or binding != self.binding
                or time.time() >= self.job["deadline"]
                or time.monotonic() - self.checked_at > 6
            ):
                raise ValueError(self.error_code)

    def close(self):
        with self.lock:
            self.closed = True
