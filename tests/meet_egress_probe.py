"""Secret-free probe mounted only in disposable internal-network test containers."""

import json
import socket
import struct
import sys
import threading
import time
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from worker.meet_egress.dns_server import read_exact


def serve(mode):
    counts, lock = {"http": 0, "wrong_port": 0, "udp": 0, "dns": 0, "ipv6": 0}, threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            with lock:
                if self.path == "/hit":
                    counts["wrong_port" if self.server.server_port == 18082 else "http"] += 1
                body = json.dumps(counts).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    def udp(family, address, key):
        connection = socket.socket(family, socket.SOCK_DGRAM)
        connection.bind(address)

        def receive():
            while True:
                data, peer = connection.recvfrom(512)
                with lock:
                    counts[key] += 1
                connection.sendto(data, peer)

        threading.Thread(target=receive, daemon=True).start()

    if mode == "endpoint":
        udp(socket.AF_INET, ("0.0.0.0", 18081), "udp")
        udp(socket.AF_INET, ("0.0.0.0", 53), "dns")
        wrong_port = ThreadingHTTPServer(("0.0.0.0", 18082), Handler)
        threading.Thread(target=wrong_port.serve_forever, daemon=True).start()
    else:
        udp(socket.AF_INET6, ("::1", 18081), "ipv6")
    ThreadingHTTPServer(("0.0.0.0", 18080 if mode == "endpoint" else 8094), Handler).serve_forever()


def http(address, port, path):
    connection = HTTPConnection(address, port, timeout=0.7)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        assert response.status == 200
        return json.loads(response.read(1024))
    finally:
        connection.close()


def datagram(address, port, data=b"probe", *, family=socket.AF_INET):
    with socket.socket(family, socket.SOCK_DGRAM) as connection:
        connection.settimeout(0.7)
        connection.connect((address, port))
        connection.send(data)
        return connection.recv(513)


def blocked(operation):
    try:
        operation()
    except OSError:
        return True
    return False


def dns(name, *, tcp=False):
    packet = struct.pack("!6H", 1, 0x0100, 1, 0, 0, 0)
    packet += b"".join(bytes([len(label)]) + label.encode() for label in name.split("."))
    packet += bytes([0, 0, 1, 0, 1])
    if tcp:
        deadline = time.monotonic() + 0.7
        with socket.create_connection(("127.0.0.11", 53), timeout=0.7) as connection:
            connection.sendall(struct.pack("!H", len(packet)) + packet)
            size = struct.unpack("!H", read_exact(connection, 2, deadline))[0]
            assert 12 <= size <= 512
            response = read_exact(connection, size, deadline)
    else:
        response = datagram("127.0.0.11", 53, packet)
    header = struct.unpack("!6H", response[:12])
    return {"rcode": header[1] & 15, "records": header[3], "address": socket.inet_ntoa(response[-4:])}


def client(allowed, forbidden):
    return {
        "allowed_http": http(allowed, 18080, "/hit")["http"] > 0,
        "allowed_udp": datagram(allowed, 18081) == b"probe",
        "allowed_name": http("allowed.example.test", 18080, "/hit")["http"] > 0,
        "forbidden_http": blocked(lambda: http(forbidden, 18080, "/hit")),
        "forbidden_udp": blocked(lambda: datagram(forbidden, 18081)),
        "wrong_port": blocked(lambda: http(allowed, 18082, "/hit")),
        "external_dns_allowed_ip": blocked(lambda: datagram(allowed, 53)),
        "external_dns_forbidden_ip": blocked(lambda: datagram(forbidden, 53)),
        "ipv6": blocked(lambda: datagram("::1", 18081, family=socket.AF_INET6)),
        "fixed_dns": dns("allowed.example.test"),
        "tcp_dns": dns("allowed.example.test", tcp=True),
        "unknown_dns": dns("unadmitted.example.test"),
    }


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode in {"endpoint", "worker"}:
        serve(mode)
    elif mode == "client":
        print(json.dumps(client(*sys.argv[2:])))
    elif mode == "http":
        print(json.dumps(http(sys.argv[2], int(sys.argv[3]), sys.argv[4])))
    elif mode == "inbound":
        print(json.dumps({"blocked": blocked(lambda: http(sys.argv[2], 8094, "/hit"))}))
