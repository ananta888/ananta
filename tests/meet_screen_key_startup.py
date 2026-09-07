"""Synthetic sender-key timing only; never changes authority, key bytes or codec."""

import threading
import time
from collections import deque

from playwright.sync_api import Browser

from worker.meet_media.dialog_screen_pump import DialogScreenPump

SCRIPT = """(() => {
  const Native = window.Worker;
  if (typeof Native !== 'function') return;
  const observation = {scheduled:0, delivered:0, cancelled:0, delay_ms:0};
  window.__testScreenKeyStartup = observation;
  window.Worker = class extends Native {
    constructor(url, options) {
      super(url, options);
      this.testKeyDelay = options?.name === 'sframe-media';
      this.testKeyDelays = 0;
      this.testPendingKey = null;
    }
    postMessage(message, ...args) {
      const pending = this.testPendingKey;
      if (pending && message?.type === 'set-key' && message?.direction === 'encrypt'
        && message.contextId === pending.message.contextId && message.keyId === pending.message.keyId) {
        if (message.baseKey instanceof ArrayBuffer && message.baseKey !== pending.message.baseKey) {
          new Uint8Array(message.baseKey).fill(0);
        }
        return;
      }
      if (pending && (message?.type === 'clear-all'
        || (message?.contextId === pending.message.contextId
          && (message?.type === 'clear-context'
            || (message?.type === 'set-key' && message?.direction === 'encrypt'))))) {
        this.testCancelKey();
      }
      if (!this.testKeyDelay || this.testKeyDelays >= 3
        || message?.type !== 'set-key' || message?.direction !== 'encrypt') {
        return super.postMessage(message, ...args);
      }
      this.testKeyDelays++;
      if (!(message.baseKey instanceof ArrayBuffer) || message.baseKey.byteLength !== 16) {
        throw new Error('test_key_delay_input_invalid');
      }
      const started = performance.now();
      const delayed = {message, args, timer:null};
      this.testPendingKey = delayed; observation.scheduled++;
      delayed.timer = setTimeout(() => {
        if (this.testPendingKey !== delayed) return;
        this.testPendingKey = null;
        this.testKeyDelay = false;
        observation.delivered++; observation.delay_ms = performance.now() - started;
        super.postMessage(delayed.message, ...delayed.args);
      }, 2000);
    }
    testCancelKey() {
      const pending = this.testPendingKey; this.testPendingKey = null;
      if (pending) {
        clearTimeout(pending.timer);
        new Uint8Array(pending.message.baseKey).fill(0);
        observation.cancelled++;
      }
    }
    terminate() {
      this.testKeyDelay = false;
      this.testCancelKey();
      return super.terminate();
    }
  };
})()"""

STATS = """async () => {
  const entries = (await Promise.all((window.__testPcs || []).map(pc => pc.getStats())))
    .flatMap(report => [...report.values()]);
  const video = entries.filter(s => s.type === 'outbound-rtp' && s.kind === 'video');
  const total = field => video.reduce((sum, value) => sum + (Number.isFinite(value[field]) ? value[field] : 0), 0);
  const key = window.__testScreenKeyStartup;
  return {scheduled:key?.scheduled || 0, delivered:key?.delivered || 0,
    cancelled:key?.cancelled || 0, delay_ms:Math.round(key?.delay_ms || 0),
    frames_encoded:total('framesEncoded'), keyframes_encoded:total('keyFramesEncoded'),
    packets_sent:total('packetsSent'), pli_count:total('pliCount'), nack_count:total('nackCount')};
}"""


class ScreenKeyStartup:
    def __init__(self, monkeypatch):
        self.lock = threading.Lock()
        self.observations = deque(maxlen=16)
        self.next_observation = 0
        new_context, tick = Browser.new_context, DialogScreenPump.tick

        def context(browser, *args, **kwargs):
            result = new_context(browser, *args, **kwargs)
            result.add_init_script(SCRIPT)
            return result

        def observe(pump):
            tick(pump)
            if time.monotonic() < self.next_observation:
                return
            self.next_observation = time.monotonic() + 1
            value = pump.page.evaluate(STATS)
            with self.lock:
                self.observations.append(value)

        monkeypatch.setattr(Browser, "new_context", context)
        monkeypatch.setattr(DialogScreenPump, "tick", observe)

    def report(self):
        with self.lock:
            return list(self.observations)

    def verify(self):
        values = self.report()
        assert any(
            1 <= row["scheduled"] <= 3
            and row["delivered"] == 1
            and row["cancelled"] == row["scheduled"] - 1
            and 2000 <= row["delay_ms"] < 4000
            for row in values
        ), values
