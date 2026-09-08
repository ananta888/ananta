"""Execute one Hub browser job asynchronously; publish only its current safe view."""

import time
from concurrent.futures import ThreadPoolExecutor

from ananta_contracts.meet_browser_workspace import browser_generation
from worker.meet_media.browser_execution_lease import (
    BrowserExecutionLease,
    browser_execution_key,
    current_browser_source,
)
from worker.meet_media.browser_public_fetch_process import PublicDocumentFetch
from worker.meet_media.browser_public_workspace import PublicDocumentWorkspace
from worker.meet_media.browser_workspace_frame_source import BrowserWorkspaceFrameSource
from worker.meet_media.dialog_screen_pump import DialogScreenPump


class DialogBrowserScreen:
    def __init__(
        self,
        page,
        browser,
        assignment,
        *,
        pool=None,
        pump_factory=DialogScreenPump,
        workspace_factory=PublicDocumentWorkspace,
        fetch_factory=PublicDocumentFetch,
        clock=time.monotonic,
        wall_clock=time.time,
        finish=None,
    ):
        if assignment.get("browser_workspace") is not True or "screen.publish" not in assignment["capabilities"]:
            raise ValueError("meet_browser_not_negotiated")
        self.page, self.browser, self.assignment = page, browser, assignment
        self.clock, self.wall_clock = clock, wall_clock
        self.finish = finish if finish is not None else lambda _job, _status: None
        self.pump_factory, self.workspace_factory, self.fetch_factory = pump_factory, workspace_factory, fetch_factory
        self.pool = (
            pool if pool is not None else ThreadPoolExecutor(max_workers=1, thread_name_prefix="meet-public-document")
        )
        self.status = pump_factory(page, browser, assignment)
        self.pump = self.workspace = self.pending = self.lease = self.key = self.job_id = None
        self.job = None
        self.retired = set()
        self.revision = 0
        self.closed = self.failed = self.submitted = False
        self.mode = "off"

    def update(self, receipt, control, projection, activity=None):
        if self.closed:
            raise ValueError("meet_browser_controller_closed")
        try:
            if self.page.url != self.assignment["meeting"]["origin"] + "/machine":
                raise ValueError("meet_browser_parent_changed")
            source = current_browser_source(
                projection, self.assignment, receipt, control, now_ms=self.wall_clock() * 1000
            )
            if source["revision"] < self.revision:
                raise ValueError("meet_browser_control_regressed")
            previous_revision = self.revision
            self.revision, self.mode = source["revision"], source["mode"]
            if source["job"] is None:
                self._retire()
                if self.mode == "status":
                    self.status.update(control, activity)
                else:
                    self.status.close()
                return
            self.status.close()
            key, job_id = browser_execution_key(source), source["job"]["task_id"]
            if key != self.key:
                if (
                    job_id != self.job_id
                    and job_id not in self.retired
                    and (source["revision"] <= previous_revision or len(self.retired) >= 1023)
                ):
                    raise ValueError("meet_browser_job_revision_changed")
                self._retire()
                if job_id in self.retired:
                    return  # A revoked Task can never resurrect through a later projection.
                self.key, self.job_id = key, job_id
                self.job = source["job"]
                self.lease = BrowserExecutionLease(source, clock=self.clock, wall_clock=self.wall_clock)
                self.failed = self.submitted = False
            if self.failed:
                return
            self.lease.refresh(source)
            self._hydrate(source)
            if self.workspace is None or self.failed:
                return
            if self.mode != "browser" or not control["enabled"]:
                self._stop_presentation()
                return
            if self.pump is None:
                workspace, generation = self.workspace, browser_generation(source["job"])
                self.pump = self.pump_factory(
                    self.page,
                    self.browser,
                    self.assignment,
                    source_factory=lambda _browser, session: BrowserWorkspaceFrameSource(
                        workspace, generation, session
                    ),
                )
            self.pump.update(control)
        except Exception:
            self._retire()
            self.status.close()
            raise ValueError("meet_browser_update_denied") from None

    def _hydrate(self, source):
        if self.pending is not None and self.pending[0].done():
            future, lease = self.pending
            self.pending = None
            if lease is self.lease:
                try:
                    content = future.result()
                    lease.require()
                    generation = browser_generation(source["job"])
                    self.workspace = self.workspace_factory(
                        self.browser,
                        generation,
                        deadline=lease.deadline,
                        require_current=lease.require,
                        clock=self.clock,
                    )
                    self.workspace.load(content, generation)
                    lease.require()
                except Exception:
                    self._fail()
                    return
        if self.pending is None and not self.submitted:
            lease = self.lease
            fetcher = self.fetch_factory(require_current=lease.require)
            self.pending = (self.pool.submit(fetcher.fetch, source["job"]["fetch"], deadline=lease.deadline), lease)
            self.submitted = True
        # A cancelled fetch retains its one slot until the real child has exited.
        # Never queue another URL behind a stalled in-flight operation.

    def tick(self):
        if self.closed:
            return
        if self.mode == "status":
            self.status.tick()
        if self.lease is not None and not self.failed:
            try:
                self.lease.require()
                if self.pump is not None:
                    self.pump.tick()
                    if self.pump.failed:
                        raise ValueError("meet_browser_source_failed")
            except Exception:
                self._fail()

    def _stop_presentation(self):
        pump, self.pump = self.pump, None
        if pump is not None:
            pump.close()
        if self.workspace is not None:
            self.workspace.discard_pending()

    def _fail(self):
        was_failed = self.failed
        self.failed = True
        if self.lease is not None:
            self.lease.close()
        self._stop_presentation()
        workspace, self.workspace = self.workspace, None
        if workspace is not None:
            workspace.close()
        if not was_failed and self.job is not None:
            try:
                self.finish(self.job, "failed")
            except Exception:
                pass  # Cleanup report failure cannot reopen a source or block other media.

    def _retire(self):
        self._fail()
        if self.job_id is not None:
            self.retired.add(self.job_id)
        self.key = self.job_id = self.lease = None
        self.job = None
        if self.pending is not None and self.pending[0].done():
            self.pending = None

    def invalidate(self):
        self._retire()
        self.status.invalidate()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self._retire()
        self.status.close()
        if self.pending is not None:
            self.pending[0].cancel()
        self.pool.shutdown(wait=False, cancel_futures=True)
