"""Consume only an exact Hub visual child assignment; never create or route tasks."""

import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack

from ananta_contracts.meet_visual_receive import require_visual_probe, require_visual_subscription, validate_visual_job
from worker.meet_media.source_lease import SourceLease
from worker.meet_media.visual_frames import project_visual_frame
from worker.meet_media.visual_pipeline import MeetVisualPipeline


class DialogVisualPump:
    def __init__(self, page, hub, assignment, job):
        self.page, self.hub, self.job = page, hub, validate_visual_job(job, time.time())
        if "video.receive" not in assignment["capabilities"]:
            raise ValueError("meet_visual_capability_denied")
        with ExitStack() as setup:
            setup.callback(self._close_browser)
            require_visual_probe(page.evaluate("() => window.anantaMachine?.visual?.probe?.() ?? null"))
            self.subscription = require_visual_subscription(
                page.evaluate("id => window.anantaMachine.visual.open(id)", job["publication_id"]), assignment, job
            )
            self.binding = self.subscription["binding"]
            self.lease = SourceLease(self.binding, job)
            setup.callback(self.lease.close)
            self.pipeline = MeetVisualPipeline(
                self.binding, self.lease, deadline_monotonic=time.monotonic() + min(20, job["deadline"] - time.time())
            )
            setup.callback(self.pipeline.cancel)
            self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="meet-local-visual")
            setup.pop_all()
        self.frames = []
        self.pending = None
        self.last_frame = float("-inf")
        self.closed = False
        self.stage = "receive"

    def refresh(self, receipt, job):
        self.lease.refresh(receipt, job)

    def tick(self):
        if self.closed:
            return
        self.lease.require(self.binding)
        if not self.page.evaluate("window.anantaMachine.visual.status().open"):
            raise ValueError("meet_visual_browser_revoked")
        if self.stage == "receive":
            if time.monotonic() - self.last_frame < 0.5:
                return
            frame = self.page.evaluate(
                "id => window.anantaMachine.visual.frame(id)", self.subscription["subscriptionId"]
            )
            self.frames.append(project_visual_frame(frame, self.subscription["subscriptionId"], len(self.frames) + 1))
            self.last_frame = time.monotonic()
            if len(self.frames) == 3:
                self.stage = "analyze"
                frames, self.frames = self.frames, []
                self.pending = self.pool.submit(self._analyze, frames)
        elif self.pending.done():
            result = self.pending.result()
            if self.stage == "analyze":
                self.stage = "report"
                self.pending = self.pool.submit(
                    self.hub.call,
                    "visual_result",
                    meet_session_id=self.job["meet_session_id"],
                    visual_task_id=self.job["task_id"],
                    visual_lease_id=self.job["lease_id"],
                    result=result,
                )
            else:
                self.close()

    def _analyze(self, frames):
        try:
            return self.pipeline.analyze(frames)
        finally:
            frames.clear()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.lease.close()
        self.pipeline.cancel()
        self.frames.clear()
        if self.pending is not None:
            self.pending.cancel()
            self.pending = None
        self.pool.shutdown(wait=False, cancel_futures=True)
        self._close_browser()

    def _close_browser(self):
        try:
            self.page.evaluate("window.anantaMachine.visual.close()")
        except Exception:
            pass  # Native cleanup still runs after a lost browser.


def start_visual(page, hub, assignment, state, meet_session):
    control = state["controls"].get("visual")
    if (
        "video.receive" not in assignment["capabilities"]
        or not control
        or not control["enabled"]
        or state.get("visual_job") is not None
    ):
        return None
    receipt = state["authorization"]
    sources = page.evaluate("() => window.anantaMachine?.visual?.sources?.() ?? []")
    source = next(
        (
            p
            for p in receipt["publications"]
            if p["source"] in ("camera", "screen")
            and any(
                s
                == {
                    "publicationId": p["publicationId"],
                    "peerId": p["peerId"],
                    "source": p["source"],
                    "publicationEpoch": p["publicationEpoch"],
                }
                for s in sources
            )
        ),
        None,
    )
    if source is None:
        return None
    try:
        delegated = hub.call("visual", meet_session_id=meet_session, publication_id=source["publicationId"])
        return DialogVisualPump(page, hub, assignment, delegated["job"])
    except Exception:
        return None  # Hub reservation/expiry bounds retry; no alternative source/profile.
