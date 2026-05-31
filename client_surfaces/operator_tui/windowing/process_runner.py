from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessStartResult:
    ok: bool
    pid: int | None
    reason: str = ""


def run_detached(argv: list[str]) -> ProcessStartResult:
    if not argv:
        return ProcessStartResult(ok=False, pid=None, reason="empty_command")
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return ProcessStartResult(ok=True, pid=int(proc.pid))
    except OSError as exc:
        return ProcessStartResult(ok=False, pid=None, reason=str(exc))
