"""Passive bounded cost attribution; no extra browser work or private payloads."""

import threading
import time
from collections import deque


def operation_name(expression):
    """Closed tags distinguish source validation and test-only diagnostic RPCs."""
    if "iceCounts:window.__testIce" in expression or "window.__testPcs.map" in expression:
        return "test_diagnostics"
    if "Boolean(document.querySelector(" in expression:
        return "source_content"
    if "getElementById('activity')" in expression:
        return "source_activity"
    for name in ("timing", "screen", "speech", "avatar", "chat"):
        if name.casefold() in expression.casefold():
            return name
    return "other_evaluate"


class DialogBrowserCostObserver:
    """Inclusive public-call costs; nested ACK costs must not be added twice."""

    def __init__(self, monkeypatch, *, clock=time.monotonic):
        from playwright.sync_api import CDPSession, Page

        self.lock = threading.Lock()
        self.totals = {}
        self.recent = deque(maxlen=20)
        evaluate, send, idle = Page.evaluate, CDPSession.send, Page.wait_for_timeout

        def invoke(name, function, *args, **kwargs):
            started = clock()
            try:
                return function(*args, **kwargs)
            finally:
                elapsed = max(0, (clock() - started) * 1000)
                bucket = next((i for i, bound in enumerate((5, 40, 100, 250, 500, 1000)) if elapsed <= bound), 6)
                with self.lock:
                    totals = self.totals.setdefault(name, {"calls": 0, "total_ms": 0, "max_ms": 0, "buckets": [0] * 7})
                    totals["calls"] += 1
                    totals["total_ms"] += elapsed
                    totals["max_ms"] = max(totals["max_ms"], elapsed)
                    totals["buckets"][bucket] += 1
                    if elapsed >= 40:
                        self.recent.append(
                            {
                                "operation": name,
                                "started_at_ms": round(started * 1000, 2),
                                "elapsed_ms": round(elapsed, 2),
                            }
                        )

        def measured_evaluate(page, expression, *args, **kwargs):
            return invoke(operation_name(expression), evaluate, page, expression, *args, **kwargs)

        def measured_send(session, method, *args, **kwargs):
            tag = "source_ack" if method == "Page.screencastFrameAck" else "other_cdp"
            return invoke(tag, send, session, method, *args, **kwargs)

        def measured_idle(page, *args, **kwargs):
            return invoke("idle", idle, page, *args, **kwargs)

        monkeypatch.setattr(Page, "evaluate", measured_evaluate)
        monkeypatch.setattr(CDPSession, "send", measured_send)
        monkeypatch.setattr(Page, "wait_for_timeout", measured_idle)

    def report(self):
        with self.lock:
            return {
                "inclusive_costs_not_additive": True,
                "bucket_upper_ms": [5, 40, 100, 250, 500, 1000, None],
                "operations": {
                    name: {
                        **values,
                        "total_ms": round(values["total_ms"], 2),
                        "max_ms": round(values["max_ms"], 2),
                        "buckets": list(values["buckets"]),
                    }
                    for name, values in self.totals.items()
                },
                "recent": [dict(row) for row in self.recent],
            }
