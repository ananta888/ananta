"""Non-blocking isolated avatar bridge; pulses only come from its Hub caller."""

import secrets

_START = """([token, id]) => {
  const source = window.anantaMachine.avatar, before = source.status().generation;
  const phase = {token, state: 'pending', receipt: null, generation: null};
  window.__anantaAvatarSetup = phase;
  const pending = source.open(id, 'neutral-ai-v1');
  const generation = source.status().generation;
  if (generation !== before) phase.generation = generation;
  void Promise.resolve(pending).then(receipt => {
    if (window.__anantaAvatarSetup !== phase || phase.state !== 'pending') return;
    if (JSON.stringify(receipt)?.length > 1024) { phase.state = 'failed'; return; }
    phase.receipt = receipt; phase.state = 'done';
  }).catch(() => { if (phase.state === 'pending') phase.state = 'failed'; });
}"""
_STATE = """token => {
  const phase = window.__anantaAvatarSetup;
  if (!phase || phase.token !== token) return null;
  return {phase: phase.state, generation: phase.generation,
    receipt: phase.receipt, source: window.anantaMachine.avatar.status()};
}"""
_PULSE = """token => {
  const phase = window.__anantaAvatarSetup;
  if (!phase || phase.token !== token || !Number.isSafeInteger(phase.generation)) throw new Error('avatar_phase_stale');
  window.anantaMachine.avatar.pulse(phase.generation);
}"""
_CLOSE = """token => {
  const phase = window.__anantaAvatarSetup;
  if (!phase || phase.token !== token) return;
  phase.state = 'cancelled'; phase.receipt = null;
  if (Number.isSafeInteger(phase.generation)) window.anantaMachine.avatar.close(phase.generation);
  delete window.__anantaAvatarSetup;
}"""


class AvatarBrowserPort:
    def __init__(self, page, url):
        self.page, self.url, self.token = page, url, None

    def _check(self):
        if self.page.url != self.url:
            raise ValueError("meet_avatar_navigation_denied")

    def start(self, source_id):
        self._check()
        if self.token is not None:
            raise ValueError("meet_avatar_operation_busy")
        # Local operation token, never an evidence identity or authorization.
        self.token = secrets.token_hex(16)
        self.page.evaluate(_START, [self.token, source_id])

    def status(self):
        self._check()
        return self.page.evaluate(_STATE, self.token)

    def pulse(self):
        self._check()
        self.page.evaluate(_PULSE, self.token)

    def close(self):
        token, self.token = self.token, None
        if token is not None and self.page.url == self.url:
            self.page.evaluate(_CLOSE, token)
