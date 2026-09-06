"""Bounded private HTTP serving shared by execution adapters, not task orchestration."""

import socket
import threading
from http.server import ThreadingHTTPServer


class BoundedWorkerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, *, slots, connection_seconds, read_seconds=5):
        self.slots = threading.BoundedSemaphore(slots)
        self.connection_seconds, self.read_seconds = connection_seconds, read_seconds
        super().__init__(address, handler)

    def process_request(self, connection, client_address):
        if not self.slots.acquire(blocking=False):
            connection.close()
            return
        connection.settimeout(self.read_seconds)
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

        # Includes header parsing; malformed slow clients cannot keep a slot forever.
        timer = threading.Timer(self.connection_seconds, expire)
        timer.daemon = True
        timer.start()
        try:
            super().process_request_thread(connection, client_address)
        finally:
            timer.cancel()
            self.slots.release()

    def handle_error(self, *_args):
        pass  # No HTTP body, key, image or model text is logged on disconnect.
