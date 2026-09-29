"""A partial HTTP header cannot hold a private worker slot indefinitely."""

import socket
import threading
from http.server import BaseHTTPRequestHandler

import pytest

from worker.meet_media.http_server import BoundedWorkerServer

pytestmark = pytest.mark.timeout(10)


def test_incomplete_headers_expire_and_release_the_connection_slot():
    completed = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.send_response(204)
            self.end_headers()

    class ObservedServer(BoundedWorkerServer):
        def process_request_thread(self, *args):
            try:
                super().process_request_thread(*args)
            finally:
                completed.set()

    server = ObservedServer(("127.0.0.1", 0), Handler, slots=1, connection_seconds=0.2)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.create_connection(server.server_address, timeout=2) as client:
            client.sendall(b"GET / HTTP/1.1\r\nHost:")
            assert client.recv(1024) == b""
        assert completed.wait(timeout=2)
        with socket.create_connection(server.server_address, timeout=2) as client:
            client.sendall(b"GET / HTTP/1.0\r\n\r\n")
            assert b"204" in client.recv(1024)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()
