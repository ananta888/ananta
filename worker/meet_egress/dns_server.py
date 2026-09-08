"""Loopback-only bounded DNS transport; fixed records and no upstream client."""

import selectors
import socket
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack

from ananta_contracts.meet_egress import EgressPolicy
from worker.meet_egress.dns_wire import MAX_DNS_BYTES, answer


def read_exact(connection, size, deadline):
    result = bytearray()
    while len(result) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        connection.settimeout(remaining)
        part = connection.recv(size - len(result))
        if not part:
            raise ValueError("meet_egress_dns_incomplete")
        result.extend(part)
    return bytes(result)


class FixedDnsResponder:
    def __init__(self, policy, *, port=53):
        if type(policy) is not EgressPolicy or type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("meet_egress_dns_listener_invalid")
        self.policy, self.resources = policy, ExitStack()
        self.slots = threading.BoundedSemaphore(4)
        try:
            self.tcp = self.resources.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
            self.tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.tcp.bind(("127.0.0.1", port))
            self.port = self.tcp.getsockname()[1]
            self.tcp.listen(8)
            self.tcp.setblocking(False)
            self.udp = self.resources.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
            self.udp.bind(("127.0.0.1", self.port))
            self.udp.setblocking(False)
            self.selector = self.resources.enter_context(selectors.DefaultSelector())
            self.selector.register(self.udp, selectors.EVENT_READ, self._udp)
            self.selector.register(self.tcp, selectors.EVENT_READ, self._accept)
            self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="meet-fixed-dns")
            self.resources.callback(lambda: self.pool.shutdown(wait=True, cancel_futures=False))
        except Exception:
            self.close()
            raise

    def serve(self, stopped):
        while not stopped():
            for key, _ in self.selector.select(0.1):
                if stopped():
                    break
                key.data()

    def _udp(self):
        try:
            packet, peer = self.udp.recvfrom(MAX_DNS_BYTES + 1)
            self.udp.sendto(answer(packet, self.policy), peer)
        except OSError:
            pass  # One closed/local socket result never enables forwarding.

    def _accept(self):
        try:
            connection, _ = self.tcp.accept()
        except BlockingIOError:
            return
        if not self.slots.acquire(blocking=False):
            connection.close()
            return
        try:
            self.pool.submit(self._tcp, connection, time.monotonic() + 1)
        except Exception:
            connection.close()
            self.slots.release()
            raise

    def _tcp(self, connection, deadline):
        try:
            with connection:
                size = struct.unpack("!H", read_exact(connection, 2, deadline))[0]
                if not 12 <= size <= MAX_DNS_BYTES:
                    return
                response = answer(read_exact(connection, size, deadline), self.policy)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                connection.settimeout(remaining)
                connection.sendall(struct.pack("!H", len(response)) + response)
        except (OSError, ValueError):
            pass  # No query, peer, configuration or exception content is logged.
        finally:
            self.slots.release()

    def close(self):
        self.resources.close()  # Caller first stops serve(); active reads expire within one second.
