#!/usr/bin/env python3
"""Minimal LMStudio-compatible mock server for CI E2E tests."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


MOCK_MODEL_ID = "mock-lmstudio-model"
MOCK_TEMPLATE = {
    "name": "Scrum Team Templates",
    "description": "Vorlage fuer Scrum-Rollen und Teamablauf.",
    "prompt_template": "Du bist {{agent_name}}. Erstelle Templates fuer {{team_name}}."
}


class _Handler(BaseHTTPRequestHandler):
    server_version = "MockLMStudio/1.0"

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json({"status": "ok"})
            return
        if self.path == "/v1/models":
            self._send_json(
                {
                    "object": "list",
                    "data": [
                        {
                            "id": MOCK_MODEL_ID,
                            "object": "model",
                            "context_length": 8192,
                        }
                    ],
                }
            )
            return
        self._send_json({"error": "not_found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        _ = self._read_json()
        content = json.dumps(MOCK_TEMPLATE)

        if self.path == "/v1/chat/completions":
            self._send_json(
                {
                    "id": "chatcmpl-mock",
                    "object": "chat.completion",
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": content}}
                    ],
                }
            )
            return

        if self.path == "/v1/completions":
            self._send_json(
                {
                    "id": "cmpl-mock",
                    "object": "text_completion",
                    "choices": [{"index": 0, "text": content}],
                }
            )
            return

        self._send_json({"error": "not_found"}, status=404)

    def log_message(self, _format: str, *_args) -> None:  # noqa: A003
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1234)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), _Handler)
    print(f"Mock LMStudio server listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
