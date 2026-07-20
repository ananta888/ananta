"""Bounded process-group lifecycle for delegated speech-training backends.

The Hub still owns admission, cancellation and fencing.  This worker-local
component has one responsibility: ensure a backend child and all descendants
terminate when that delegated attempt loses authority.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from worker.speech_training.backend import (
    AbortSignal,
    SpeechTrainingAborted,
    SpeechTrainingBackendError,
)


@dataclass(frozen=True, slots=True)
class SpeechChildProcessResult:
    return_code: int
    elapsed_ms: int


class BoundedSpeechChildProcess:
    """Run exactly one allowlisted executable without a shell.

    stdout/stderr are discarded deliberately: model subprocess output may
    contain transcripts, local paths or provider details and therefore never
    becomes a Hub event or worker log.
    """

    def __init__(
        self,
        *,
        allowed_executables: Sequence[Path],
        poll_interval_seconds: float = 0.02,
        termination_grace_seconds: float = 0.2,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        resolved = frozenset(path.resolve() for path in allowed_executables)
        if not resolved or any(not path.is_absolute() for path in resolved):
            raise ValueError("speech_child_process_executable_allowlist_invalid")
        if not 0.005 <= poll_interval_seconds <= 1.0:
            raise ValueError("speech_child_process_poll_interval_invalid")
        if not 0.05 <= termination_grace_seconds <= 10.0:
            raise ValueError("speech_child_process_termination_grace_invalid")
        self._allowed = resolved
        self._poll_interval = poll_interval_seconds
        self._termination_grace = termination_grace_seconds
        self._monotonic = monotonic
        self._lock = threading.RLock()
        self._active_pid: int | None = None
        self._last_pid: int | None = None

    @property
    def active_pid(self) -> int | None:
        with self._lock:
            return self._active_pid

    @property
    def last_pid(self) -> int | None:
        with self._lock:
            return self._last_pid

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        abort: AbortSignal,
        timeout_seconds: float,
        environment: Mapping[str, str] | None = None,
    ) -> SpeechChildProcessResult:
        command = self._command(argv)
        workspace = cwd.resolve()
        if not workspace.is_dir():
            raise SpeechTrainingBackendError(
                "speech_child_process_workspace_invalid",
                "speech backend child workspace is unavailable",
            )
        if not 0.1 <= timeout_seconds <= 8 * 60 * 60:
            raise SpeechTrainingBackendError(
                "speech_child_process_timeout_invalid",
                "speech backend child timeout is outside its bound",
            )
        env = self._environment(environment)
        started = self._monotonic()
        process = subprocess.Popen(  # noqa: S603 - exact argv + executable allowlist, never a shell
            command,
            cwd=workspace,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
        with self._lock:
            if self._active_pid is not None:
                self._terminate(process)
                raise SpeechTrainingBackendError(
                    "speech_child_process_already_active",
                    "speech backend attempted parallel child execution",
                )
            self._active_pid = process.pid
            self._last_pid = process.pid
        try:
            while True:
                return_code = process.poll()
                if return_code is not None:
                    if return_code != 0:
                        raise SpeechTrainingBackendError(
                            "speech_child_process_failed",
                            "speech backend child exited unsuccessfully",
                        )
                    return SpeechChildProcessResult(
                        return_code=return_code,
                        elapsed_ms=max(0, int((self._monotonic() - started) * 1000)),
                    )
                if abort.aborted:
                    self._terminate(process)
                    raise SpeechTrainingAborted(
                        abort.reason_code,
                        "speech backend child lost its Hub-authorized attempt",
                    )
                if self._monotonic() - started >= timeout_seconds:
                    self._terminate(process)
                    raise SpeechTrainingAborted(
                        "speech_wall_time_exceeded",
                        "speech backend child exceeded admitted wall time",
                    )
                time.sleep(self._poll_interval)
        finally:
            if process.poll() is None:
                self._terminate(process)
            with self._lock:
                if self._active_pid == process.pid:
                    self._active_pid = None

    def _command(self, argv: Sequence[str]) -> tuple[str, ...]:
        if not 1 <= len(argv) <= 64 or any(
            not isinstance(value, str) or not value or len(value) > 4096 or "\x00" in value
            for value in argv
        ):
            raise SpeechTrainingBackendError(
                "speech_child_process_command_invalid",
                "speech backend child command is invalid",
            )
        executable = Path(argv[0]).resolve()
        if executable not in self._allowed or not executable.is_file():
            raise SpeechTrainingBackendError(
                "speech_child_process_executable_forbidden",
                "speech backend child executable is not allowlisted",
            )
        return (str(executable), *(str(value) for value in argv[1:]))

    @staticmethod
    def _environment(values: Mapping[str, str] | None) -> dict[str, str]:
        allowed = {
            "HF_HOME",
            "HF_HUB_OFFLINE",
            "OMP_NUM_THREADS",
            "TOKENIZERS_PARALLELISM",
            "TRANSFORMERS_OFFLINE",
        }
        supplied = dict(values or {})
        if set(supplied) - allowed or any(
            not isinstance(value, str) or len(value) > 4096 or "\x00" in value
            for value in supplied.values()
        ):
            raise SpeechTrainingBackendError(
                "speech_child_process_environment_forbidden",
                "speech backend child environment is not allowlisted",
            )
        return {
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            **supplied,
        }

    def _terminate(self, process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=self._termination_grace)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=self._termination_grace)
        except subprocess.TimeoutExpired as exc:  # pragma: no cover - kernel/process invariant
            raise SpeechTrainingBackendError(
                "speech_child_process_kill_failed",
                "speech backend child ignored hard termination",
            ) from exc


__all__ = ["BoundedSpeechChildProcess", "SpeechChildProcessResult"]
