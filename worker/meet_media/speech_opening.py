"""One owned browser setup, polled by its assigned runtime without inner waits."""

import re
import time
from collections.abc import Callable
from typing import Protocol


class SpeechOpeningBrowserPort(Protocol):
    def begin_open(self, source_id: str, total_samples: int) -> str: ...
    def poll_open(self, token: str) -> dict: ...
    def cancel_open(self, token: str) -> None: ...


class BrowserSpeechOpening:
    def __init__(
        self,
        browser: SpeechOpeningBrowserPort,
        source_id: str,
        total_samples: int,
        require_current: Callable[[], None],
        *,
        clock=time.monotonic,
    ):
        self.browser, self.source_id, self.total_samples = browser, source_id, total_samples
        self.require_current, self.clock = require_current, clock
        self.deadline = clock() + 10
        self.token = None
        self.closed = False
        self.delivered = False

    def _check(self):
        if self.closed or self.clock() >= self.deadline:
            raise ValueError("meet_speech_setup_timeout")
        self.require_current()

    def poll(self):
        self._check()
        if self.delivered:
            raise ValueError("meet_speech_setup_already_delivered")
        if self.token is None:
            self.token = self.browser.begin_open(self.source_id, self.total_samples)
            if not isinstance(self.token, str) or not re.fullmatch(r"[a-f0-9]{32}", self.token):
                raise ValueError("meet_speech_setup_token_invalid")
            self._check()
            return None
        value = self.browser.poll_open(self.token)
        self._check()
        if not isinstance(value, dict) or set(value) != {"state", "result"}:
            raise ValueError("meet_speech_setup_failed")
        if value["state"] == "pending" and value["result"] is None:
            return None
        if value["state"] == "done" and isinstance(value["result"], dict):
            self.delivered = True
            return value["result"]  # Caller must validate the exact source receipt before transfer.
        raise ValueError("meet_speech_setup_failed")

    def release(self):
        """Only after validated publication construction has assumed ownership."""
        if not self.delivered or self.closed:
            raise ValueError("meet_speech_setup_not_delivered")
        self.closed = True
        self.token = None

    def close(self):
        token, self.token = self.token, None
        self.closed = True
        if token is not None:
            try:
                self.browser.cancel_open(token)
            except Exception:
                pass  # The independent browser generation/lease watchdog still applies.
