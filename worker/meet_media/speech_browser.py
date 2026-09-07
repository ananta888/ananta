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
    def __init__(self, page, require_current, *, clock=time.monotonic, lease=None):
        self.page, self.require_current, self.clock = page, require_current, clock
        self.lease = lease
        self.url = page.url

    def _evaluate(self, operation, args=None):
        if self.lease is None:
            return self.page.evaluate(operation, args)
        # Keep local membership/chat checks in the same RPC as source access.
        # Separate status RPCs can consume the entire unchanged 200-ms queue.
        script = """([expected, argument]) => {
          const check = () => {
            const machine = window.anantaMachine, current = machine.status();
            if (!expected || current.joined !== true || machine.chat.status().open !== true
              || JSON.stringify(Object.entries(current.lease || {}).sort())
                !== JSON.stringify(Object.entries(expected).sort())) {
              throw new Error('meet_dialog_speech_browser_changed');
            }
          };
          check(); const result = (OPERATION)(argument); check(); return result;
        }""".replace("OPERATION", operation)
        return self.page.evaluate(script, [dict(self.lease()), args])

    def _check(self):
        if self.page.url != self.url:
            raise ValueError("meet_speech_navigation_denied")
        self.require_current()
        if self.page.url != self.url:
            raise ValueError("meet_speech_navigation_denied")

    def open(self, source_id, total_samples):
        # Synchronous compatibility port. Dialog runtimes use begin/poll instead
        # so their outer loop can refresh Hub state during browser setup.
        token = None
        deadline = self.clock() + 10
        try:
            token = self.begin_open(source_id, total_samples)
            while True:
                if self.clock() >= deadline:
                    raise ValueError("meet_speech_setup_timeout")
                value = self.poll_open(token)
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
            if token is not None:
                try:
                    self.cancel_open(token)
                except Exception:
                    pass
            raise

    def begin_open(self, source_id, total_samples):
        token = secrets.token_hex(16)
        try:
            self._check()
            self._evaluate(_START, [token, source_id, total_samples])
            self._check()
            return token
        except Exception:
            try:
                self.cancel_open(token)
            except Exception:
                pass
            raise

    def poll_open(self, token):
        self._check()
        value = self._evaluate(_STATE, token)
        self._check()
        return value

    def cancel_open(self, token):
        if self.page.url == self.url:
            self.page.evaluate(_CANCEL, token)

    def status(self):
        self._check()
        return self._evaluate("() => window.anantaMachine.speech.status()")

    def push(self, generation, start_sample, pcm_base64):
        self._check()
        self._evaluate(
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

    def push_frames(self, generation, start_sample, frames):
        self._check()
        if not isinstance(frames, list) or not 1 <= len(frames) <= 10:
            raise ValueError("meet_speech_publication_batch_invalid")
        self._evaluate(
            """([gen, start, frames]) => {
              const source = window.anantaMachine.speech;
              for (const pcm of frames) {
                source.push(gen, start, pcm);
                start += atob(pcm).length / 2;
              }
            }""",
            [generation, start_sample, frames],
        )
        self._check()
