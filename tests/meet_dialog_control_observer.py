"""Read-only scheduling diagnostics; never consumes a future or changes a read."""

import threading
from collections import deque

from worker.meet_media.dialog_control_exchange import DialogControlExchange


class DialogControlObserver:
    def __init__(self, monkeypatch):
        self.lock = threading.Lock()
        self.failure = None
        self.previous_poll = None
        self.transitions = deque(maxlen=16)
        poll = DialogControlExchange.poll

        def observe(exchange, **kwargs):
            now = exchange.clock()
            previous, self.previous_poll = self.previous_poll, now
            pending, fresh_until = exchange.pending, exchange.fresh_until
            event = "poll"
            try:
                result = poll(exchange, **kwargs)
                if result is not None:
                    event = "accepted"
                return result
            except Exception:
                event = "failed"
                with self.lock:
                    self.failure = {
                        "poll_gap_ms": None if previous is None else round((now - previous) * 1000, 2),
                        "expiry_delta_ms": None
                        if exchange.fresh_until is None
                        else round((now - exchange.fresh_until) * 1000, 2),
                        "request_age_ms": round((now - exchange.started) * 1000, 2),
                        "pending": exchange.pending is not None,
                        "done": exchange.pending is not None and exchange.pending.done(),
                        "running": exchange.pending is not None and exchange.pending.running(),
                        "next_due_delta_ms": round((now - exchange.next_request) * 1000, 2),
                    }
                raise
            finally:
                if event != "poll" or pending is not exchange.pending or fresh_until != exchange.fresh_until:
                    with self.lock:
                        self.transitions.append(
                            {
                                "event": event,
                                "at_ms": round(now * 1000, 2),
                                "request_started_ms": round(exchange.started * 1000, 2),
                                "fresh_until_ms": None
                                if exchange.fresh_until is None
                                else round(exchange.fresh_until * 1000, 2),
                                "pending": exchange.pending is not None,
                                "done": exchange.pending is not None and exchange.pending.done(),
                            }
                        )

        monkeypatch.setattr(DialogControlExchange, "poll", observe)

    def report(self):
        with self.lock:
            return None if self.failure is None else dict(self.failure)

    def history(self):
        with self.lock:
            return [dict(row) for row in self.transitions]
