"""Validate delivery observations; Hub authority and synthesis stay outside this port."""

import time

from ananta_contracts.meet_speech_source import source_progress, source_receipt
from worker.meet_media.browser_pcm_feeder import BrowserPcmFeeder
from worker.meet_media.speech_publication import validate_publication_input


class BrowserSpeechPlayback:
    def __init__(
        self,
        page,
        session_id,
        pcm,
        authority,
        *,
        opened_receipt,
        clock=time.time,
        monotonic=time.monotonic,
        feeder=None,
    ):
        self.authority, self.clock, self.monotonic = authority, clock, monotonic
        self.sent = self.played = 0
        self.closed = self.completed = False
        self.deadline = monotonic() + 50
        self.receipt = None
        self.feeder = feeder
        try:
            if type(pcm) is not bytes or len(pcm) % 2:
                raise ValueError("meet_speech_feeder_input_invalid")
            validate_publication_input(session_id, len(pcm) // 2)
            self.receipt = source_receipt(
                opened_receipt,
                source_id="speech:" + session_id,
                total_samples=len(pcm) // 2,
                now_ms=int(clock() * 1000),
            )
            current = self._check()
            if self.feeder is None:
                self.feeder = BrowserPcmFeeder(page, current["url"])
            self.feeder.start(pcm, dict(opened_receipt), current)
            self._check()
        except Exception:
            self.close()
            raise

    def _check(self):
        if self.closed or self.monotonic() >= self.deadline or self.clock() * 1000 >= self.receipt.expires_at_ms:
            raise ValueError("meet_speech_feeder_expired")
        return self.authority()

    def refresh(self):
        """Called only by a new authenticated Hub update, never by tick."""
        try:
            current = self._check()
            if self.feeder.pulse(current) is not True:
                raise ValueError("meet_speech_feeder_pulse_denied")
            self._check()
        except Exception:
            self.close()
            raise

    def tick(self):
        try:
            self._check()
            value = self.feeder.status()
            self._check()
            if (
                not isinstance(value, dict)
                or set(value) != {"state", "sent", "source"}
                or not isinstance(value["state"], str)
                or value["state"] not in {"open", "completed"}
                or type(value["sent"]) is not int
                or not self.sent <= value["sent"] <= self.receipt.total_samples
            ):
                raise ValueError("meet_speech_feeder_progress_invalid")
            played, completed = source_progress(value["source"], self.receipt, sent=value["sent"], played=self.played)
            if completed != (value["state"] == "completed"):
                raise ValueError("meet_speech_feeder_progress_invalid")
            self.sent, self.played, self.completed = value["sent"], played, completed
        except Exception:
            self.close()
            raise

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.feeder is not None:
            try:
                self.feeder.close()
            except Exception:
                pass  # The browser-local controller and source watchdogs remain bounded.
