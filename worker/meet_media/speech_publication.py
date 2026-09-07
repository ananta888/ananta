"""Nonblocking sink for already delegated PCM; never synthesizes or delegates work."""

import base64
import re
import time
from collections.abc import Callable
from typing import Protocol

from ananta_contracts.meet_speech_source import (
    FRAME_SAMPLES,
    QUEUE_SAMPLES,
    SAMPLE_RATE,
    source_progress,
    source_receipt,
)
from worker.meet_media.audio_output import SpeechFrame


class SpeechBrowserPort(Protocol):
    def open(self, source_id: str, total_samples: int) -> dict: ...
    def status(self) -> dict: ...
    def push(self, generation: int, start_sample: int, pcm_base64: str) -> None: ...
    def close(self, generation: int) -> None: ...


class SpeechPublication:
    """One bounded generation; callers retain the next frame during backpressure."""

    def __init__(
        self,
        browser: SpeechBrowserPort,
        session_id: str,
        total_samples: int,
        require_current: Callable[[], None],
        *,
        clock=time.time,
        monotonic=time.monotonic,
    ):
        if (
            not isinstance(session_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", session_id)
            or type(total_samples) is not int
            or not 1 <= total_samples <= 40 * SAMPLE_RATE
        ):
            raise ValueError("meet_speech_publication_invalid")
        self.browser, self.require_current = browser, require_current
        self.clock, self.monotonic = clock, monotonic
        self.receipt = None
        self.sent = self.played = 0
        self.completed = False
        self.closed = False
        self.deadline = monotonic() + 50
        self.last_wall = clock()
        generation = None
        try:
            self._check()
            source_id = "speech:" + session_id
            value = browser.open(source_id, total_samples)
            # A malformed receipt must never authorize a write. Cleanup is only
            # generation-conditional, including when a newer source has opened.
            if isinstance(value, dict) and type(value.get("generation")) is int:
                generation = value["generation"]
            self.receipt = source_receipt(
                value, source_id=source_id, total_samples=total_samples, now_ms=int(clock() * 1000)
            )
            self._check()
        except Exception:
            if generation is not None:
                try:
                    browser.close(generation)
                except Exception:
                    pass
            self.closed = True
            raise

    def _check(self):
        now = self.clock()
        if (
            self.closed
            or now < self.last_wall
            or self.monotonic() >= self.deadline
            or self.receipt is not None
            and now * 1000 >= self.receipt.expires_at_ms
        ):
            raise ValueError("meet_speech_publication_expired")
        self.last_wall = now
        self.require_current()

    def writable_samples(self):
        if self.completed:
            return 0
        try:
            self._check()
            value = self.browser.status()
            self._check()
            self.played, self.completed = source_progress(value, self.receipt, sent=self.sent, played=self.played)
            return (
                0
                if self.completed
                else min(QUEUE_SAMPLES - (self.sent - self.played), self.receipt.total_samples - self.sent)
            )
        except Exception:
            self.close()
            raise

    def push(self, frame: SpeechFrame):
        try:
            if (
                not isinstance(frame, SpeechFrame)
                or type(frame.start_sample) is not int
                or frame.start_sample != self.sent
                or type(frame.pcm_s16le) is not bytes
                or len(frame.pcm_s16le) != 2 * min(FRAME_SAMPLES, self.receipt.total_samples - self.sent)
                or not frame.pcm_s16le
            ):
                raise ValueError("meet_speech_publication_frame_invalid")
            if self.writable_samples() < frame.samples:
                return False
            self._check()
            self.browser.push(self.receipt.generation, self.sent, base64.b64encode(frame.pcm_s16le).decode("ascii"))
            self.sent += frame.samples
            self._check()
            return True
        except Exception:
            self.close()
            raise

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.receipt is not None:
            try:
                self.browser.close(self.receipt.generation)
            except Exception:
                pass  # The browser also owns a bounded independent source watchdog.
