"""Read-only scheduling diagnostics; never consumes a future or changes a read."""

import threading

from worker.meet_media.dialog_control_exchange import DialogControlExchange


class DialogControlObserver:
    def __init__(self, monkeypatch):
        self.lock = threading.Lock()
        self.failure = None
        self.previous_poll = None
        poll = DialogControlExchange.poll

        def observe(exchange, **kwargs):
            now = exchange.clock()
            previous, self.previous_poll = self.previous_poll, now
            try:
                return poll(exchange, **kwargs)
            except Exception:
                with self.lock:
                    self.failure = {
                        "poll_gap_ms": None if previous is None else round((now - previous) * 1000, 2),
                        "expiry_delta_ms": None
                        if exchange.fresh_until is None
                        else round((now - exchange.fresh_until) * 1000, 2),
                        "request_age_ms": round((now - exchange.started) * 1000, 2),
                        "pending": exchange.pending is not None,
                        "done": exchange.pending is not None and exchange.pending.done(),
                    }
                raise

        monkeypatch.setattr(DialogControlExchange, "poll", observe)

    def report(self):
        with self.lock:
            return None if self.failure is None else dict(self.failure)
