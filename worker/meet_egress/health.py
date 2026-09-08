"""Local configuration-bound readiness and a bounded, non-forwarding DNS probe."""

import os
import socket
import stat
import struct
import sys

from worker.meet_egress.configuration import read_policy
from worker.meet_egress.dns_wire import answer

CONFIGURATION = "/etc/ananta/egress.json"
READINESS = "/run/meet-egress/ready"


def check_health(configuration=CONFIGURATION, readiness=READINESS, *, port=53):
    policy = read_policy(configuration)
    descriptor = os.open(readiness, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != 64:
            return False
        if os.read(descriptor, 65) != policy.digest.encode("ascii"):
            return False
    finally:
        os.close(descriptor)
    query = struct.pack("!6H", 0xAA01, 0x0100, 1, 0, 0, 0) + b"\x06health\x07invalid\x00\x00\x01\x00\x01"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.settimeout(0.5)
        connection.connect(("127.0.0.1", port))
        connection.send(query)
        return connection.recv(513) == answer(query, policy)


def main():
    try:
        healthy = check_health()
    except (OSError, ValueError):
        healthy = False
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
