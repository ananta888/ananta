"""Cancellation and process-group containment for individual training jobs."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from dataclasses import dataclass
from typing import IO, Mapping, Sequence


class TrainingCancelled(RuntimeError):
    """Cancellation signal that preserves whether containment required SIGKILL."""

    def __init__(self, message: str, *, forced: bool = False) -> None:
        super().__init__(message)
        self.forced = forced


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise TrainingCancelled("training cancellation requested")


@dataclass(frozen=True)
class TerminationResult:
    return_code: int | None
    forced: bool


class ProcessGroupController:
    """Launch one job in its own group and enforce TERM -> KILL cancellation."""

    def start(
        self,
        command: Sequence[str],
        *,
        cwd: str,
        env: Mapping[str, str],
        stdout: int | IO[bytes] | None = subprocess.PIPE,
        stderr: int | IO[bytes] | None = subprocess.STDOUT,
    ) -> subprocess.Popen[bytes]:
        return subprocess.Popen(  # noqa: S603 - command is constructed by the worker, never the request
            list(command),
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )

    def terminate(self, process: subprocess.Popen[bytes], *, grace_seconds: float = 10.0) -> TerminationResult:
        if process.poll() is not None:
            return TerminationResult(return_code=process.returncode, forced=False)
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return TerminationResult(return_code=process.poll(), forced=False)
        try:
            return TerminationResult(return_code=process.wait(timeout=grace_seconds), forced=False)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return TerminationResult(return_code=process.poll(), forced=False)
            return TerminationResult(return_code=process.wait(timeout=max(1.0, grace_seconds)), forced=True)
