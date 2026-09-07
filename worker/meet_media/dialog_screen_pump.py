"""Continuous source execution; only fresh Hub state can activate/reopen a track."""

import time

from worker.meet_media.dialog_screen import OwnedDialogScreen
from worker.meet_media.screen_frame_delivery import BrowserScreenFrames


class DialogScreenPump:
    def __init__(
        self, page, browser, assignment, source_factory=OwnedDialogScreen, *, frames=None, clock=time.monotonic
    ):
        self.page, self.browser, self.assignment, self.source_factory = page, browser, assignment, source_factory
        self.clock = clock
        self.frames = (
            frames
            if frames is not None
            else BrowserScreenFrames(page, url=assignment["meeting"]["origin"] + "/machine", clock=clock)
        )
        self.source = None
        self.lease = None
        self.sequence = 0
        self.revision = 0
        self.next_frame = 0
        self.failed = False

    def update(self, control, activity=None):
        if control["revision"] != self.revision:
            self.close()
            self.failed = False
        self.revision = control["revision"]
        if not control["enabled"] or "screen.publish" not in self.assignment["capabilities"]:
            self.close()
            return
        if self.failed:
            return
        try:
            if self.source is None:
                self.source = self.source_factory(self.browser, self.assignment["session_id"])
            if activity is not None:
                self.source.render_activity(activity)
            if not self.frames.busy and not self.page.evaluate("window.anantaMachine.screen.status().open"):
                self.lease = self.page.evaluate("id => window.anantaMachine.screen.open(id)", self.source.source_id)
                self.sequence = 0
        except Exception:
            self.failed = True
            self.close()

    def tick(self):
        if self.source is None or self.failed:
            return
        try:
            if self.frames.busy:
                outcome = self.frames.poll()
                if outcome == "stale":
                    # Only a later fresh Hub update may reopen an expired activation.
                    self.lease = None
                if outcome != "pending":
                    self.next_frame = self.clock() + 0.2
                return
            if self.clock() < self.next_frame:
                return
            if not self.page.evaluate("window.anantaMachine.screen.status().open"):
                self.lease = None
                return
            if self.lease is None:
                return
            frame = self.source.take()
            if frame is not None:
                self.sequence += 1
                self.frames.begin(self.lease["generation"], self.sequence, frame)
            self.next_frame = self.clock() + 0.2
        except Exception:
            self.failed = True
            self.close()  # A source failure does not revive it or stop unrelated chat/audio.

    def invalidate(self):
        lease, self.lease = self.lease, None
        try:
            try:
                self.frames.cancel()
            finally:
                if lease is not None:
                    self.frames.close_generation(lease["generation"])
        except Exception:
            self.failed = True  # Never reopen under the same control revision.

    def close(self):
        try:
            self.invalidate()
        finally:
            source, self.source = self.source, None
            if source is not None:
                try:
                    source.close()
                except Exception:
                    self.failed = True
