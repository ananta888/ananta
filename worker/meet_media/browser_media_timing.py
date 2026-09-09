"""Narrow optional browser quality transport; no source-open or Hub policy port."""

import time

from ananta_contracts.meet_media_timing import PROFILE, require_media_timing_probe, validate_media_timing
from worker.meet_media.media_timing_gate import MediaTimingGate

READ_PROBE = """() => {
  const timing = window.anantaMachine?.timing;
  if (!timing || ['probe', 'start', 'snapshot'].some(key => typeof timing[key] !== 'function')) return null;
  const value = timing.probe();
  return value && typeof value.then !== 'function' ? value : null;
}"""
START = """profile => {
  const value = window.anantaMachine.timing.start(profile);
  return value && typeof value.then !== 'function' ? value : null;
}"""
SNAPSHOT = """() => {
  const value = window.anantaMachine.timing.snapshot();
  return value && typeof value.then !== 'function' ? value : null;
}"""


class BrowserMediaTiming:
    def __init__(self, page, checkpoint, capabilities, *, decoded_video=False, clock=time.monotonic):
        self.page, self.checkpoint, self.clock = page, checkpoint, clock
        self.gate = None
        self.closed = False
        self.last_poll = None
        try:
            checkpoint()
            require_media_timing_probe(page.evaluate(READ_PROBE), decoded_video=decoded_video)
            checkpoint()
            snapshot = validate_media_timing(page.evaluate(START, PROFILE))
            checkpoint()
            if snapshot["sources"]:
                raise ValueError("meet_media_timing_sources_already_active")
            sources = {source for source in ("speech", "avatar", "screen") if source + ".publish" in capabilities}
            self.gate = MediaTimingGate(snapshot["epoch"], sources, clock=clock)
            self.gate.accept(snapshot)
            self.last_poll = self.gate.last_at
        except Exception:
            self.close()
            raise

    def poll(self):
        if self.closed:
            raise ValueError("meet_media_timing_closed")
        try:
            now = self.clock()
            if type(now) not in (int, float) or not 0 <= now < float("inf") or now < self.last_poll:
                raise ValueError("meet_media_timing_clock_invalid")
            if now - self.last_poll < 0.1:
                return None
            self.checkpoint()
            value = self.page.evaluate(SNAPSHOT)
            self.checkpoint()
            result = self.gate.accept(value)
            self.last_poll = self.gate.last_at
            return result
        except Exception:
            self.close()
            raise

    def close(self):
        self.closed = True
        if self.gate is not None:
            self.gate.close()
        # Outer source/session lifecycles own teardown. No permissive API exists
        # to reset the browser quality fence while preserving this membership.
