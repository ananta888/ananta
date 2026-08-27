"""Bounded process-group runner for one third-party training CLI."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from worker.training.backends.base import TrainingBackendError
from worker.training.process_control import CancellationToken, ProcessGroupController


class ExternalProcessPort(Protocol):
    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        cancel: CancellationToken,
        deadline_epoch_ms: int,
    ) -> None: ...


class BoundedExternalTrainingProcess:
    """Run a worker-owned argv without shell, inherited secrets or raw logs."""

    def __init__(self, *, allowed_executable: Path, poll_seconds: float = 0.1, grace_seconds: float = 10.0) -> None:
        executable = allowed_executable.resolve()
        if not executable.is_absolute():
            raise ValueError("external trainer executable must be absolute")
        self._executable = executable
        self._poll_seconds = max(0.02, min(1.0, poll_seconds))
        self._grace_seconds = max(0.1, min(30.0, grace_seconds))
        self._processes = ProcessGroupController()

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        cancel: CancellationToken,
        deadline_epoch_ms: int,
    ) -> None:
        argv = tuple(command)
        if not 2 <= len(argv) <= 16 or Path(argv[0]).resolve() != self._executable:
            raise TrainingBackendError("config_invalid", "external trainer command is not allowlisted")
        if any(not isinstance(value, str) or not value or len(value) > 4096 or "\x00" in value for value in argv):
            raise TrainingBackendError("config_invalid", "external trainer command is invalid")
        workspace = cwd.resolve()
        if not workspace.is_dir():
            raise TrainingBackendError("config_invalid", "external trainer workspace is unavailable")
        process = self._processes.start(
            argv,
            cwd=str(workspace),
            env=_offline_environment(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            while process.poll() is None:
                if cancel.cancelled:
                    self._processes.terminate(process, grace_seconds=self._grace_seconds)
                    raise TrainingBackendError("cancelled", "external training was cancelled", retryable=False)
                if int(time.time() * 1000) >= deadline_epoch_ms:
                    self._processes.terminate(process, grace_seconds=self._grace_seconds)
                    raise TrainingBackendError("training_failed", "external training deadline expired", retryable=True)
                time.sleep(self._poll_seconds)
            if process.returncode != 0:
                raise TrainingBackendError(
                    "training_failed",
                    "external trainer exited unsuccessfully",
                    retryable=process.returncode in {137, -9},
                )
        finally:
            if process.poll() is None:
                self._processes.terminate(process, grace_seconds=self._grace_seconds)


def _offline_environment() -> Mapping[str, str]:
    allowed_names = {
        "CUDA_VISIBLE_DEVICES",
        "HF_HOME",
        "LANG",
        "LC_ALL",
        "LD_LIBRARY_PATH",
        "NVIDIA_DRIVER_CAPABILITIES",
        "NVIDIA_VISIBLE_DEVICES",
        "PATH",
        "TMPDIR",
    }
    values = {name: value for name in allowed_names if (value := os.getenv(name)) is not None}
    return {
        **values,
        "AXOLOTL_DO_NOT_TRACK": "1",
        "DO_NOT_TRACK": "1",
        "HF_DATASETS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HUB_OFFLINE": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "TRANSFORMERS_OFFLINE": "1",
        "WANDB_DISABLED": "true",
    }


__all__ = ["BoundedExternalTrainingProcess", "ExternalProcessPort"]
