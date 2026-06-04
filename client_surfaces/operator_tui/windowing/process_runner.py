from __future__ import annotations

from dataclasses import dataclass

from client_runtime import process


@dataclass(frozen=True)
class ProcessStartResult:
    ok: bool
    pid: int | None
    reason: str = ""


def run_detached(argv: list[str]) -> ProcessStartResult:
    if not argv:
        return ProcessStartResult(ok=False, pid=None, reason="empty_command")
    try:
        proc = process.Popen(
            argv,
            stdin=process.DEVNULL,
            stdout=process.DEVNULL,
            stderr=process.DEVNULL,
            start_new_session=True,
        )
        return ProcessStartResult(ok=True, pid=int(proc.pid))
    except OSError as exc:
        return ProcessStartResult(ok=False, pid=None, reason=str(exc))
