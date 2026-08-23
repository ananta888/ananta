#!/usr/bin/env python3
"""Minimal authenticated HTTP boundary for Needle candidate generation."""
from __future__ import annotations

import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import needle

MAX_REQUEST_BYTES = 512 * 1024
_LOCK = threading.Lock()


class CandidateHandler(BaseHTTPRequestHandler):
    server_version = "AnantaNeedleCandidate/1"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(5.0)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        expected = f"Bearer {self.server.api_token}"  # type: ignore[attr-defined]
        return hmac.compare_digest(self.headers.get("Authorization", ""), expected)

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        if self.path in {"/health", "/ready"}:
            self._json(200, {"status": "ready", "mode": "candidate_only"})
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        if self.path != "/internal/v1/candidates":
            self._json(404, {"error": "not_found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 1 or size > MAX_REQUEST_BYTES:
                raise ValueError("request_size_invalid")
            payload = json.loads(self.rfile.read(size))
            prompt = str(payload.get("prompt") or "")
            tools = payload.get("tools")
            if not prompt.strip() or not isinstance(tools, list) or len(tools) > 100:
                raise ValueError("candidate_request_invalid")
            with _LOCK:
                agent = needle.Needle(tools=tools, weights=self.server.weights)  # type: ignore[attr-defined]
                candidate = agent.complete(prompt)
            self._json(200, {"candidate": candidate, "executed": False})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception:
            self._json(503, {"error": "candidate_runtime_failed"})

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    host = os.environ.get("ANANTA_NEEDLE_BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("ANANTA_NEEDLE_PORT", "8083"))
    token = os.environ.get("ANANTA_NEEDLE_TOKEN", "")
    weights = os.environ.get("ANANTA_NEEDLE_WEIGHTS", "")
    if len(token) < 24 or not os.path.isfile(weights):
        raise SystemExit("Needle token (>=24 chars) and weights are required")
    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer.request_queue_size = 16
    server = ThreadingHTTPServer((host, port), CandidateHandler)
    server.api_token = token  # type: ignore[attr-defined]
    server.weights = weights  # type: ignore[attr-defined]
    server.serve_forever()


if __name__ == "__main__":
    main()
