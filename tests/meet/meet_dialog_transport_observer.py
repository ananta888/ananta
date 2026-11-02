"""Passive end-to-end Worker callback timing; no payloads, URLs or extra calls."""

import threading
import time
from collections import deque

from worker.meet_media.dialog_client import HubDialogClient


class DialogTransportObserver:
    def __init__(self, monkeypatch, *, clock=time.monotonic):
        self.clock, self.lock = clock, threading.Lock()
        self.recent, self.active = deque(maxlen=12), {}
        self.overflow = False
        call = HubDialogClient.call

        def observe(client, action, **fields):
            if action != "exchange":
                return call(client, action, **fields)
            started, token = clock(), object()
            with self.lock:
                if len(self.active) < 4:
                    self.active[token] = started
                else:
                    self.overflow = True
            status = "failed"
            try:
                value = call(client, action, **fields)
                status = "completed"
                return value
            finally:
                elapsed = (clock() - started) * 1000
                with self.lock:
                    self.active.pop(token, None)
                    self.recent.append(
                        {"status": status, "started_at_ms": round(started * 1000, 2), "elapsed_ms": round(elapsed, 2)}
                    )

        monkeypatch.setattr(HubDialogClient, "call", observe)

    def report(self):
        now = self.clock()
        with self.lock:
            return {
                "recent": [dict(row) for row in self.recent],
                "inflight": [
                    {"started_at_ms": round(started * 1000, 2), "elapsed_ms": round((now - started) * 1000, 2)}
                    for started in self.active.values()
                ],
                "overflow": self.overflow,
            }
