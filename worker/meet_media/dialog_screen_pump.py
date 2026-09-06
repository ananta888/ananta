"""Continuous source execution; only fresh Hub state can activate/reopen a track."""

import time
from worker.meet_media.dialog_screen import OwnedDialogScreen


class DialogScreenPump:
    def __init__(self, page, browser, assignment, source_factory=OwnedDialogScreen):
        self.page, self.browser, self.assignment, self.source_factory = page, browser, assignment, source_factory
        self.source = None; self.lease = None; self.sequence = 0; self.revision = 0
        self.next_frame = 0; self.failed = False

    def update(self, control):
        if control["revision"] != self.revision:
            self.close(); self.failed = False
        self.revision = control["revision"]
        if not control["enabled"] or "screen.publish" not in self.assignment["capabilities"]:
            self.close(); return
        if self.failed:
            return
        try:
            if self.source is None:
                self.source = self.source_factory(self.browser, self.assignment["session_id"])
            if not self.page.evaluate("window.anantaMachine.screen.status().open"):
                self.lease = self.page.evaluate("id => window.anantaMachine.screen.open(id)", self.source.source_id)
                self.sequence = 0
        except Exception:
            self.failed = True
            self.close()

    def tick(self):
        if self.source is None or self.failed or time.monotonic() < self.next_frame:
            return
        try:
            if not self.page.evaluate("window.anantaMachine.screen.status().open"):
                self.lease = None; return
            if self.lease is None:
                return
            frame = self.source.take()
            if frame is not None:
                self.sequence += 1
                self.page.evaluate("([gen, seq, jpeg]) => window.anantaMachine.screen.push(gen, seq, jpeg)",
                                   [self.lease["generation"], self.sequence, frame])
            self.next_frame = time.monotonic() + 0.2
        except Exception:
            self.failed = True
            self.close()  # A source failure does not revive it or stop unrelated chat/audio.

    def invalidate(self):
        self.lease = None
        try:
            self.page.evaluate("window.anantaMachine.screen.close()")
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
