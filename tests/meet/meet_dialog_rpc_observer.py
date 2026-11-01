"""Measure existing browser calls only; no extra RPCs, contents or arguments."""

import threading
import time
from collections import deque


class DialogRpcObserver:
    def __init__(self, monkeypatch, *, clock=time.monotonic):
        from playwright.sync_api import Page

        self.lock = threading.Lock()
        self.slow = deque(maxlen=20)
        evaluate, screenshot = Page.evaluate, Page.screenshot

        def record(operation, started):
            elapsed = (clock() - started) * 1000
            if elapsed >= 40:
                with self.lock:
                    self.slow.append(
                        {
                            "operation": operation,
                            "started_at_ms": round(started * 1000, 2),
                            "elapsed_ms": round(elapsed, 2),
                        }
                    )

        def observed_evaluate(page, expression, *args, **kwargs):
            operation = next(
                (
                    name
                    for name in ("speech", "screen", "avatar", "__testPcs", "__testIce", "chat", "status", "probe")
                    if name.casefold() in expression.casefold()
                ),
                "other",
            )
            started = clock()
            try:
                return evaluate(page, expression, *args, **kwargs)
            finally:
                record(operation, started)

        def observed_screenshot(page, *args, **kwargs):
            started = clock()
            try:
                return screenshot(page, *args, **kwargs)
            finally:
                record("screenshot", started)

        monkeypatch.setattr(Page, "evaluate", observed_evaluate)
        monkeypatch.setattr(Page, "screenshot", observed_screenshot)

    def report(self):
        with self.lock:
            return [dict(row) for row in self.slow]
