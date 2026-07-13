"""Minimal liveness/readiness HTTP server for the Temporal worker container."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass(frozen=True)
class WorkerHealthSnapshot:
    live: bool
    ready: bool
    draining: bool
    reason_code: str
    build_id: str
    identity: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "ananta.temporal-worker-health.v1",
            "status": "ready" if self.ready else "degraded",
            "live": self.live,
            "ready": self.ready,
            "draining": self.draining,
            "reason_code": self.reason_code,
            "build_id": self.build_id,
            "identity": self.identity,
        }


class WorkerHealthState:
    def __init__(self, *, build_id: str, identity: str) -> None:
        self._lock = threading.Lock()
        self._live = True
        self._ready = False
        self._draining = False
        self._reason_code = "starting"
        self._build_id = str(build_id)
        self._identity = str(identity)

    def ready(self) -> None:
        self._set(ready=True, draining=False, reason_code="")

    def degraded(self, reason_code: str) -> None:
        self._set(ready=False, reason_code=reason_code)

    def draining(self) -> None:
        self._set(ready=False, draining=True, reason_code="graceful_shutdown")

    def stopped(self) -> None:
        self._set(live=False, ready=False, draining=False, reason_code="stopped")

    def snapshot(self) -> WorkerHealthSnapshot:
        with self._lock:
            return WorkerHealthSnapshot(
                live=self._live,
                ready=self._ready,
                draining=self._draining,
                reason_code=self._reason_code,
                build_id=self._build_id,
                identity=self._identity,
            )

    def _set(
        self,
        *,
        live: bool | None = None,
        ready: bool | None = None,
        draining: bool | None = None,
        reason_code: str | None = None,
    ) -> None:
        with self._lock:
            if live is not None:
                self._live = live
            if ready is not None:
                self._ready = ready
            if draining is not None:
                self._draining = draining
            if reason_code is not None:
                self._reason_code = str(reason_code or "")[:256]


class WorkerHealthServer:
    def __init__(self, *, host: str, port: int, state: WorkerHealthState) -> None:
        self._state = state
        handler = _handler_for(state)
        self._server = ThreadingHTTPServer((host, int(port)), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="temporal-worker-health",
            daemon=True,
        )

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def _handler_for(state: WorkerHealthState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "AnantaTemporalHealth/1"

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            snapshot = state.snapshot()
            if self.path == "/live":
                ok = snapshot.live
            elif self.path == "/ready":
                ok = snapshot.ready
            elif self.path == "/health":
                ok = snapshot.live
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            payload = json.dumps(snapshot.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
            self.send_response(HTTPStatus.OK if ok else HTTPStatus.SERVICE_UNAVAILABLE)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


__all__ = [
    "WorkerHealthServer",
    "WorkerHealthSnapshot",
    "WorkerHealthState",
]
