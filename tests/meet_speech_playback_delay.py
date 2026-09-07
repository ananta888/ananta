"""Delay only Python progress polling; browser PCM feeding keeps its real clock."""

import threading
import time
from collections import deque

from worker.meet_media.browser_pcm_feeder import BrowserPcmFeeder


class SpeechPlaybackDelay:
    def __init__(self, monkeypatch, *, clock=time.monotonic):
        self.lock = threading.Lock()
        self.durations = deque(maxlen=128)
        status = BrowserPcmFeeder.status

        def delayed(feeder):
            started = clock()
            feeder.page.wait_for_timeout(350)
            result = status(feeder)
            with self.lock:
                self.durations.append(round((clock() - started) * 1000, 2))
            return result

        monkeypatch.setattr(BrowserPcmFeeder, "status", delayed)

    def verify(self, record_property):
        with self.lock:
            values = list(self.durations)
        assert len(values) >= 3 and all(300 <= value < 1500 for value in values), values
        record_property("synthetic_python_playback_poll_delay_ms", values)
