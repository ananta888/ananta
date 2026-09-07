"""PCM browser boundary; no URL, credentials, capture or model port."""

import secrets
import time

_START = """([token, id, samples]) => {
  const source = window.anantaMachine.speech;
  window.__anantaSpeechSetup?.cancel();
  const phase = {token, state: 'pending', result: null, generation: null};
  phase.cancel = () => {
    phase.state = 'cancelled'; phase.result = null;
    if (phase.generation !== null && source.status().generation === phase.generation) source.close();
  };
  window.__anantaSpeechSetup = phase;
  const pending = source.open(id, samples);
  phase.generation = source.status().generation;
  void Promise.resolve(pending).then(result => {
    if (window.__anantaSpeechSetup !== phase || phase.state !== 'pending') return;
    if (JSON.stringify(result)?.length > 1024) { phase.cancel(); return; }
    phase.result = result; phase.state = 'done';
  }).catch(() => { if (phase.state === 'pending') phase.state = 'failed'; });
}"""
_STATE = """token => {
  const phase = window.__anantaSpeechSetup;
  if (!phase || phase.token !== token) return {state: 'stale', result: null};
  return {state: phase.state, result: phase.result};
}"""
_CANCEL = """token => {
  const phase = window.__anantaSpeechSetup;
  if (phase?.token === token) { phase.cancel(); delete window.__anantaSpeechSetup; }
}"""


class BrowserSpeechPort:
    def __init__(self, page, require_current, *, clock=time.monotonic):
        self.page, self.require_current, self.clock = page, require_current, clock
        self.url = page.url

    def _check(self):
        if self.page.url != self.url:
            raise ValueError("meet_speech_navigation_denied")
        self.require_current()
        if self.page.url != self.url:
            raise ValueError("meet_speech_navigation_denied")

    def open(self, source_id, total_samples):
        # Local operation token, never an evidence identity or grant. Do not await
        # a browser Promise in the RPC: retain Hub lease polling while it starts.
        token = secrets.token_hex(16)
        deadline = self.clock() + 10
        try:
            self._check()
            self.page.evaluate(_START, [token, source_id, total_samples])
            while True:
                self._check()
                if self.clock() >= deadline:
                    raise ValueError("meet_speech_setup_timeout")
                value = self.page.evaluate(_STATE, token)
                self._check()
                if self.clock() >= deadline:
                    raise ValueError("meet_speech_setup_timeout")
                if not isinstance(value, dict) or set(value) != {"state", "result"}:
                    raise ValueError("meet_speech_setup_failed")
                if value["state"] == "done":
                    return value["result"]
                if value["state"] != "pending":
                    raise ValueError("meet_speech_setup_failed")
                self.page.wait_for_timeout(50)
        except Exception:
            if self.page.url == self.url:
                try:
                    self.page.evaluate(_CANCEL, token)
                except Exception:
                    pass
            raise

    def status(self):
        self._check()
        return self.page.evaluate("window.anantaMachine.speech.status()")

    def push(self, generation, start_sample, pcm_base64):
        self._check()
        self.page.evaluate(
            "([gen, start, pcm]) => window.anantaMachine.speech.push(gen, start, pcm)",
            [generation, start_sample, pcm_base64],
        )

    def close(self, generation):
        if self.page.url != self.url:
            return
        self.page.evaluate(
            """gen => {
          const source = window.anantaMachine.speech;
          if (source.status().generation === gen) source.close();
          if (window.__anantaSpeechSetup?.generation === gen) delete window.__anantaSpeechSetup;
        }""",
            generation,
        )
