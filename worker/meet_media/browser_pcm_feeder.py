"""Content-bounded transport for one browser-owned PCM feeder."""

import base64
import secrets

from worker.meet_media.browser_pcm_feeder_scripts import CLOSE, PULSE, START, STATUS


class BrowserPcmFeeder:
    def __init__(self, page, url):
        self.page, self.url, self.token = page, url, None

    def _evaluate(self, script, argument):
        if self.page.url != self.url:
            raise ValueError("meet_speech_feeder_navigation_denied")
        value = self.page.evaluate(script, argument)
        if self.page.url != self.url:
            raise ValueError("meet_speech_feeder_navigation_denied")
        return value

    def start(self, pcm, receipt, authority):
        if self.token is not None or type(pcm) is not bytes or not 0 < len(pcm) <= 1764000 or len(pcm) % 2:
            raise ValueError("meet_speech_feeder_input_invalid")
        self.token = secrets.token_hex(16)  # Local cancellation token, never evidence or authority.
        self._evaluate(START, [self.token, receipt, authority, base64.b64encode(pcm).decode("ascii")])

    def status(self):
        return self._evaluate(STATUS, self.token)

    def pulse(self, authority):
        return self._evaluate(PULSE, [self.token, authority])

    def close(self):
        token, self.token = self.token, None
        if token is not None and self.page.url == self.url:
            self.page.evaluate(CLOSE, token)
