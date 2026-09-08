"""Small deterministic checks for the secret-free container probe itself."""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tests.meet_egress_probe import blocked, http


def test_http_probe_uses_real_bounded_client_and_closes_connection():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.path == "/counts"
            body = json.dumps({"http": 2}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        server.timeout = 1
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        try:
            assert http("127.0.0.1", server.server_port, "/counts") == {"http": 2}
        finally:
            thread.join(timeout=2)
            assert not thread.is_alive()


@pytest.mark.parametrize("error", [TimeoutError(), ConnectionRefusedError(), socket.gaierror()])
def test_only_network_failures_are_a_blocked_observation(error):
    def operation():
        raise error

    assert blocked(operation) is True
    assert blocked(lambda: b"reply") is False


def test_probe_programming_error_cannot_be_misreported_as_firewall_denial():
    def operation():
        raise AttributeError("probe bug")

    with pytest.raises(AttributeError):
        blocked(operation)
