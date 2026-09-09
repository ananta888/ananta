"""Observe actual Worker startup before exercising peer-side consent UI."""

import re
import threading
import time

from worker.meet_media.dialog_session_operations import DialogSessionOperations


class DialogStartupObserver:
    def __init__(self, monkeypatch, *, clock=time.monotonic):
        self.clock = clock
        self.started = None
        self.joined_ms = None
        self.phase = "not_started"
        self.failed_phase = None
        self.http_errors = []
        self.request_errors = []
        self.script_errors = []
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
            self.failed_phase = self.phase
            self.phase = "failed"
            self.settled.set()

    def observe_context(self, context):
        def code(values, value):
            if len(values) < 8:
                match = re.search(r"\b(?:ERR_[A-Z_]{1,64}|NG[0-9]{4}|meet_[a-z_]{1,64})\b", str(value))
                values.append(match[0] if match else "unclassified")

        def response(value):
            if type(value.status) is int and 400 <= value.status <= 599 and len(self.http_errors) < 8:
                self.http_errors.append(value.status)

        context.on("response", response)
        context.on("requestfailed", lambda request: code(self.request_errors, request.failure))
        context.on("page", lambda page: page.on("pageerror", lambda error: code(self.script_errors, error)))

    def snapshot(self):
        return {
            "phase": self.phase,
            "failed_phase": self.failed_phase,
            "joined_ms": self.joined_ms,
            "http_errors": list(self.http_errors),
            "request_errors": list(self.request_errors),
            "script_errors": list(self.script_errors),
        }
