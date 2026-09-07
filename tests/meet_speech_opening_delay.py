"""Delay only native speech worklet setup, never authority or its source watchdog."""

import threading
import time
from collections import deque

from playwright.sync_api import Browser

from worker.meet_media.speech_opening import BrowserSpeechOpening

SCRIPT = """(() => {
  if (typeof AudioWorklet === 'undefined') throw new Error('test_audio_worklet_missing');
  const original = AudioWorklet.prototype.addModule;
  AudioWorklet.prototype.addModule = function(url, ...args) {
    const loaded = original.call(this, url, ...args);
    if (url !== '/assets/machine-speech.worklet.js') return loaded;
    return Promise.all([loaded, new Promise(resolve => setTimeout(resolve, 3000))]).then(([value]) => value);
  };
})()"""


class SpeechOpeningDelay:
    def __init__(self, monkeypatch):
        self.lock = threading.Lock()
        self.durations = deque(maxlen=3)
        new_context, poll = Browser.new_context, BrowserSpeechOpening.poll

        def context(browser, *args, **kwargs):
            result = new_context(browser, *args, **kwargs)
            result.add_init_script(SCRIPT)
            return result

        def observe(opening):
            result = poll(opening)
            if result is not None:
                with self.lock:
                    self.durations.append(round((time.monotonic() - (opening.deadline - 10)) * 1000, 2))
            return result

        monkeypatch.setattr(Browser, "new_context", context)
        monkeypatch.setattr(BrowserSpeechOpening, "poll", observe)

    def verify(self, record_property):
        with self.lock:
            values = list(self.durations)
        assert len(values) == 2 and all(3000 <= duration < 10000 for duration in values), values
        record_property("synthetic_speech_setup_delay_ms", values)
