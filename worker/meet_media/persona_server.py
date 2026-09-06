"""Dedicated private image worker; no media publication or orchestration routes."""

import hmac
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ananta_contracts.persona_image import MAX_REQUEST_BYTES, MAX_RESULT_BYTES
from worker.meet_media.contract import encode, load_key
from worker.meet_media.persona_executor import PersonaImageExecutor
from worker.meet_media.persona_http import read_bounded, request_signature, result_signature
from worker.meet_media.persona_lease import PersonaLeaseGuard


def create_server(address, key, executor):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.connection.settimeout(3)
            raw = b""
            try:
                if self.path != "/v1/persona-images" or self.headers.get("Transfer-Encoding"):
                    raise ValueError("persona_image_request_invalid")
                lengths = self.headers.get_all("Content-Length", [])
                if len(lengths) != 1 or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= MAX_REQUEST_BYTES:
                    raise ValueError("persona_image_request_size_invalid")
                raw = read_bounded(
                    self.rfile, maximum=MAX_REQUEST_BYTES, deadline=time.monotonic() + 5, length=int(lengths[0])
                )
                if not hmac.compare_digest(
                    request_signature(key, b"persona-image-v1", raw), self.headers.get("X-Ananta-Persona-Signature", "")
                ):
                    raise ValueError("persona_image_request_unauthorized")
                result, status = executor.execute(json.loads(raw)), 200
            except (ValueError, PermissionError, TimeoutError):
                result, status = {"error": {"code": "persona_image_denied_or_invalid"}}, 409
            except Exception:
                result, status = {"error": {"code": "persona_image_worker_unavailable"}}, 503
            output = encode(result)
            if len(output) > MAX_RESULT_BYTES:
                output, status = encode({"error": {"code": "persona_image_result_too_large"}}), 503
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(output)))
            self.send_header(
                "X-Ananta-Persona-Result-Signature", result_signature(key, b"persona-image-v1", raw, output)
            )
            self.end_headers()
            self.wfile.write(output)

    class BoundedServer(ThreadingHTTPServer):
        daemon_threads = True

        def __init__(self, *args):
            self.slots = threading.BoundedSemaphore(4)
            super().__init__(*args)

        def process_request(self, connection, client_address):
            if not self.slots.acquire(blocking=False):
                connection.close()
                return
            try:
                super().process_request(connection, client_address)
            except Exception:
                self.slots.release()
                raise

        def process_request_thread(self, connection, client_address):
            def expire():
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            # Includes request headers, body, execution and response; malformed
            # slow clients cannot keep a semaphore slot indefinitely.
            timer = threading.Timer(25, expire)
            timer.daemon = True
            timer.start()
            try:
                super().process_request_thread(connection, client_address)
            finally:
                timer.cancel()
                self.slots.release()

        def handle_error(self, *_args):
            pass  # No HTTP payload, key or image data is logged on disconnect.

    return BoundedServer(address, Handler)


if __name__ == "__main__":
    key = load_key(os.environ["PERSONA_IMAGE_WORKER_KEY_FILE"])
    endpoint = os.environ["PERSONA_IMAGE_HUB_LEASE_URL"]
    Path("/state").mkdir(exist_ok=True)
    executor = PersonaImageExecutor(
        "/state/persona-image-leases.sqlite",
        guard_factory=lambda assignment: PersonaLeaseGuard(endpoint, key, assignment),
    )
    create_server(("0.0.0.0", 8095), key, executor).serve_forever()
