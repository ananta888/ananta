"""Bounded subprocess boundary shared by optional verification adapters."""

from __future__ import annotations

import os
import resource
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

_ALLOWED_EXECUTABLES = frozenset({"python", "python3", "pytest", "crosshair"})
_DENIED_CROSSHAIR_ARGS = frozenset({"--unblock", "--plugin", "--extra_plugin"})
_CONTROL_ENV = frozenset({"ANANTA_HYPOTHESIS_BACKEND", "ANANTA_HYPOTHESIS_CASES"})


@dataclass(frozen=True, slots=True)
class ProcessObservation:
    returncode: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool
    output_truncated: bool


class VerificationProcessRunner:
    """Executes one allowlisted command with closed environment and hard budgets."""

    def run(
        self,
        command: Sequence[str],
        *,
        repository: Path,
        timeout_seconds: int,
        max_output_bytes: int,
        memory_mb: int,
        extra_env: Mapping[str, str] | None = None,
    ) -> ProcessObservation:
        argv = tuple(str(item) for item in command)
        if not argv or Path(argv[0]).name not in _ALLOWED_EXECUTABLES:
            raise ValueError("verification_executable_denied")
        if Path(argv[0]).name in {"python", "python3"}:
            argv = (sys.executable, *argv[1:])
        is_crosshair = Path(argv[0]).name == "crosshair" or argv[1:3] == ("-m", "crosshair")
        if is_crosshair and any(item in _DENIED_CROSSHAIR_ARGS for item in argv[1:]):
            raise ValueError("verification_crosshair_option_denied")
        root = repository.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("verification_repository_invalid")
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(root),
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "HYPOTHESIS_STORAGE_DIRECTORY": "/tmp/ananta-hypothesis",
            "HOME": "/tmp/ananta-verification-home",
            "NO_PROXY": "*",
            "no_proxy": "*",
        }
        for key, value in dict(extra_env or {}).items():
            if key not in _CONTROL_ENV and (
                key.startswith(("ANANTA_", "SRC_", "RUN_"))
                or any(token in key.upper() for token in ("SECRET", "TOKEN", "PASSWORD", "KEY"))
            ):
                raise ValueError("verification_environment_key_denied")
            env[str(key)] = str(value)
        started = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                cwd=root,
                env=env,
                capture_output=True,
                text=False,
                timeout=timeout_seconds,
                check=False,
                start_new_session=True,
                preexec_fn=lambda: self._limit(memory_mb),
            )
        except subprocess.TimeoutExpired as exc:
            stdout = bytes(exc.stdout or b"")
            stderr = bytes(exc.stderr or b"")
            return self._observation(None, stdout, stderr, started, True, max_output_bytes)
        return self._observation(
            completed.returncode,
            completed.stdout,
            completed.stderr,
            started,
            False,
            max_output_bytes,
        )

    @staticmethod
    def _limit(memory_mb: int) -> None:
        memory = int(memory_mb) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
        # Process/thread count is enforced by the dedicated container pids_limit.
        # RLIMIT_NPROC is per host UID (not per child) and would make behavior
        # depend on unrelated processes owned by the same development user.

    @staticmethod
    def _observation(
        returncode: int | None,
        stdout: bytes,
        stderr: bytes,
        started: float,
        timed_out: bool,
        max_output_bytes: int,
    ) -> ProcessObservation:
        joined = stdout + stderr
        truncated = len(joined) > max_output_bytes
        remaining = max_output_bytes
        stdout = stdout[:remaining]
        remaining -= len(stdout)
        stderr = stderr[: max(0, remaining)]
        return ProcessObservation(
            returncode=returncode,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            timed_out=timed_out,
            output_truncated=truncated,
        )


__all__ = ["ProcessObservation", "VerificationProcessRunner"]
