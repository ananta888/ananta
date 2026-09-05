"""Run one backend attempt in an isolated, cancellable process group."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Mapping

from worker.training.backends.base import TrainingBackendError, TrainingContext, TrainingOutcome
from worker.training.cuda_allocator import CUDA_MEMORY_FRACTION_ENV
from worker.training.evaluation import AdapterEvaluationContext, AdapterEvaluationOutcome
from worker.training.process_control import (
    CancellationToken,
    ProcessGroupController,
    TerminationResult,
    TrainingCancelled,
)

_MAX_RESULT_BYTES = 1024 * 1024
_MAX_EVENT_BYTES = 64 * 1024
_EVENT_PREFIX = b"ANANTA_LORA_EVENT "


class IsolatedBackendExecutor:
    """Adapter from the backend port to a per-attempt child process."""

    def __init__(self, *, termination_grace_seconds: float = 15.0) -> None:
        self._processes = ProcessGroupController()
        self._termination_grace_seconds = termination_grace_seconds
        self._lock = threading.RLock()
        self._running: dict[int, subprocess.Popen[bytes]] = {}

    def cancel(self, token: CancellationToken) -> TerminationResult | None:
        """Cancel one attempt and synchronously contain its process group."""

        token.cancel()
        with self._lock:
            process = self._running.get(id(token))
        if process is not None:
            return self._processes.terminate(process, grace_seconds=self._termination_grace_seconds)
        return None

    def run(self, context: TrainingContext | AdapterEvaluationContext) -> TrainingOutcome | AdapterEvaluationOutcome:
        context.cancel.raise_if_cancelled()
        process_root = context.artifact_root.parent / "process"
        process_root.mkdir(parents=True, exist_ok=True)
        context_path = process_root / "context.json"
        result_path = process_root / "result.json"
        if isinstance(context, TrainingContext):
            payload = {
                "request": context.request.to_dict(),
                "dataset": {
                    "train_path": str(context.dataset.train_path),
                    "validation_path": str(context.dataset.validation_path),
                    "train_records": context.dataset.train_records,
                    "validation_records": context.dataset.validation_records,
                    "dataset_hash": context.dataset.dataset_hash,
                },
                "model_path": str(context.model_path),
                "artifact_root": str(context.artifact_root),
                "checkpoint_root": str(context.checkpoint_root),
                "resume_path": str(context.resume_path) if context.resume_path else None,
                "checkpoint_state_root": (
                    str(context.checkpoint_state_root) if context.checkpoint_state_root else None
                ),
            }
        else:
            payload = {
                "request": context.request.to_dict(),
                "validation_dataset": {
                    "validation_path": str(context.dataset.validation_path),
                    "validation_records": context.dataset.validation_records,
                    "dataset_hash": context.dataset.dataset_hash,
                },
                "model_path": str(context.model_path),
                "adapter_path": str(context.adapter_path),
                "artifact_root": str(context.artifact_root),
            }
        context_path.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        command = [
            sys.executable,
            "-m",
            "worker.training.job_process",
            "--context",
            str(context_path),
            "--result",
            str(result_path),
        ]
        process = self._processes.start(command, cwd=str(Path(__file__).resolve().parents[2]), env=_child_environment())
        token_key = id(context.cancel)
        with self._lock:
            self._running[token_key] = process
        if context.cancel.cancelled:
            termination = self._processes.terminate(
                process,
                grace_seconds=self._termination_grace_seconds,
            )
            with self._lock:
                self._running.pop(token_key, None)
            raise TrainingCancelled("training cancellation requested", forced=termination.forced)
        stdout = process.stdout
        assert stdout is not None
        lines: queue.Queue[bytes | None] = queue.Queue(maxsize=1024)

        def read_events() -> None:
            try:
                for line in iter(stdout.readline, b""):
                    try:
                        lines.put(line, timeout=1)
                    except queue.Full:
                        break
            finally:
                lines.put(None)

        reader = threading.Thread(target=read_events, name="lora-training-event-reader", daemon=True)
        reader.start()
        stream_closed = False
        try:
            while process.poll() is None or not stream_closed:
                if context.cancel.cancelled:
                    termination = self._processes.terminate(
                        process,
                        grace_seconds=self._termination_grace_seconds,
                    )
                    raise TrainingCancelled("training cancellation requested", forced=termination.forced)
                if int(time.time() * 1000) >= context.request.deadline_epoch_ms:
                    self._processes.terminate(process, grace_seconds=self._termination_grace_seconds)
                    raise TimeoutError("training deadline expired")
                try:
                    line = lines.get(timeout=0.1)
                except queue.Empty:
                    continue
                if line is None:
                    stream_closed = True
                    continue
                self._forward_event(context, line)
            return_code = process.wait(timeout=1)
        finally:
            if process.poll() is None:
                self._processes.terminate(process, grace_seconds=self._termination_grace_seconds)
            with self._lock:
                if self._running.get(token_key) is process:
                    self._running.pop(token_key, None)
            reader.join(timeout=1)

        if not result_path.is_file() or result_path.stat().st_size > _MAX_RESULT_BYTES:
            code = "worker_process_killed" if return_code < 0 else "worker_process_failed"
            raise TrainingBackendError(code, "isolated training process produced no valid result", retryable=True)
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise TrainingBackendError(
                "worker_process_failed", "isolated training result is invalid", retryable=True
            ) from exc
        if result.get("status") != "succeeded":
            error = result.get("error") if isinstance(result.get("error"), Mapping) else {}
            raise TrainingBackendError(
                str(error.get("code") or "worker_process_failed"),
                str(error.get("message") or "isolated training process failed"),
                retryable=bool(error.get("retryable", False)),
            )
        artifacts = tuple(Path(item) for item in result.get("artifacts", []) if isinstance(item, str))
        metrics = result.get("metrics") if isinstance(result.get("metrics"), Mapping) else {}
        if isinstance(context, AdapterEvaluationContext):
            return AdapterEvaluationOutcome(metrics=dict(metrics), artifacts=artifacts)
        best = result.get("best_checkpoint")
        return TrainingOutcome(metrics=dict(metrics), artifacts=artifacts, best_checkpoint=Path(best) if best else None)

    @staticmethod
    def _forward_event(context: TrainingContext | AdapterEvaluationContext, raw_line: bytes) -> None:
        # ML frameworks may write diagnostics to stdout/stderr. Only the
        # worker-owned prefix is part of the event contract.
        if not raw_line.startswith(_EVENT_PREFIX):
            return
        raw_line = raw_line[len(_EVENT_PREFIX) :]
        if len(raw_line) > _MAX_EVENT_BYTES:
            raise TrainingBackendError("invalid_backend_event", "isolated backend event exceeds its byte limit")
        try:
            event = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TrainingBackendError("invalid_backend_event", "isolated backend emitted invalid JSON") from exc
        if not isinstance(event, Mapping) or not isinstance(event.get("payload"), Mapping):
            raise TrainingBackendError("invalid_backend_event", "isolated backend emitted an invalid event")
        context.emit(str(event.get("type") or ""), event["payload"])


def _child_environment() -> dict[str, str]:
    """Pass only runtime/device variables; never copy the worker bearer token."""

    names = {
        CUDA_MEMORY_FRACTION_ENV,
        "CUDA_VISIBLE_DEVICES",
        "CUDA_CACHE_PATH",
        "HF_DATASETS_OFFLINE",
        "HF_HOME",
        "HF_HUB_DISABLE_TELEMETRY",
        "HF_HUB_OFFLINE",
        "HOME",
        "LANG",
        "LC_ALL",
        "LD_LIBRARY_PATH",
        "NUMBA_CACHE_DIR",
        "NVIDIA_DRIVER_CAPABILITIES",
        "NVIDIA_VISIBLE_DEVICES",
        "PATH",
        "PYTHONPATH",
        "TMPDIR",
        "TRANSFORMERS_OFFLINE",
        "TRITON_CACHE_DIR",
        "UNSLOTH_COMPILE_LOCATION",
        "XDG_CACHE_HOME",
    }
    environment = {name: value for name in names if (value := os.getenv(name)) is not None}
    environment.setdefault("HF_DATASETS_OFFLINE", "1")
    environment.setdefault("HF_HOME", "/tmp/huggingface")
    environment.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    environment.setdefault("HF_HUB_OFFLINE", "1")
    # Never inherit the container account's non-writable home into the
    # isolated job. Caches are ephemeral and contained by the worker tmpfs.
    environment["HOME"] = "/tmp"
    environment.setdefault("XDG_CACHE_HOME", "/tmp/cache")
    environment.setdefault("TRANSFORMERS_OFFLINE", "1")
    environment.setdefault("PYTHONUNBUFFERED", "1")
    return environment
