"""Bounded output and cancellation around the existing process-group control."""

from __future__ import annotations

import os
import selectors
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from worker.training.process_control import ProcessGroupController


@dataclass(frozen=True, slots=True)
class BoundedProcessResult:
    returncode: int | None
    output: str
    reason: str


class BoundedProcessRunner:
    """Drain a process without unbounded communicate() buffers or shell use."""

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: float,
        max_output_bytes: int,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> BoundedProcessResult:
        if timeout_seconds <= 0 or max_output_bytes < 1:
            raise ValueError("bounded_process_limits_invalid")
        controller = ProcessGroupController()
        process = controller.start(command, cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        output = bytearray()
        reason = "completed"
        deadline = time.monotonic() + timeout_seconds
        try:
            assert process.stdout is not None
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    if cancelled():
                        reason = "cancelled"
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        reason = "timed_out"
                        break
                    for key, _mask in selector.select(min(remaining, 0.05)):
                        chunk = os.read(key.fileobj.fileno(), min(16384, max_output_bytes - len(output) + 1))
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        output.extend(chunk)
                        if len(output) > max_output_bytes:
                            reason = "output_limit"
                            break
                    if reason != "completed":
                        break
                if reason == "completed":
                    try:
                        process.wait(timeout=max(0.001, deadline - time.monotonic()))
                    except subprocess.TimeoutExpired:
                        reason = "timed_out"
        finally:
            if process.poll() is None:
                controller.terminate(process, grace_seconds=0.5)
            if process.stdout is not None:
                process.stdout.close()
        return BoundedProcessResult(
            process.returncode, bytes(output[:max_output_bytes]).decode("utf-8", "replace"), reason
        )
