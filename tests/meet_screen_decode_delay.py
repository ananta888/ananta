"""Delay native JPEG completion only; never change source or authority watchdogs."""

import threading
import time
from collections import deque

from playwright.sync_api import Browser

from worker.meet_media.screen_frame_delivery import BrowserScreenFrames

SCRIPT = """(() => {
  const original = window.createImageBitmap;
  if (typeof original !== 'function') throw new Error('test_bitmap_decoder_missing');
  window.createImageBitmap = function(input, ...args) {
    const decoded = original.call(this, input, ...args);
    if (!(input instanceof Blob) || input.type !== 'image/jpeg') return decoded;
    return Promise.all([decoded, new Promise(resolve => setTimeout(resolve, 300))]).then(([bitmap]) => bitmap);
  };
})()"""


class ScreenDecodeDelay:
    def __init__(self, monkeypatch):
        self.lock = threading.Lock()
        self.durations = deque(maxlen=128)
        new_context, poll = Browser.new_context, BrowserScreenFrames.poll

        def context(browser, *args, **kwargs):
            result = new_context(browser, *args, **kwargs)
            result.add_init_script(SCRIPT)
            return result

        def observe(frames):
            result = poll(frames)
            if result == "done":
                with self.lock:
                    self.durations.append(round((time.monotonic() - (frames.deadline - 1.5)) * 1000, 2))
            return result

        monkeypatch.setattr(Browser, "new_context", context)
        monkeypatch.setattr(BrowserScreenFrames, "poll", observe)

    def verify(self, record_property):
        with self.lock:
            values = list(self.durations)
        assert len(values) >= 3 and all(300 <= value < 1500 for value in values), values
        record_property("synthetic_screen_decode_delay_ms", values)
