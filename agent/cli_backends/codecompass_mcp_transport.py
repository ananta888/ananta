"""Invocation-local MCP Streamable HTTP transport; authority stays in the parent."""

from __future__ import annotations

import hmac
import json
import secrets
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import copy_context
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TypeAlias

Json: TypeAlias = str | int | float | bool | None | list["Json"] | dict[str, "Json"]
RpcObject: TypeAlias = dict[str, Json]


@dataclass(frozen=True, slots=True)
class McpEndpoint:
    url: str
    token: str

    def config(self) -> RpcObject:
        return {
            "type": "remote",
            "url": self.url,
            "enabled": True,
            "oauth": False,
            "headers": {"Authorization": f"Bearer {self.token}"},
        }


@contextmanager
def serve_mcp(dispatch: Callable[[RpcObject], RpcObject | None]) -> Iterator[McpEndpoint]:
    """Serve one invocation on loopback, requiring an ephemeral parent-held secret."""
    token = secrets.token_urlsafe(32)
    context = copy_context()
    closed = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Json) -> None:
            """Do not log request data or credentials."""

        def do_GET(self) -> None:
            self.send_error(405)

        def do_POST(self) -> None:
            if (
                closed.is_set()
                or self.path != "/mcp"
                or self.headers.get("Origin")
                or not hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {token}")
            ):
                self.send_error(403)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 1024 * 1024:
                    self.send_error(413)
                    return
                self.connection.settimeout(5)
                request = json.loads(self.rfile.read(size))
                if not isinstance(request, dict):
                    self.send_error(400)
                    return
                response = context.copy().run(dispatch, request)
            except (ValueError, OSError):
                self.send_error(400)
                return
            if response is None:
                self.send_response(202)
                self.end_headers()
                return
            body = json.dumps(response, ensure_ascii=True).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            yield McpEndpoint(f"http://127.0.0.1:{server.server_port}/mcp", token)
        finally:
            closed.set()
            server.shutdown()
            thread.join(timeout=2)
