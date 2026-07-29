"""Bounded asynchronous runtime owned by the isolated training worker."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ananta_contracts.unsloth_capability import (
    compose_worker_capability_probe,
    progress_telemetry,
)
from worker.training.backends.base import (
    TrainingBackend,
    TrainingBackendError,
    TrainingContext,
    TrainingOutcome,
    run_backend,
)
from worker.training.contracts import (
    CONTRACT_VERSION,
    AdapterEvaluationJobRequest,
    JobRequest,
    TrainingContractError,
    TrainingJobRequest,
    parse_job_request,
)
from worker.training.datasets import DatasetValidator
from worker.training.evaluation import (
    AdapterEvaluationContext,
    AdapterEvaluationOutcome,
    evaluator_for_backend,
)
from worker.training.process_control import CancellationToken, TrainingCancelled
from worker.training.subprocess_executor import IsolatedBackendExecutor

TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
ACTIVE_STATUSES = frozenset({"queued", "running", "cancel_requested"})
_EVENT_MODALITIES = frozenset({"text", "vision", "audio", "embedding"})
_RESOURCE_ADMISSION_PAYLOAD_KEYS = frozenset(
    {
        "profile",
        "admitted",
        "estimated_peak_bytes",
        "usable_bytes",
        "reserve_bytes",
        "assumptions",
        "estimate_only",
        "reason_code",
    }
)
_EVENT_PAYLOAD_KEYS = {
    "accepted": frozenset({"backend"}),
    "status": frozenset({"status", "reason_code", "retryable"}),
    "phase": frozenset({"phase", "step", "modality"}),
    "progress": frozenset(
        {
            "step",
            "max_steps",
            "epoch",
            "loss",
            "eval_loss",
            "learning_rate",
            "tokens_per_second",
            "gpu_utilization_percent",
            "vram_used_bytes",
            "telemetry",
        }
    ),
    "checkpoint": frozenset({"step", "name", "sha256"}),
    "artifact": frozenset({"name", "sha256", "size_bytes", "media_type"}),
    "resource_admission": _RESOURCE_ADMISSION_PAYLOAD_KEYS,
}


class TrainingRuntimeError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 422,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.retryable = retryable


class JobDeadlineExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeConfiguration:
    state_root: Path
    workspace_root: Path
    dataset_root: Path
    model_root: Path
    resource_profile: str = "mock"
    max_workers: int = 1
    max_queue: int = 2
    max_dataset_bytes: int = 4 * 1024**3
    max_dataset_records: int = 10_000_000
    max_model_bytes: int = 20 * 1024**3
    max_checkpoint_bytes: int = 8 * 1024**3
    max_export_bytes: int = 20 * 1024**3
    max_tenant_bytes: int = 64 * 1024**3
    isolate_processes: bool = True
    termination_grace_seconds: float = 15.0


@dataclass
class _Job:
    request: JobRequest
    status: str
    created_at: float
    updated_at: float
    heartbeat_at: float
    sequence: int = 0
    progress: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    error: dict[str, Any] | None = None
    resume_checkpoint: dict[str, Any] | None = None
    cancel_mode: str | None = None
    cancel: CancellationToken = field(default_factory=CancellationToken, repr=False)
    future: Future[None] | None = field(default=None, repr=False)


class TrainingWorkerRuntime:
    """Execute admitted jobs; it contains no queue ownership or Hub routing."""

    def __init__(self, config: RuntimeConfiguration, backends: Mapping[str, TrainingBackend]) -> None:
        self._config = config
        self._backends = {str(name).lower(): backend for name, backend in backends.items()}
        self._lock = threading.RLock()
        self._jobs: dict[str, _Job] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, config.max_workers),
            thread_name_prefix="lora-training",
        )
        self._isolated_executor = IsolatedBackendExecutor(
            termination_grace_seconds=max(0.1, config.termination_grace_seconds)
        )
        self._worker_instance_id = str(uuid.uuid4())
        self._configuration_errors = self._configuration_failures()
        self._capability_cache: tuple[float, dict[str, Any]] | None = None
        if not self._configuration_errors:
            self._load_persisted_jobs()

    def health(self) -> dict[str, Any]:
        with self._lock:
            active = sum(job.status in ACTIVE_STATUSES for job in self._jobs.values())
            backend_health = {
                name: {"available": available, "detail": detail}
                for name, backend in self._backends.items()
                for available, detail in [backend.availability()]
            }
            errors = list(self._configuration_errors)
            errors.extend(
                f"backend {name}: {state['detail']}" for name, state in backend_health.items() if not state["available"]
            )
            return {
                "contract_version": CONTRACT_VERSION,
                "status": "ready" if not errors else "degraded",
                "runtime_configured": not self._configuration_errors,
                "resource_profile": self._config.resource_profile,
                "process_isolation": self._config.isolate_processes,
                "worker_instance_id": self._worker_instance_id,
                "backends": backend_health,
                "capacity": {
                    "active": active,
                    "max_running": self._config.max_workers,
                    "max_queue": self._config.max_queue,
                },
                "errors": errors,
            }

    def capability_probe(self) -> dict[str, Any]:
        """Return a cached, bounded, Worker-owned dependency/hardware probe."""

        with self._lock:
            if self._capability_cache is not None:
                cached_at, cached = self._capability_cache
                if time.monotonic() - cached_at < 30.0:
                    return json.loads(json.dumps(cached))
            backend_availability = {
                name: backend.availability()
                for name, backend in self._backends.items()
            }
            default_gpu_profile = (
                "none" if self._config.resource_profile in {"mock", "cpu"} else "generic-safe"
            )
            active_gpu_profile = str(
                os.getenv("ANANTA_LORA_TRAINING_GPU_PROFILE", default_gpu_profile)
            ).strip().lower()
            probe = compose_worker_capability_probe(
                contract_version=CONTRACT_VERSION,
                resource_profile=self._config.resource_profile,
                active_gpu_profile=active_gpu_profile,
                backend_availability=backend_availability,
                package_versions=self._package_versions(),
                hardware=self._bounded_torch_hardware_probe(),
                runtime_ready=not self._configuration_errors,
                runtime_reason_code="configuration_invalid",
            )
            self._capability_cache = (time.monotonic(), probe)
            return json.loads(json.dumps(probe))

    def submit(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        self._ensure_ready()
        request = parse_job_request(envelope)
        if request.resource_profile != self._config.resource_profile:
            raise TrainingRuntimeError(
                "resource_profile_mismatch",
                "job resource profile does not match this worker",
                http_status=409,
                retryable=True,
            )
        if isinstance(request, TrainingJobRequest):
            request.validate_resume_binding()
        if request.deadline_epoch_ms <= int(time.time() * 1000):
            raise TrainingRuntimeError("deadline_expired", "job deadline has already expired", http_status=408)
        backend = self._backends.get(request.backend)
        if backend is None:
            raise TrainingRuntimeError(
                "backend_unavailable", f"backend {request.backend} is not enabled", http_status=503
            )
        available, detail = backend.availability()
        if not available:
            raise TrainingRuntimeError(
                "dependency_unavailable",
                detail or f"backend {request.backend} is unavailable",
                http_status=503,
            )

        with self._lock:
            existing = self._jobs.get(request.job_id)
            if existing is not None:
                if existing.request.request_hash == request.request_hash:
                    return self._public_status(existing)
                self._admit_retry(existing, request)
            active = sum(job.status in ACTIVE_STATUSES for job in self._jobs.values())
            if active >= self._config.max_workers + self._config.max_queue:
                raise TrainingRuntimeError(
                    "queue_full", "training worker queue is full", http_status=429, retryable=True
                )
            now = time.time()
            job = _Job(request=request, status="queued", created_at=now, updated_at=now, heartbeat_at=now)
            self._jobs[request.job_id] = job
            self._job_root(job).mkdir(parents=True, exist_ok=False)
            self._append_event(job, "accepted", {"backend": request.backend})
            self._persist(job)
            job.future = self._executor.submit(self._execute, request.job_id, request.attempt_id, request.fencing_token)
            return self._public_status(job)

    def status(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            return self._public_status(self._require_job(job_id))

    def heartbeat(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._require_job(job_id)
            if job.status in ACTIVE_STATUSES:
                job.heartbeat_at = time.time()
                job.updated_at = job.heartbeat_at
                self._persist(job)
            return self._public_status(job)

    def events(self, job_id: str, *, after_sequence: int = 0, limit: int = 100) -> dict[str, Any]:
        if after_sequence < 0 or not 1 <= limit <= 1000:
            raise TrainingRuntimeError("invalid_pagination", "event cursor or limit is invalid")
        with self._lock:
            job = self._require_job(job_id)
            event_path = self._event_path(job)
            events: list[dict[str, Any]] = []
            if event_path.exists():
                with event_path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if int(event.get("sequence", 0)) > after_sequence:
                            events.append(event)
                            if len(events) >= limit:
                                break
            return {
                "contract_version": CONTRACT_VERSION,
                "job_id": job.request.job_id,
                "attempt_id": job.request.attempt_id,
                "events": events,
                "next_sequence": events[-1]["sequence"] if events else after_sequence,
            }

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._require_job(job_id)
            if job.status in TERMINAL_STATUSES:
                return self._public_status(job)
            job.cancel.cancel()
            if job.status == "queued" and job.future is not None and job.future.cancel():
                job.cancel_mode = "graceful"
                self._transition(job, "cancelled", reason_code="cancelled", retryable=False)
            elif job.status != "cancel_requested":
                job.status = "cancel_requested"
                job.updated_at = time.time()
                self._append_event(
                    job, "status", {"status": "cancel_requested", "reason_code": "cancelled", "retryable": False}
                )
                self._persist(job)
            return self._public_status(job)

    def artifact(self, job_id: str, artifact_name: str) -> tuple[Path, dict[str, Any]]:
        with self._lock:
            job = self._require_job(job_id)
            metadata = next((item for item in job.artifacts if item["name"] == artifact_name), None)
            if metadata is None:
                raise TrainingRuntimeError("artifact_not_found", "artifact does not exist", http_status=404)
            root = self._artifact_root(job).resolve()
            unresolved = root / artifact_name
            if unresolved.is_symlink():
                raise TrainingRuntimeError("artifact_not_found", "artifact does not exist", http_status=404)
            candidate = unresolved.resolve()
            try:
                candidate.relative_to(root)
            except ValueError as exc:
                raise TrainingRuntimeError("artifact_not_found", "artifact does not exist", http_status=404) from exc
            if not candidate.is_file() or candidate.is_symlink():
                raise TrainingRuntimeError("artifact_not_found", "artifact does not exist", http_status=404)
            if _file_sha256(candidate) != metadata["sha256"]:
                raise TrainingRuntimeError(
                    "artifact_hash_mismatch", "artifact integrity verification failed", http_status=409
                )
            return candidate, dict(metadata)

    def cleanup(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        from worker.training.storage_cleanup import (
            WorkerStorageCleanupExecutor,
        )

        return WorkerStorageCleanupExecutor(
            state_root=self._config.state_root,
            workspace_root=self._config.workspace_root,
        ).execute(envelope)

    def close(self) -> None:
        with self._lock:
            for job in self._jobs.values():
                if job.status in ACTIVE_STATUSES:
                    job.cancel.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _execute(self, job_id: str, attempt_id: str, fencing_token: int) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not self._is_current(job, attempt_id, fencing_token) or job is None or job.status in TERMINAL_STATUSES:
                return
            if job.cancel.cancelled:
                job.cancel_mode = "graceful"
                self._transition(job, "cancelled", reason_code="cancelled", retryable=False)
                return
            job.status = "running"
            job.updated_at = job.heartbeat_at = time.time()
            self._append_event(job, "status", {"status": "running", "reason_code": "", "retryable": False})
            self._persist(job)

        try:
            self._check_deadline(job)
            workspace = self._resolve_within(
                self._config.workspace_root, job.request.workspace_ref, "workspace_missing"
            )
            expected_workspace = self._workspace_relative(job)
            if (
                job.request.workspace_ref.startswith("tenants/")
                and job.request.workspace_ref != expected_workspace
            ):
                raise TrainingContractError(
                    "workspace_scope_binding_mismatch",
                    "workspace_ref is not bound to the current tenant and attempt",
                )
            if not workspace.is_dir():
                raise TrainingContractError(
                    "workspace_missing", "workspace_ref does not identify an admitted workspace"
                )
            model_path = self._resolve_within(
                self._config.model_root, job.request.base_model.relative_path, "model_missing"
            )
            if not model_path.exists():
                raise TrainingContractError("model_missing", "base model snapshot is unavailable")
            self._enforce_path_quota(
                model_path,
                self._config.max_model_bytes,
                "model_quota_exceeded",
            )
            if _path_sha256(model_path) != job.request.base_model.snapshot_hash:
                raise TrainingContractError(
                    "base_model_hash_mismatch",
                    "base model snapshot does not match the admitted catalog hash",
                )
            self._write_model_binding(job)
            self._enforce_dataset_scope_and_quota(job)
            validator = DatasetValidator(
                self._config.dataset_root,
                max_split_bytes=self._config.max_dataset_bytes,
                max_records=self._config.max_dataset_records,
            )
            artifact_root = self._artifact_root(job)
            artifact_root.mkdir(parents=True, exist_ok=True)
            outcome: TrainingOutcome | AdapterEvaluationOutcome
            dataset: Any
            if isinstance(job.request, TrainingJobRequest):
                outcome, dataset = self._run_training_attempt(
                    job,
                    validator,
                    model_path,
                    job_id,
                    attempt_id,
                    fencing_token,
                )
            else:
                outcome, dataset = self._run_evaluation_attempt(
                    job,
                    validator,
                    workspace,
                    model_path,
                    job_id,
                    attempt_id,
                    fencing_token,
                )
            self._check_deadline(job)
            with self._lock:
                if not self._is_current(job, attempt_id, fencing_token) or job.status in TERMINAL_STATUSES:
                    return
                artifacts = self._admit_artifacts(job, outcome)
                if isinstance(job.request, TrainingJobRequest):
                    assert isinstance(outcome, TrainingOutcome)
                    manifest = self._write_training_manifest(job, dataset, outcome, artifacts)
                else:
                    assert isinstance(outcome, AdapterEvaluationOutcome)
                    manifest = self._write_evaluation_manifest(job, dataset, outcome, artifacts)
                artifacts.append(self._artifact_metadata(job, manifest))
                self._enforce_storage_quotas(job)
                job.artifacts = artifacts
                job.metrics = dict(outcome.metrics)
                if (
                    isinstance(outcome, TrainingOutcome)
                    and outcome.best_checkpoint is not None
                    and outcome.best_checkpoint.exists()
                ):
                    job.resume_checkpoint = self._checkpoint_metadata(job, outcome.best_checkpoint)
                for artifact in artifacts:
                    self._append_event(job, "artifact", artifact)
                self._transition(job, "succeeded", reason_code="", retryable=False)
        except TrainingCancelled as exc:
            with self._lock:
                if self._is_current(job, attempt_id, fencing_token) and job.status not in TERMINAL_STATUSES:
                    job.cancel_mode = "forced" if exc.forced else "graceful"
                    self._transition(
                        job,
                        "cancelled",
                        reason_code="forced_cancel" if exc.forced else "cancelled",
                        retryable=False,
                    )
        except JobDeadlineExceeded:
            self._fail(job, attempt_id, fencing_token, "timeout", "training deadline expired", retryable=True)
        except TimeoutError:
            self._fail(job, attempt_id, fencing_token, "timeout", "training deadline expired", retryable=True)
        except TrainingContractError as exc:
            self._fail(job, attempt_id, fencing_token, exc.code, exc.message, retryable=exc.retryable)
        except TrainingBackendError as exc:
            self._fail(job, attempt_id, fencing_token, exc.code, exc.message, retryable=exc.retryable)
        except FileNotFoundError:
            self._fail(
                job,
                attempt_id,
                fencing_token,
                "resource_missing",
                "an admitted local resource is missing",
                retryable=False,
            )
        except MemoryError:
            self._fail(job, attempt_id, fencing_token, "out_of_memory", "training exhausted memory", retryable=True)
        except Exception:
            self._fail(
                job, attempt_id, fencing_token, "internal_error", "training worker failed unexpectedly", retryable=True
            )

    def _run_training_attempt(
        self,
        job: _Job,
        validator: DatasetValidator,
        model_path: Path,
        job_id: str,
        attempt_id: str,
        fencing_token: int,
    ) -> tuple[TrainingOutcome, Any]:
        request = job.request
        assert isinstance(request, TrainingJobRequest)
        job.cancel.raise_if_cancelled()
        dataset = validator.validate(request.dataset)
        checkpoint_root = self._checkpoint_root(job)
        checkpoint_root.mkdir(parents=True, exist_ok=True)
        context = TrainingContext(
            request=request,
            dataset=dataset,
            model_path=model_path,
            artifact_root=self._artifact_root(job),
            checkpoint_root=checkpoint_root,
            resume_path=self._resume_path(job),
            cancel=job.cancel,
            emit=lambda event_type, payload: self._backend_event(
                job_id,
                attempt_id,
                fencing_token,
                event_type,
                payload,
            ),
            checkpoint_state_root=self._config.state_root,
        )
        backend = self._backends[request.backend]
        if self._config.isolate_processes:
            outcome = self._isolated_executor.run(context)
            assert isinstance(outcome, TrainingOutcome)
            return outcome, dataset
        return run_backend(backend, context), dataset

    def _run_evaluation_attempt(
        self,
        job: _Job,
        validator: DatasetValidator,
        workspace: Path,
        model_path: Path,
        job_id: str,
        attempt_id: str,
        fencing_token: int,
    ) -> tuple[AdapterEvaluationOutcome, Any]:
        request = job.request
        assert isinstance(request, AdapterEvaluationJobRequest)
        dataset = validator.validate_validation(request.validation_dataset)
        adapter_path = self._resolve_within(workspace, request.adapter.relative_path, "adapter_missing")
        if not adapter_path.exists():
            raise TrainingContractError("adapter_missing", "adapter artifact is unavailable")
        if _path_sha256(adapter_path) != request.adapter.sha256:
            raise TrainingContractError("adapter_hash_mismatch", "adapter SHA-256 does not match its contract")
        context = AdapterEvaluationContext(
            request=request,
            dataset=dataset,
            model_path=model_path,
            adapter_path=adapter_path,
            artifact_root=self._artifact_root(job),
            cancel=job.cancel,
            emit=lambda event_type, payload: self._backend_event(
                job_id,
                attempt_id,
                fencing_token,
                event_type,
                payload,
            ),
        )
        if self._config.isolate_processes:
            outcome = self._isolated_executor.run(context)
            assert isinstance(outcome, AdapterEvaluationOutcome)
            return outcome, dataset
        return evaluator_for_backend(request.backend).evaluate_existing_adapter(context), dataset

    def _backend_event(
        self,
        job_id: str,
        attempt_id: str,
        fencing_token: int,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not self._is_current(job, attempt_id, fencing_token) or job is None or job.status in TERMINAL_STATUSES:
                raise TrainingBackendError("stale_fence", "late or fenced backend event rejected")
            self._check_deadline(job)
            if job.cancel.cancelled:
                job.cancel.raise_if_cancelled()
            allowed = _EVENT_PAYLOAD_KEYS.get(event_type)
            if allowed is None or not set(payload).issubset(allowed):
                raise TrainingBackendError("invalid_backend_event", "backend event contains unsupported fields")
            if event_type == "resource_admission":
                clean = _safe_resource_admission_payload(payload)
            elif event_type == "progress":
                clean = {
                    key: _safe_scalar(value)
                    for key, value in payload.items()
                    if key != "telemetry"
                }
                clean["telemetry"] = progress_telemetry(clean)
            else:
                clean = {key: _safe_scalar(value) for key, value in payload.items()}
            if event_type == "phase":
                modality = clean.get("modality")
                if modality is not None and modality not in _EVENT_MODALITIES:
                    raise TrainingBackendError(
                        "invalid_backend_event",
                        "backend phase event contains an unsupported modality",
                    )
            if event_type == "progress":
                if not isinstance(job.request, TrainingJobRequest):
                    raise TrainingBackendError("invalid_progress", "evaluation jobs cannot emit training progress")
                step = int(clean.get("step", 0))
                max_steps = int(clean.get("max_steps", 0))
                previous = int(job.progress.get("step", 0))
                if step < previous or step < 0 or max_steps != job.request.configuration.max_steps or step > max_steps:
                    raise TrainingBackendError(
                        "invalid_progress", "backend progress is not monotone or exceeds its admitted bound"
                    )
                job.progress = clean
            elif event_type == "checkpoint":
                checkpoint = self._resolve_within(
                    self._checkpoint_root(job),
                    str(clean.get("name") or ""),
                    "checkpoint_missing",
                )
                if not checkpoint.exists():
                    raise TrainingBackendError("checkpoint_missing", "backend checkpoint does not exist")
                checkpoint_metadata = self._checkpoint_metadata(job, checkpoint)
                checkpoint_digest = checkpoint_metadata["binding"]["checkpoint_sha256"]
                supplied_digest = str(clean.get("sha256") or "")
                if supplied_digest and supplied_digest != checkpoint_digest:
                    raise TrainingBackendError(
                        "checkpoint_hash_mismatch",
                        "backend checkpoint digest does not match the persisted checkpoint",
                    )
                clean["sha256"] = checkpoint_digest
                job.resume_checkpoint = checkpoint_metadata
            job.updated_at = job.heartbeat_at = time.time()
            self._append_event(job, event_type, clean)
            self._persist(job)

    def _fail(
        self,
        job: _Job,
        attempt_id: str,
        fencing_token: int,
        code: str,
        message: str,
        *,
        retryable: bool,
    ) -> None:
        with self._lock:
            if self._is_current(job, attempt_id, fencing_token) and job.status not in TERMINAL_STATUSES:
                job.error = {"code": code, "message": message, "retryable": retryable}
                self._transition(job, "failed", reason_code=code, retryable=retryable)

    def _transition(self, job: _Job, status: str, *, reason_code: str, retryable: bool) -> None:
        if job.status in TERMINAL_STATUSES:
            return
        job.status = status
        job.updated_at = job.heartbeat_at = time.time()
        self._append_event(job, "status", {"status": status, "reason_code": reason_code, "retryable": retryable})
        self._persist(job)

    def _append_event(self, job: _Job, event_type: str, payload: Mapping[str, Any]) -> None:
        job.sequence += 1
        event = {
            "contract_version": CONTRACT_VERSION,
            "sequence": job.sequence,
            "timestamp": time.time(),
            "job_id": job.request.job_id,
            "attempt_id": job.request.attempt_id,
            "fencing_token": job.request.fencing_token,
            "correlation_id": job.request.correlation_id,
            "type": event_type,
            "payload": dict(payload),
        }
        path = self._event_path(job)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _persist(self, job: _Job) -> None:
        path = self._status_path(job)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "request": job.request.to_dict(),
            "request_hash": job.request.request_hash,
            "status": job.status,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "heartbeat_at": job.heartbeat_at,
            "sequence": job.sequence,
            "progress": job.progress,
            "metrics": job.metrics,
            "artifacts": job.artifacts,
            "error": job.error,
            "resume_checkpoint": job.resume_checkpoint,
            "cancel_mode": job.cancel_mode,
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        temporary.replace(path)

    def _load_persisted_jobs(self) -> None:
        jobs_root = self._config.state_root / "jobs"
        tenants_root = self._config.state_root / "tenants"
        if not jobs_root.exists() and not tenants_root.exists():
            return
        loaded: list[_Job] = []
        status_paths = (
            *jobs_root.glob("*/attempts/*/status.json"),
            *tenants_root.glob("*/jobs/*/attempts/*/status.json"),
        )
        for status_path in sorted(status_paths):
            try:
                payload = json.loads(status_path.read_text(encoding="utf-8"))
                request = parse_job_request(payload["request"])
                job = _Job(
                    request=request,
                    status=str(payload["status"]),
                    created_at=float(payload["created_at"]),
                    updated_at=float(payload["updated_at"]),
                    heartbeat_at=float(payload["heartbeat_at"]),
                    sequence=int(payload.get("sequence", 0)),
                    progress=dict(payload.get("progress") or {}),
                    metrics=dict(payload.get("metrics") or {}),
                    artifacts=list(payload.get("artifacts") or []),
                    error=payload.get("error"),
                    resume_checkpoint=payload.get("resume_checkpoint"),
                    cancel_mode=(
                        str(payload["cancel_mode"])
                        if payload.get("cancel_mode") in {"graceful", "forced"}
                        else None
                    ),
                )
                if job.status in ACTIVE_STATUSES:
                    job.error = {
                        "code": "worker_restarted",
                        "message": "worker restarted before the attempt reached a terminal state",
                        "retryable": True,
                    }
                    self._transition(job, "failed", reason_code="worker_restarted", retryable=True)
                loaded.append(job)
            except (KeyError, OSError, TypeError, ValueError, TrainingContractError):
                continue
        for job in loaded:
            current = self._jobs.get(job.request.job_id)
            if current is None or job.request.fencing_token > current.request.fencing_token:
                self._jobs[job.request.job_id] = job

    def _admit_retry(self, existing: _Job, request: JobRequest) -> None:
        if existing.status not in ACTIVE_STATUSES | {"failed", "cancelled"}:
            raise TrainingRuntimeError(
                "job_conflict",
                "job_id already has a non-retryable attempt",
                http_status=409,
            )
        if request.fencing_token <= existing.request.fencing_token or request.attempt_id == existing.request.attempt_id:
            raise TrainingRuntimeError(
                "stale_fence", "retry attempt must advance attempt_id and fencing_token", http_status=409
            )
        immutable_fields = ["job_type", "backend", "base_model", "configuration"]
        if isinstance(request, TrainingJobRequest) and isinstance(existing.request, TrainingJobRequest):
            immutable_fields.extend(("dataset", "exports"))
        elif isinstance(request, AdapterEvaluationJobRequest) and isinstance(
            existing.request, AdapterEvaluationJobRequest
        ):
            immutable_fields.extend(("adapter", "validation_dataset"))
        else:
            raise TrainingRuntimeError("job_conflict", "retry attempt changed job type", http_status=409)
        if any(getattr(existing.request, field) != getattr(request, field) for field in immutable_fields):
            raise TrainingRuntimeError(
                "job_conflict",
                "retry attempt changed immutable training inputs",
                http_status=409,
            )
        if existing.status in ACTIVE_STATUSES:
            existing.cancel.cancel()
            termination = self._isolated_executor.cancel(existing.cancel)
            if termination is not None:
                existing.cancel_mode = "forced" if termination.forced else "graceful"
            if existing.future is not None and existing.status == "queued":
                existing.future.cancel()
            existing.error = {
                "code": "superseded_by_higher_fence",
                "message": "attempt was superseded by a higher fencing token",
                "retryable": False,
            }
            self._transition(
                existing,
                "cancelled",
                reason_code="superseded_by_higher_fence",
                retryable=False,
            )

    def _configuration_failures(self) -> list[str]:
        errors: list[str] = []
        if self._config.resource_profile not in {"mock", "cpu", "nvidia"}:
            errors.append("resource profile must be mock, cpu, or nvidia")
        if self._config.max_workers < 1 or self._config.max_queue < 0:
            errors.append("worker and queue capacity are invalid")
        if any(
            value < 1
            for value in (
                self._config.max_dataset_bytes,
                self._config.max_dataset_records,
                self._config.max_model_bytes,
                self._config.max_checkpoint_bytes,
                self._config.max_export_bytes,
                self._config.max_tenant_bytes,
            )
        ):
            errors.append("worker storage quotas are invalid")
        if self._config.max_tenant_bytes < max(
            self._config.max_model_bytes,
            self._config.max_checkpoint_bytes,
            self._config.max_export_bytes,
        ):
            errors.append("worker tenant quota is smaller than an artifact quota")
        if not self._backends:
            errors.append("no training backend is enabled")
        for name, root, writable in (
            ("state", self._config.state_root, True),
            ("workspace", self._config.workspace_root, False),
            ("dataset", self._config.dataset_root, False),
            ("model", self._config.model_root, False),
        ):
            if not root.exists() or not root.is_dir():
                errors.append(f"{name} root is unavailable")
            elif writable and not os.access(root, os.W_OK | os.X_OK):
                errors.append(f"{name} root is not writable")
            elif not os.access(root, os.R_OK | os.X_OK):
                errors.append(f"{name} root is not readable")
        if self._config.resource_profile == "nvidia" and str(os.getenv("NVIDIA_VISIBLE_DEVICES", "")).lower() in {
            "",
            "none",
            "void",
        }:
            errors.append("NVIDIA resource profile has no visible device")
        return errors

    def _ensure_ready(self) -> None:
        health = self.health()
        if health["status"] != "ready":
            raise TrainingRuntimeError(
                "worker_degraded", "training worker is not ready", http_status=503, retryable=True
            )

    def _require_job(self, job_id: str) -> _Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise TrainingRuntimeError("job_not_found", "training job does not exist", http_status=404)
        return job

    def _is_current(self, job: _Job | None, attempt_id: str, fencing_token: int) -> bool:
        return bool(
            job
            and self._jobs.get(job.request.job_id) is job
            and job.request.attempt_id == attempt_id
            and job.request.fencing_token == fencing_token
        )

    @staticmethod
    def _check_deadline(job: _Job) -> None:
        if int(time.time() * 1000) >= job.request.deadline_epoch_ms:
            raise JobDeadlineExceeded()

    def _public_status(self, job: _Job) -> dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "job_id": job.request.job_id,
            "attempt_id": job.request.attempt_id,
            "fencing_token": job.request.fencing_token,
            "correlation_id": job.request.correlation_id,
            "job_type": job.request.job_type,
            "backend": job.request.backend,
            "status": job.status,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "heartbeat_at": job.heartbeat_at,
            "progress": dict(job.progress),
            "metrics": dict(job.metrics),
            "artifacts": [dict(item) for item in job.artifacts],
            "resume_checkpoint": dict(job.resume_checkpoint) if job.resume_checkpoint else None,
            "storage_usage": self._storage_usage(job),
            "cancel_mode": job.cancel_mode,
            "error": dict(job.error) if job.error else None,
        }

    def _admit_artifacts(
        self,
        job: _Job,
        outcome: TrainingOutcome | AdapterEvaluationOutcome,
    ) -> list[dict[str, Any]]:
        if not outcome.artifacts:
            raise TrainingBackendError("artifact_missing", "worker backend produced no result artifacts")
        return [self._artifact_metadata(job, path) for path in outcome.artifacts]

    def _artifact_metadata(self, job: _Job, path: Path) -> dict[str, Any]:
        root = self._artifact_root(job).resolve()
        if path.is_symlink():
            raise TrainingBackendError("artifact_boundary_violation", "symbolic-link artifacts are forbidden")
        resolved = path.resolve()
        try:
            name = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise TrainingBackendError("artifact_boundary_violation", "backend artifact escaped its job root") from exc
        if not resolved.is_file() or resolved.is_symlink():
            raise TrainingBackendError("artifact_missing", "backend artifact is not a regular file")
        return {
            "name": name,
            "sha256": _file_sha256(resolved),
            "size_bytes": resolved.stat().st_size,
            "media_type": _media_type(resolved),
        }

    def _write_training_manifest(
        self,
        job: _Job,
        dataset: Any,
        outcome: TrainingOutcome,
        artifacts: list[dict[str, Any]],
    ) -> Path:
        assert isinstance(job.request, TrainingJobRequest)
        manifest = self._artifact_root(job) / "training_manifest.json"
        package_versions: dict[str, str] = {}
        for package in ("torch", "transformers", "datasets", "peft", "trl", "safetensors", "unsloth"):
            try:
                package_versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                continue
        payload = {
            "schema_version": "ananta.lora-training-manifest.v1",
            "contract_version": CONTRACT_VERSION,
            "job_id": job.request.job_id,
            "attempt_id": job.request.attempt_id,
            "fencing_token": job.request.fencing_token,
            "correlation_id": job.request.correlation_id,
            "backend": job.request.backend,
            "base_model": asdict(job.request.base_model),
            "dataset": {
                **asdict(job.request.dataset),
                "identity_hash": job.request.dataset.identity_hash,
                "verified_train_records": dataset.train_records,
                "verified_validation_records": dataset.validation_records,
            },
            "configuration": {
                **asdict(job.request.configuration),
                "identity_hash": job.request.configuration.identity_hash,
            },
            "metrics": dict(outcome.metrics),
            "artifacts": artifacts,
            "software": {"python": platform.python_version(), "packages": package_versions},
            "hardware": {
                "resource_profile": self._config.resource_profile,
                "machine": platform.machine(),
                "processor": platform.processor(),
                "cpu_count": os.cpu_count(),
                "cuda_visible_devices": str(os.getenv("CUDA_VISIBLE_DEVICES", "")),
            },
            "completed_at": time.time(),
        }
        manifest.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        return manifest

    def _write_evaluation_manifest(
        self,
        job: _Job,
        dataset: Any,
        outcome: AdapterEvaluationOutcome,
        artifacts: list[dict[str, Any]],
    ) -> Path:
        request = job.request
        assert isinstance(request, AdapterEvaluationJobRequest)
        manifest = self._artifact_root(job) / "evaluation_manifest.json"
        payload = {
            "schema_version": "ananta.adapter-evaluation-manifest.v1",
            "contract_version": CONTRACT_VERSION,
            "job_id": request.job_id,
            "attempt_id": request.attempt_id,
            "fencing_token": request.fencing_token,
            "correlation_id": request.correlation_id,
            "backend": request.backend,
            "base_model": asdict(request.base_model),
            "adapter": asdict(request.adapter),
            "validation_dataset": {
                **asdict(request.validation_dataset),
                "identity_hash": request.validation_dataset.identity_hash,
                "verified_validation_records": dataset.validation_records,
            },
            "configuration": {
                **asdict(request.configuration),
                "identity_hash": request.configuration.identity_hash,
            },
            "metrics": dict(outcome.metrics),
            "artifacts": artifacts,
            "software": {"python": platform.python_version(), "packages": self._package_versions()},
            "hardware": self._hardware_manifest(),
            "completed_at": time.time(),
        }
        manifest.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        return manifest

    def _checkpoint_metadata(self, job: _Job, checkpoint: Path) -> dict[str, Any]:
        assert isinstance(job.request, TrainingJobRequest)
        root = self._checkpoint_root(job).resolve()
        resolved = checkpoint.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise TrainingBackendError("checkpoint_boundary_violation", "best checkpoint escaped its job root") from exc
        digest = _path_sha256(resolved)
        relative = resolved.relative_to(self._config.state_root.resolve()).as_posix()
        return {
            "relative_path": relative,
            "binding": {
                "job_id": job.request.job_id,
                "source_attempt_id": job.request.attempt_id,
                "base_model_hash": job.request.base_model.snapshot_hash,
                "dataset_hash": job.request.dataset.identity_hash,
                "configuration_hash": job.request.configuration.identity_hash,
                "checkpoint_sha256": digest,
            },
        }

    def _resume_path(self, job: _Job) -> Path | None:
        assert isinstance(job.request, TrainingJobRequest)
        resume = job.request.resume_checkpoint
        if resume is None:
            return None
        path_parts = Path(resume.relative_path).parts
        legacy_prefix = (
            "jobs",
            resume.binding.job_id,
            "attempts",
            resume.binding.source_attempt_id,
        )
        scoped_prefix = (
            "tenants",
            job.request.tenant_scope_digest,
            "jobs",
            resume.binding.job_id,
            "attempts",
            resume.binding.source_attempt_id,
        )
        if path_parts[: len(scoped_prefix)] == scoped_prefix:
            source_prefix = scoped_prefix
        elif path_parts[: len(legacy_prefix)] == legacy_prefix:
            source_prefix = legacy_prefix
        else:
            raise TrainingContractError(
                "checkpoint_binding_mismatch",
                "resume checkpoint path is not bound to its source job",
            )
        source_status_path = self._config.state_root.joinpath(
            *source_prefix,
            "status.json",
        )
        try:
            source_status = json.loads(source_status_path.read_text(encoding="utf-8"))
            source_request = TrainingJobRequest.from_mapping(source_status["request"])
        except (FileNotFoundError, KeyError, json.JSONDecodeError, TrainingContractError) as exc:
            raise TrainingContractError(
                "checkpoint_binding_mismatch",
                "resume checkpoint source job cannot be verified",
            ) from exc
        if (
            source_request.job_id != resume.binding.job_id
            or source_request.attempt_id != resume.binding.source_attempt_id
            or source_request.tenant_scope_digest
            != job.request.tenant_scope_digest
            or source_request.backend != job.request.backend
            or source_request.base_model.snapshot_hash != resume.binding.base_model_hash
            or source_request.dataset.identity_hash != resume.binding.dataset_hash
            or source_request.configuration.identity_hash != resume.binding.configuration_hash
        ):
            raise TrainingContractError(
                "checkpoint_binding_mismatch",
                "resume checkpoint source provenance does not match its binding",
            )
        path = self._resolve_within(self._config.state_root, resume.relative_path, "checkpoint_missing")
        if not path.exists():
            raise TrainingContractError("checkpoint_missing", "resume checkpoint does not exist")
        if _path_sha256(path) != resume.binding.checkpoint_sha256:
            raise TrainingContractError(
                "checkpoint_hash_mismatch", "resume checkpoint SHA-256 does not match its binding"
            )
        return path

    @staticmethod
    def _resolve_within(root: Path, relative: str, missing_code: str) -> Path:
        base = root.resolve()
        unresolved = base / relative
        current = base
        for part in Path(relative).parts:
            current = current / part
            if current.is_symlink():
                raise TrainingContractError(missing_code, "symbolic-link resources are not admitted")
        candidate = unresolved.resolve()
        try:
            candidate.relative_to(base)
        except ValueError as exc:
            raise TrainingContractError("invalid_path", "resource path escapes its configured root") from exc
        return candidate

    def _job_root(self, job: _Job) -> Path:
        return (
            self._config.state_root
            / "tenants"
            / job.request.tenant_scope_digest
            / "jobs"
            / job.request.job_id
            / "attempts"
            / job.request.attempt_id
        )

    def _artifact_root(self, job: _Job) -> Path:
        return self._job_root(job) / "artifacts"

    def _checkpoint_root(self, job: _Job) -> Path:
        return self._job_root(job) / "checkpoints"

    def _event_path(self, job: _Job) -> Path:
        return self._job_root(job) / "events.jsonl"

    def _status_path(self, job: _Job) -> Path:
        return self._job_root(job) / "status.json"

    def _workspace_relative(self, job: _Job) -> str:
        return (
            f"tenants/{job.request.tenant_scope_digest}/jobs/"
            f"{job.request.job_id}/attempts/{job.request.attempt_id}/workspace"
        )

    def _write_model_binding(self, job: _Job) -> None:
        binding = self._job_root(job) / "model-binding.json"
        binding.write_text(
            json.dumps(
                {
                    "schema": "ananta.lora-model-binding.v1",
                    "tenant_scope_digest": job.request.tenant_scope_digest,
                    "job_id": job.request.job_id,
                    "attempt_id": job.request.attempt_id,
                    "model_id": job.request.base_model.model_id,
                    "snapshot_hash": job.request.base_model.snapshot_hash,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

    def _enforce_dataset_scope_and_quota(self, job: _Job) -> None:
        request = job.request
        if isinstance(request, TrainingJobRequest):
            manifests = (request.dataset.train, request.dataset.validation)
        else:
            manifests = (request.validation_dataset.validation,)
        total = 0
        for manifest in manifests:
            relative = str(manifest.relative_path)
            parts = Path(relative).parts
            if parts[:1] == ("tenants",) and (
                len(parts) < 3
                or parts[1] != str(request.tenant_storage_key)
                or parts[2] != "datasets"
            ):
                raise TrainingContractError(
                    "dataset_tenant_binding_mismatch",
                    "dataset path belongs to another tenant",
                )
            path = self._resolve_within(
                self._config.dataset_root,
                relative,
                "dataset_missing",
            )
            if path.is_symlink() or not path.is_file():
                raise TrainingContractError(
                    "dataset_missing",
                    "dataset split is not a regular file",
                )
            total += path.stat().st_size
        if total > self._config.max_dataset_bytes:
            raise TrainingContractError(
                "dataset_quota_exceeded",
                "combined dataset splits exceed the configured quota",
            )

    def _enforce_storage_quotas(self, job: _Job) -> None:
        self._enforce_path_quota(
            self._checkpoint_root(job),
            self._config.max_checkpoint_bytes,
            "checkpoint_quota_exceeded",
        )
        self._enforce_path_quota(
            self._artifact_root(job),
            self._config.max_export_bytes,
            "export_quota_exceeded",
        )
        tenant_state = (
            self._config.state_root
            / "tenants"
            / job.request.tenant_scope_digest
        )
        tenant_workspace = (
            self._config.workspace_root
            / "tenants"
            / job.request.tenant_scope_digest
        )
        if (
            _path_size(tenant_state)
            + _path_size(tenant_workspace)
            > self._config.max_tenant_bytes
        ):
            raise TrainingContractError(
                "tenant_storage_quota_exceeded",
                "worker tenant storage exceeds the configured quota",
            )

    @staticmethod
    def _enforce_path_quota(
        path: Path,
        maximum: int,
        reason_code: str,
    ) -> None:
        if _path_size(path) > maximum:
            raise TrainingContractError(
                reason_code,
                "worker storage exceeds the configured quota",
            )

    def _storage_usage(self, job: _Job) -> dict[str, Any]:
        checkpoint_bytes = _path_size(self._checkpoint_root(job))
        export_bytes = _path_size(self._artifact_root(job))
        workspace = self._resolve_within(
            self._config.workspace_root,
            job.request.workspace_ref,
            "workspace_missing",
        )
        return {
            "workspace_bytes": _path_size(workspace),
            "checkpoint_bytes": checkpoint_bytes,
            "export_bytes": export_bytes,
            "attempt_bytes": checkpoint_bytes + export_bytes,
            "quotas": {
                "dataset_bytes": self._config.max_dataset_bytes,
                "model_bytes": self._config.max_model_bytes,
                "checkpoint_bytes": self._config.max_checkpoint_bytes,
                "export_bytes": self._config.max_export_bytes,
                "tenant_total_bytes": self._config.max_tenant_bytes,
            },
        }

    @staticmethod
    def _package_versions() -> dict[str, str]:
        versions: dict[str, str] = {}
        for package in (
            "torch",
            "transformers",
            "datasets",
            "peft",
            "trl",
            "safetensors",
            "unsloth",
            "unsloth_zoo",
        ):
            try:
                versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                continue
        return versions

    @staticmethod
    def _bounded_torch_hardware_probe() -> dict[str, Any]:
        script = (
            "import json, torch\n"
            "available=bool(torch.cuda.is_available())\n"
            "count=int(torch.cuda.device_count()) if available else 0\n"
            "props=torch.cuda.get_device_properties(0) if count else None\n"
            "print(json.dumps({'cuda_available':available,'torch_version':str(torch.__version__),"
            "'cuda_version':str(torch.version.cuda) if torch.version.cuda else None,"
            "'device_count':count,'device_name':str(props.name) if props else None,"
            "'total_vram_bytes':int(props.total_memory) if props else None}))\n"
        )
        try:
            completed = subprocess.run(
                [sys.executable, "-I", "-c", script],
                check=False,
                capture_output=True,
                text=True,
                timeout=3.0,
            )
            if completed.returncode != 0 or len(completed.stdout) > 16_384:
                return {}
            payload = json.loads(completed.stdout)
            return dict(payload) if isinstance(payload, Mapping) else {}
        except (OSError, subprocess.SubprocessError, ValueError):
            return {}

    def _hardware_manifest(self) -> dict[str, Any]:
        return {
            "resource_profile": self._config.resource_profile,
            "machine": platform.machine(),
            "processor": platform.processor(),
            "cpu_count": os.cpu_count(),
            "cuda_visible_devices": str(os.getenv("CUDA_VISIBLE_DEVICES", "")),
        }


def _safe_resource_admission_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if set(payload) != _RESOURCE_ADMISSION_PAYLOAD_KEYS:
        raise TrainingBackendError(
            "invalid_backend_event",
            "resource admission event must contain exactly the declared fields",
        )
    profile = payload.get("profile")
    if not isinstance(profile, str) or not profile or len(profile) > 64:
        raise TrainingBackendError("invalid_backend_event", "resource admission profile is invalid")
    admitted = payload.get("admitted")
    estimate_only = payload.get("estimate_only")
    if not isinstance(admitted, bool) or not isinstance(estimate_only, bool):
        raise TrainingBackendError("invalid_backend_event", "resource admission flags are invalid")
    if payload.get("reason_code") != "vram_admission_admitted":
        raise TrainingBackendError("invalid_backend_event", "resource admission reason is invalid")
    clean: dict[str, Any] = {
        "profile": profile,
        "admitted": admitted,
        "estimate_only": estimate_only,
        "reason_code": "vram_admission_admitted",
    }
    for field_name in ("estimated_peak_bytes", "reserve_bytes"):
        value = payload.get(field_name)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**63 - 1:
            raise TrainingBackendError(
                "invalid_backend_event",
                f"resource admission {field_name} is invalid",
            )
        clean[field_name] = value
    usable_bytes = payload.get("usable_bytes")
    if usable_bytes is not None and (
        isinstance(usable_bytes, bool)
        or not isinstance(usable_bytes, int)
        or not 0 <= usable_bytes <= 2**63 - 1
    ):
        raise TrainingBackendError("invalid_backend_event", "resource admission usable_bytes is invalid")
    clean["usable_bytes"] = usable_bytes
    assumptions = payload.get("assumptions")
    if (
        not isinstance(assumptions, (list, tuple))
        or not 1 <= len(assumptions) <= 16
        or any(not isinstance(item, str) or not item or len(item) > 256 for item in assumptions)
    ):
        raise TrainingBackendError("invalid_backend_event", "resource admission assumptions are invalid")
    clean["assumptions"] = list(assumptions)
    return clean


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise TrainingBackendError("invalid_backend_event", "backend event contains a non-finite number")
        return value
    raise TrainingBackendError("invalid_backend_event", "backend event values must be scalar")


def _path_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(
        item.stat().st_size
        for item in path.rglob("*")
        if item.is_file() and not item.is_symlink()
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_sha256(path: Path) -> str:
    if path.is_symlink():
        raise TrainingContractError("invalid_path", "hashed resource must not be a symbolic link")
    if path.is_file():
        return _file_sha256(path)
    if not path.is_dir():
        raise TrainingContractError("invalid_path", "hashed resource is not a regular file or directory")
    entries = list(path.rglob("*"))
    if any(item.is_symlink() for item in entries):
        raise TrainingContractError("invalid_path", "hashed resource tree contains a symbolic link")
    if any(not item.is_file() and not item.is_dir() for item in entries):
        raise TrainingContractError("invalid_path", "hashed resource tree contains an unsupported entry")
    children = sorted(item for item in entries if item.is_file())
    if not children:
        raise TrainingContractError("invalid_path", "hashed resource tree is empty")
    digest = hashlib.sha256()
    for child in children:
        digest.update(child.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\x00")
        digest.update(_file_sha256(child).encode("ascii"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _media_type(path: Path) -> str:
    return {
        ".json": "application/json",
        ".safetensors": "application/octet-stream",
        ".txt": "text/plain",
    }.get(path.suffix.lower(), "application/octet-stream")
