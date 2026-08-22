"""Unix-domain socket server for the networkless HRM execution container."""

from __future__ import annotations

import os
import signal
import socket
from pathlib import Path
from typing import Any

from worker.hrm_experiments.protocol import (
    HrmRunnerProtocolError,
    receive_message,
    send_message,
)
from worker.hrm_experiments.runner import HrmRunnerError, build_environment_runner

_RUNNING = True


def _stop(_signum: int, _frame: Any) -> None:
    global _RUNNING
    _RUNNING = False


def serve() -> None:
    runner = build_environment_runner()
    socket_path = Path(
        os.environ.get("ANANTA_HRM_RUNNER_SOCKET", "/run/ananta-hrm/runner.sock")
    )
    if not socket_path.is_absolute():
        raise HrmRunnerError("hrm.runner_socket_must_be_absolute")
    socket_path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(socket_path))
        os.chmod(socket_path, 0o660)
        server.listen(8)
        server.settimeout(1.0)
        while _RUNNING:
            try:
                connection, _ = server.accept()
            except TimeoutError:
                continue
            with connection:
                connection.settimeout(5.0)
                try:
                    request = receive_message(connection)
                    if set(request) != {"op", "payload"}:
                        raise HrmRunnerProtocolError("hrm.runner_request_invalid")
                    operation = request["op"]
                    payload = request["payload"]
                    if operation == "capability" and payload == {}:
                        response = {"status": "ok", "result": runner.capability()}
                    elif operation == "execute" and isinstance(payload, dict):
                        response = {"status": "ok", "result": runner.execute(payload)}
                    else:
                        raise HrmRunnerProtocolError("hrm.runner_operation_forbidden")
                except (HrmRunnerError, HrmRunnerProtocolError) as exc:
                    response = {
                        "status": "error",
                        "reason_code": str(getattr(exc, "reason_code", "") or exc),
                    }
                except Exception:
                    response = {
                        "status": "error",
                        "reason_code": "hrm.runner_internal_error",
                    }
                send_message(connection, response)
    socket_path.unlink(missing_ok=True)


if __name__ == "__main__":
    serve()
