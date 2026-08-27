"""Bounded CPU-only adapter for the locally installed Needle finetune CLI."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

from worker.training.backends.base import TrainingBackendError, TrainingContext, TrainingOutcome
from worker.training.process_control import ProcessGroupController, TrainingCancelled


class NeedleTrainingBackend:
    """Executes one Hub-delegated job; it has no routing or promotion authority."""

    name = "needle"

    def __init__(self, *, process_controller: ProcessGroupController | None = None) -> None:
        self._processes = process_controller or ProcessGroupController()

    def availability(self) -> tuple[bool, str | None]:
        binary = self._binary()
        if binary is None:
            return False, "needle executable is unavailable"
        if shutil.which("nice") is None or shutil.which("taskset") is None:
            return False, "nice and taskset are required"
        return True, None

    def prepare(self, context: TrainingContext) -> dict[str, Any]:
        available, detail = self.availability()
        if not available:
            raise TrainingBackendError("dependency_unavailable", detail or "Needle is unavailable")
        if not context.model_path.is_file():
            raise TrainingBackendError("base_checkpoint_unavailable", "Needle requires one admitted checkpoint file")
        if context.request.configuration.max_sequence_length > 256:
            raise TrainingBackendError("resource_policy_denied", "Needle max sequence length is capped at 256")
        cpu_set, cpu_count = self._cpu_set()
        if not 2 <= cpu_count <= 4:
            raise TrainingBackendError("resource_policy_denied", "Needle requires two to four admitted CPU cores")
        context.emit("phase", {"phase": "preparing"})
        return {"binary": self._binary(), "cpu_set": cpu_set, "cpu_count": cpu_count}

    def train(self, context: TrainingContext, prepared: Mapping[str, Any]) -> dict[str, Any]:
        context.artifact_root.mkdir(parents=True, exist_ok=True)
        context.checkpoint_root.mkdir(parents=True, exist_ok=True)
        output = context.artifact_root / "adapter.pkl"
        config = context.request.configuration
        nice = shutil.which("nice")
        taskset = shutil.which("taskset")
        if nice is None or taskset is None:
            raise TrainingBackendError("dependency_unavailable", "nice and taskset are required")
        command = [
            nice,
            "-n",
            "15",
            taskset,
            "--cpu-list",
            str(prepared["cpu_set"]),
            str(prepared["binary"]),
            "finetune",
            str(context.dataset.train_path),
            "--checkpoint",
            str(context.model_path),
            "--epochs",
            str(config.num_train_epochs),
            "--batch-size",
            str(config.train_batch_size),
            "--lr",
            str(config.learning_rate),
            "--lora-rank",
            str(config.lora_rank),
            "--lora-alpha",
            str(config.lora_alpha),
            "--max-len",
            str(config.max_sequence_length),
            "--val-split",
            "0.2",
            "--generate",
            "0",
            "--workers",
            "1",
            "--checkpoint-dir",
            str(context.checkpoint_root),
            "--out",
            str(output),
        ]
        environment = {
            **os.environ,
            "JAX_PLATFORMS": "cpu",
            "XLA_PYTHON_CLIENT_PREALLOCATE": "false",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "NO_PROXY": "*",
        }
        context.emit("phase", {"phase": "training"})
        with tempfile.TemporaryFile() as output_log:
            process = self._processes.start(
                command,
                cwd=str(context.artifact_root),
                env=environment,
                stdout=output_log,
                stderr=subprocess.STDOUT,
            )
            while process.poll() is None:
                if context.cancel.cancelled:
                    termination = self._processes.terminate(process, grace_seconds=10.0)
                    raise TrainingCancelled(
                        "Needle training cancellation requested",
                        forced=termination.forced,
                    )
                time.sleep(0.1)
            if process.returncode != 0:
                raise TrainingBackendError(
                    "training_failed",
                    f"Needle training exited with status {process.returncode}",
                )
        if not output.is_file() or output.stat().st_size < 1:
            raise TrainingBackendError("artifact_save_failed", "Needle did not produce an adapter")
        return {"adapter": output, "sha256": _file_sha256(output)}

    def evaluate(
        self,
        context: TrainingContext,
        prepared: Mapping[str, Any],
        trained: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        del prepared
        context.emit("phase", {"phase": "evaluation_deferred"})
        return {
            "validation_records": context.dataset.validation_records,
            "adapter_sha256": trained["sha256"],
            "independent_evaluation_required": True,
        }

    def save(
        self,
        context: TrainingContext,
        prepared: Mapping[str, Any],
        trained: Mapping[str, Any],
        metrics: Mapping[str, Any],
    ) -> TrainingOutcome:
        del prepared
        metrics_path = context.artifact_root / "evaluation.json"
        metrics_path.write_text(json.dumps(dict(metrics), sort_keys=True), encoding="utf-8")
        return TrainingOutcome(
            metrics=metrics,
            artifacts=(Path(str(trained["adapter"])), metrics_path),
            best_checkpoint=None,
        )

    @staticmethod
    def _binary() -> str | None:
        configured = str(os.getenv("ANANTA_NEEDLE_TRAINING_BIN") or "").strip()
        if configured:
            path = Path(configured)
            return str(path) if path.is_absolute() and path.is_file() and os.access(path, os.X_OK) else None
        return shutil.which("needle")

    @staticmethod
    def _cpu_set() -> tuple[str, int]:
        value = str(os.getenv("ANANTA_NEEDLE_TRAINING_CPU_SET") or "0-3").strip()
        if not re.fullmatch(r"\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*", value):
            raise TrainingBackendError("resource_policy_denied", "Needle CPU set is invalid")
        cpus: set[int] = set()
        for item in value.split(","):
            if "-" in item:
                start, end = (int(part) for part in item.split("-", 1))
                if end < start or end - start > 3:
                    raise TrainingBackendError("resource_policy_denied", "Needle CPU set is invalid")
                cpus.update(range(start, end + 1))
            else:
                cpus.add(int(item))
        return value, len(cpus)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["NeedleTrainingBackend"]
