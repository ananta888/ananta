"""Observe actual Worker startup before exercising peer-side consent UI."""

import threading
import time

from worker.meet_media.dialog_session_operations import DialogSessionOperations


class DialogStartupObserver:
    def __init__(self, monkeypatch, *, clock=time.monotonic):
        self.clock = clock
        self.started = None
        self.joined_ms = None
        self.phase = "not_started"
        self.settled = threading.Event()
        ready, join = DialogSessionOperations.ready, DialogSessionOperations.join

        def observed_ready(session, *args, **kwargs):
            if not self.settled.is_set():
                self.phase = "client_ready"
            return ready(session, *args, **kwargs)

        def observed_join(session, *args, **kwargs):
            if not self.settled.is_set():
                self.phase = "joining"
            result = join(session, *args, **kwargs)
            if not self.settled.is_set():
                self.joined_ms = round((self.clock() - self.started) * 1000)
                self.phase = "joined"
                self.settled.set()
            return result

        monkeypatch.setattr(DialogSessionOperations, "ready", observed_ready)
        monkeypatch.setattr(DialogSessionOperations, "join", observed_join)

    def start(self):
        self.started = self.clock()
        self.phase = "browser_bootstrap"

    def finished(self):
        if not self.settled.is_set():
            self.phase = "failed"
            self.settled.set()

    def snapshot(self):
        return {"phase": self.phase, "joined_ms": self.joined_ms}
