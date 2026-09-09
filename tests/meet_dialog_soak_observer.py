"""Bounded passive scheduling diagnostics for the real dialog soak."""

import threading
import time
from collections import deque

from tests.meet_dialog_control_observer import DialogControlObserver
from tests.meet_dialog_rpc_observer import DialogRpcObserver
from tests.meet_dialog_transport_observer import DialogTransportObserver
from worker.meet_media.dialog_screen_pump import DialogScreenPump


def record_soak_failure(observer, record_property):
    if observer is not None:
        record_property("dialog_soak_failure_scheduling", observer.report())


class DialogSoakObserver:
    def __init__(self, monkeypatch, *, clock=time.monotonic):
        self.rpc = DialogRpcObserver(monkeypatch, clock=clock)
        self.control = DialogControlObserver(monkeypatch)
        self.transport = DialogTransportObserver(monkeypatch, clock=clock)
        self.lock = threading.Lock()
        self.screen = deque(maxlen=16)
        self.previous_tick = None
        tick = DialogScreenPump.tick

        def observed_tick(pump):
            started, previous = clock(), self.previous_tick
            self.previous_tick = started
            try:
                return tick(pump)
            finally:
                row = {
                    "at_ms": round(started * 1000, 2),
                    "gap_ms": None if previous is None else round((started - previous) * 1000, 2),
                    "duration_ms": round((clock() - started) * 1000, 2),
                    "sequence": pump.sequence,
                    "pending": pump.frames.busy,
                    "failed": pump.failed,
                }
                with self.lock:
                    self.screen.append(row)

        monkeypatch.setattr(DialogScreenPump, "tick", observed_tick)

    def report(self):
        with self.lock:
            screen = [dict(row) for row in self.screen]
        return {
            "screen_ticks": screen,
            "slow_browser_calls": self.rpc.report(),
            "controls": self.control.history(),
            "control_failure": self.control.report(),
            "transport": self.transport.report(),
        }
