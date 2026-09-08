"""Actual bounded loopback UDP/TCP queries; no network namespace or external DNS."""

import socket
import struct
import threading
import time
from contextlib import contextmanager

import pytest

from tests.test_meet_egress_dns_wire import policy, query
from worker.meet_egress.dns_server import FixedDnsResponder, read_exact

pytestmark = pytest.mark.timeout(15)


@contextmanager
def serving():
    server = FixedDnsResponder(policy(), port=0)
    stop = threading.Event()
    thread = threading.Thread(target=server.serve, args=(stop.is_set,), daemon=True)
    thread.start()
    try:
        yield server
    finally:
        stop.set()
        thread.join(timeout=1)
        assert not thread.is_alive()
        began = time.monotonic()
        server.close()
        assert time.monotonic() - began < 1.5


def udp(server, packet):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.settimeout(1)
        client.sendto(packet, ("127.0.0.1", server.port))
        return client.recvfrom(513)[0]


@pytest.mark.parametrize(
    "name,kind,rcode,records",
    [
        ("meet.example.test", 1, 0, 1),
        ("meet.example.test", 28, 0, 0),
        ("foreign.example.test", 1, 3, 0),
        ("meet.example.test", 16, 5, 0),
    ],
)
def test_real_udp_and_tcp_share_exact_fixed_record_decision(name, kind, rcode, records):
    with serving() as server:
        assert server.tcp.getsockname()[0] == server.udp.getsockname()[0] == "127.0.0.1"
        packet = query(name, kind)
        response = udp(server, packet)
        fields = struct.unpack("!6H", response[:12])
        assert fields[1] & 15 == rcode and fields[3] == records
        with socket.create_connection(("127.0.0.1", server.port), timeout=1) as client:
            client.sendall(struct.pack("!H", len(packet)) + packet)
            deadline = time.monotonic() + 1
            size = struct.unpack("!H", read_exact(client, 2, deadline))[0]
            assert read_exact(client, size, deadline) == response
            assert client.recv(1) == b""  # One query, no unbounded persistent stream.


def test_slow_tcp_clients_do_not_block_udp_and_expire_without_a_person():
    with serving() as server:
        held = []
        try:
            for _ in range(4):
                client = socket.create_connection(("127.0.0.1", server.port), timeout=2)
                client.sendall(bytes([0]))  # Never finish even the length prefix.
                held.append(client)
            response = udp(server, query())
            assert struct.unpack("!6H", response[:12])[3] == 1
            began = time.monotonic()
            for client in held:
                assert client.recv(1) == b""
            assert time.monotonic() - began < 1.5
            assert struct.unpack("!6H", udp(server, query())[:12])[3] == 1
        finally:
            for client in held:
                client.close()


@pytest.mark.parametrize("size", [0, 11, 513, 65535])
def test_oversize_tcp_length_closes_before_body_read(size):
    with serving() as server:
        with socket.create_connection(("127.0.0.1", server.port), timeout=1) as client:
            client.sendall(struct.pack("!H", size))
            assert client.recv(1) == b""


def test_oversize_udp_is_formerr_not_forwarded_or_buffered():
    with serving() as server:
        response = udp(server, query() + b"x" * 2048)
        assert len(response) == 12 and struct.unpack("!6H", response)[1] & 15 == 1


@pytest.mark.parametrize("port", [True, -1, 65536, "53"])
def test_invalid_listener_input_cannot_bind_an_interface(port):
    with pytest.raises(ValueError, match="listener_invalid"):
        FixedDnsResponder(policy(), port=port)
