"""One immutable guard lifecycle, without tasks, grants, retries or reloads."""

import os
import signal
import sys
import threading
from pathlib import Path

from worker.meet_egress.configuration import read_policy
from worker.meet_egress.dns_server import FixedDnsResponder
from worker.meet_egress.enforcer import install_filters
from worker.meet_egress.health import CONFIGURATION, READINESS


def serve_guard(policy, stopped, *, install=install_filters, responder=FixedDnsResponder, readiness=READINESS):
    # Caller provisions an empty private tmpfs. Never overwrite a pre-existing
    # readiness marker; no success from a previous process may start a Worker.
    install(policy)
    dns = responder(policy)
    try:
        with open(readiness, "x", encoding="ascii") as target:
            os.fchmod(target.fileno(), 0o600)
            target.write(policy.digest)
        try:
            dns.serve(stopped)
        finally:
            os.unlink(readiness)
    finally:
        dns.close()


def main():
    # This marker is an accidental-host-invocation guard, not a security proof
    # against --network host. The deployment/preflight must forbid host sharing.
    if not Path("/.dockerenv").is_file() or os.getpid() != 1 or os.geteuid() != 0:
        return 1
    stopped = threading.Event()
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, lambda *_: stopped.set())
    try:
        serve_guard(read_policy(CONFIGURATION), stopped.is_set)
    except Exception:
        print("meet_egress_guard_failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
