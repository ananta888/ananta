"""Container health probe for the isolated runner Unix socket."""

from __future__ import annotations

import os
import socket

from worker.hrm_experiments.protocol import receive_message, send_message


def main() -> int:
    path = os.environ.get(
        "ANANTA_HRM_RUNNER_SOCKET", "/run/ananta-hrm/runner.sock"
    )
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2.0)
            connection.connect(path)
            send_message(connection, {"op": "capability", "payload": {}})
            response = receive_message(connection)
    except Exception:
        return 1
    capability = response.get("result") if response.get("status") == "ok" else None
    return 0 if isinstance(capability, dict) and capability.get("feature_enabled") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
