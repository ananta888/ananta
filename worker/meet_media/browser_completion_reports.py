"""One best-effort terminal report slot; Hub deadline reconciliation owns fallback."""

from concurrent.futures import ThreadPoolExecutor


class BrowserCompletionReports:
    def __init__(self, hub, *, pool=None):
        self.hub = hub
        self.pool = (
            pool if pool is not None else ThreadPoolExecutor(max_workers=1, thread_name_prefix="meet-browser-result")
        )
        self.pending, self.last_task, self.closed = None, None, False

    def report(self, job, status):
        if self.closed or job["task_id"] == self.last_task:
            return
        self.last_task = job["task_id"]
        if self.pending is not None and not self.pending.done():
            return  # No queue or retry; the ordinary Hub deadline sweep still settles it.
        self.pending = self.pool.submit(
            self.hub.call,
            "browser_finish",
            browser_task_id=job["task_id"],
            browser_lease_id=job["lease_id"],
            status=status,
        )

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.pending is not None:
            self.pending.cancel()
        self.pool.shutdown(wait=False, cancel_futures=True)
