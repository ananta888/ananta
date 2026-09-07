"""One in-flight screen decode; no source selection, media queue or Hub policy."""

import secrets
import time
from typing import Protocol

START = """([token, generation, sequence, jpeg, url]) => {
  if (window.location.href !== url) throw new Error('meet_screen_navigation_denied');
  const source = window.anantaMachine.screen;
  if (window.__anantaScreenFrame?.state === 'pending') throw new Error('meet_screen_frame_busy');
  const phase = {token, generation, sequence, state:'pending'};
  window.__anantaScreenFrame = phase;
  try {
    const current = source.status();
    if (!current.open || current.generation !== generation || current.sequence + 1 !== sequence) {
      phase.state = !current.open ? 'stale' : 'failed'; return;
    }
    void Promise.resolve(source.push(generation, sequence, jpeg)).then(() => {
      if (window.__anantaScreenFrame !== phase || phase.state !== 'pending') return;
      const current = source.status();
      phase.state = !current.open ? 'stale'
        : current.generation === generation && current.sequence === sequence ? 'done' : 'failed';
    }).catch(error => {
      if (window.__anantaScreenFrame !== phase || phase.state !== 'pending') return;
      phase.state = error?.message === 'meet_screen_authority_changed' && !source.status().open ? 'stale' : 'failed';
    });
  } catch { phase.state = 'failed'; }
}"""
POLL = """([token, url]) => {
  if (window.location.href !== url) throw new Error('meet_screen_navigation_denied');
  const phase = window.__anantaScreenFrame;
  if (!phase || phase.token !== token) return 'failed';
  const result = phase.state;
  if (result !== 'pending') delete window.__anantaScreenFrame;
  return result;
}"""
CANCEL = """([token, url]) => {
  if (window.location.href !== url) return;
  const phase = window.__anantaScreenFrame;
  if (phase?.token !== token) return;
  phase.state = 'cancelled'; delete window.__anantaScreenFrame;
  const source = window.anantaMachine.screen;
  if (source.status().generation === phase.generation) source.close();
}"""
CLOSE = """([generation, url]) => {
  if (window.location.href !== url) return;
  const source = window.anantaMachine.screen;
  if (source.status().generation === generation) source.close();
}"""


class ScreenFramePage(Protocol):
    @property
    def url(self) -> str: ...
    def evaluate(self, expression, arg=None): ...


class BrowserScreenFrames:
    def __init__(self, page: ScreenFramePage, *, url: str, clock=time.monotonic):
        self.page, self.url, self.clock = page, url, clock
        self.token = None
        self.deadline = 0

    @property
    def busy(self):
        return self.token is not None

    def _check(self):
        if self.page.url != self.url:
            raise ValueError("meet_screen_navigation_denied")
        if self.busy and self.clock() >= self.deadline:
            raise ValueError("meet_screen_delivery_timeout")

    def begin(self, generation: int, sequence: int, jpeg: str):
        if self.busy:
            raise ValueError("meet_screen_frame_busy")
        if (
            type(generation) is not int
            or not 1 <= generation < 2**53
            or type(sequence) is not int
            or not 1 <= sequence < 2**53
            or not isinstance(jpeg, str)
            or not 0 < len(jpeg) <= 350_000
        ):
            raise ValueError("meet_screen_frame_invalid")
        self.token, self.deadline = secrets.token_hex(16), self.clock() + 1.5
        try:
            self._check()
            self.page.evaluate(START, [self.token, generation, sequence, jpeg, self.url])
            self._check()
        except Exception:
            self.cancel()
            raise

    def poll(self):
        if not self.busy:
            raise ValueError("meet_screen_frame_not_pending")
        try:
            self._check()
            state = self.page.evaluate(POLL, [self.token, self.url])
            self._check()
            if not isinstance(state, str) or state not in {"pending", "done", "stale"}:
                raise ValueError("meet_screen_frame_rejected")
            if state != "pending":
                self.token = None
            return state
        except Exception:
            self.cancel()
            raise

    def cancel(self):
        token, self.token = self.token, None
        if token is not None and self.page.url == self.url:
            self.page.evaluate(CANCEL, [token, self.url])

    def close_generation(self, generation: int):
        if type(generation) is not int or not 1 <= generation < 2**53:
            raise ValueError("meet_screen_generation_invalid")
        if self.page.url == self.url:
            self.page.evaluate(CLOSE, [generation, self.url])
