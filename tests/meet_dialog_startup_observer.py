"""Observe actual Worker startup before exercising peer-side consent UI."""

import re
import threading
import time

from worker.meet_media.dialog_session_operations import DialogSessionOperations


def require_observed_dialog_startup(observer, completed, failures, command, record_property):
    # Dispatch is not membership. Preserve the three existing 20-second Worker
    # startup limits, then let the peer start its separate consent UI budget.
    observer.settled.wait(60)
    startup = observer.snapshot()
    record_property("dialog_startup", startup)
    if startup["phase"] != "joined":
        try:
            resources = command("fixture_resources")
            assert set(resources) == {"members", "machines", "connectionDrops"}
            assert all(type(value) is int and 0 <= value <= 20 for value in resources.values())
        except Exception:
            resources = {"state": "unavailable"}
        record_property("dialog_startup_peer_resources", resources)
    assert startup["phase"] == "joined" and not completed.is_set(), {"startup": startup, "runtime_errors": failures}


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
        self.fetch_errors = []
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

        from playwright.sync_api import Route

        fetch = Route.fetch

        def observed_fetch(route, *args, **kwargs):
            try:
                return fetch(route, *args, **kwargs)
            except Exception as error:
                if len(self.fetch_errors) < 8:
                    message = str(error).lower()
                    code = next(
                        (
                            code
                            for code, needles in (
                                ("certificate", ("certificate", "self-signed")),
                                ("timeout", ("timeout", "timed out")),
                                ("connection", ("econnreset", "econnrefused", "socket hang up")),
                            )
                            if any(needle in message for needle in needles)
                        ),
                        "unclassified",
                    )
                    self.fetch_errors.append(code)
                raise

        monkeypatch.setattr(Route, "fetch", observed_fetch)

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
            "fetch_errors": list(self.fetch_errors),
        }
