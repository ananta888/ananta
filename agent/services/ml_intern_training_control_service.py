from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from ananta_contracts.unsloth_capability import (
    UnslothWorkerCapabilityContractError,
    validate_worker_capability_probe,
)
from agent.db_models import MlInternTrainingAttemptDB, MlInternTrainingJobDB
from agent.repositories.ml_intern_training import (
    MlInternTrainingRepository,
    MlInternTrainingRepositoryConflict,
    get_ml_intern_training_repository,
)
from agent.services.ml_intern_training_config_service import get_gpu_profile_defaults
from agent.services.ml_intern_training_contract import (
    UNSLOTH_BACKENDS,
    CreateTrainingJobCommand,
    MlInternTrainingContractError,
    UnslothCapabilityFacet,
    UnslothCapabilitySnapshot,
    assert_job_transition,
    idempotency_digest,
    normalize_run_ids,
    normalize_source_ids,
    request_digest,
    sanitize_event_payload,
)
from agent.services.ml_intern_training_read_model_service import MlInternTrainingReadModelService
from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal
from agent.services.ml_intern_training_result_publisher import MlInternTrainingResultPublisher
from agent.services.ml_intern_training_worker_job_projection import (
    MlInternTrainingWorkerJobProjectionPort,
    get_ml_intern_training_worker_job_projection,
)

_CHECKPOINT_REF_PREFIX = "lora-checkpoint-v1:"


def _reject_non_finite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _tenant_scope_digest(principal: MlInternTrainingPrincipal) -> str:
    """Return an opaque, versioned worker-bound scope without leaking identity."""

    material = (
        "ananta.ml-intern-training.scope.v1\x00"
        f"{principal.tenant_id}\x00{principal.subject}"
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _tenant_storage_key(principal: MlInternTrainingPrincipal) -> str:
    return hashlib.sha256(principal.tenant_id.encode("utf-8")).hexdigest()


def get_task_queue_service() -> Any:
    """Resolve the queue lazily to keep service imports cycle-free.

    ``task_queue_service`` imports route-owned routing policies which in turn
    import several domain services.  Importing it while this domain service is
    collected creates a cycle; runtime composition happens only when a job is
    admitted, after module initialization has completed.
    """

    from agent.services.task_queue_service import get_task_queue_service as factory

    return factory()


class MlInternTrainingExecutionPort(Protocol):
    """Worker execution seam. Implementations may use internal HTTP or a test fake."""

    def capability_probe(self) -> Mapping[str, Any]: ...

    def execute(
        self,
        *,
        job_id: str,
        spec: Mapping[str, Any],
        dataset_path: Path,
        validation_path: Path | None,
        attempt_id: str,
        fencing_token: int,
        on_event: Callable[[Mapping[str, Any]], None],
        cancel_check: Callable[[], bool],
    ) -> Mapping[str, Any]: ...


class MlInternTrainingControlService:
    """Hub-owned admission, persistence and delegation for LoRA jobs.

    The service never imports an ML package.  Dry-run/mock may use the legacy
    bounded local adapter when explicitly allowed; every live backend requires
    an injected worker port.
    """

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        repository: MlInternTrainingRepository | None = None,
        execution_port: MlInternTrainingExecutionPort | None = None,
        result_publisher: MlInternTrainingResultPublisher | None = None,
        worker_job_projection: MlInternTrainingWorkerJobProjectionPort | None = None,
        executor: ThreadPoolExecutor | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        from agent.services.ml_intern_training_config_service import normalize_ml_intern_training_config

        raw_config = dict(config or {})
        self._config = {
            **normalize_ml_intern_training_config(raw_config),
            **({"base_model_catalog": raw_config["base_model_catalog"]} if "base_model_catalog" in raw_config else {}),
        }
        self._repository = repository or get_ml_intern_training_repository()
        self._execution_port = execution_port
        self._result_publisher = result_publisher
        self._worker_job_projection = worker_job_projection or get_ml_intern_training_worker_job_projection()
        self._executor = executor or ThreadPoolExecutor(max_workers=2, thread_name_prefix="lora-control")
        self._clock = clock
        self._lock = threading.RLock()
        self._accepting_claims = True
        self._scheduled_job_ids: set[str] = set()
        self._last_dispatched_tenant: str | None = None
        self._owns_executor = executor is None
        self._read_models = MlInternTrainingReadModelService()

    def capabilities(self) -> dict[str, Any]:
        enabled = bool(self._config.get("enabled", False))
        runtime_available = self._execution_port is not None
        worker_probe = self._worker_capability_probe()
        mode = str(self._config.get("mode") or "dry_run")
        catalog = self._config.get("base_model_catalog")
        configured_models = (
            list(catalog) if isinstance(catalog, Mapping) else list(self._config.get("base_models") or [])
        )
        backend_ids = ("mock", "peft_trl", "unsloth", "unsloth_vision", "unsloth_audio", "unsloth_embedding")
        backends = []
        for backend in backend_ids:
            worker_supports = (
                worker_probe is not None
                and self._worker_supports("train_lora", backend, probe=worker_probe)
            )
            policy_allows = backend == "mock" or mode == "live"
            available = enabled and policy_allows and (
                backend == "mock" or (runtime_available and worker_supports)
            )
            backends.append(
                {
                    "id": backend,
                    "available": available,
                    "reason_code": None
                    if available
                    else (
                        "training_disabled"
                        if not enabled
                        else (
                            "live_mode_disabled"
                            if not policy_allows
                            else (
                                "worker_unavailable"
                                if not runtime_available
                                else "worker_capability_unavailable"
                            )
                        )
                    ),
                }
            )
        gpu_profiles = []
        for name in ("rtx3080-safe", "generic-safe", "none"):
            profile_available = enabled and (
                (name == "none" and mode == "dry_run")
                or (
                    runtime_available
                    and any(
                        worker_probe is not None
                        and self._worker_supports("train_lora", backend, name, probe=worker_probe)
                        for backend in backend_ids
                    )
                )
            )
            gpu_profiles.append(
                {
                    "id": name,
                    "available": profile_available,
                    "reason_code": None
                    if profile_available
                    else ("training_disabled" if not enabled else "worker_profile_unavailable"),
                    "max_batch_size": int(
                        get_gpu_profile_defaults(name).get("max_batch_size_hard_limit", 1)
                    ),
                    "max_sequence_length": int(
                        get_gpu_profile_defaults(name).get("max_seq_length_hard_limit", 512)
                    ),
                }
            )
        backend_capabilities = {item["id"]: item for item in backends}
        facet_specs = (
            ("training.text", "unsloth", "text"),
            ("training.vision", "unsloth_vision", "vision"),
            ("training.audio", "unsloth_audio", "audio"),
            ("training.embedding", "unsloth_embedding", "embedding"),
        )
        facets = [
            UnslothCapabilityFacet(
                facet_id=facet_id,
                available=bool(backend_capabilities[backend]["available"]),
                reason_code=backend_capabilities[backend]["reason_code"],
                source="worker_probe",
                operations=("train_lora",),
                model_kinds=(model_kind,),
            )
            for facet_id, backend, model_kind in facet_specs
        ]
        export_available = enabled and "export_adapter" in set(self._config.get("allowed_job_types") or [])
        facets.append(
            UnslothCapabilityFacet(
                facet_id="export.adapter",
                available=export_available,
                reason_code=None if export_available else "unsloth_export_disabled",
                source="hub_policy",
                operations=("export_adapter",),
                model_kinds=("text",),
            )
        )
        facets.append(
            UnslothCapabilityFacet(
                facet_id="inference.generation",
                available=False,
                reason_code="unsloth_inference_capability_unavailable",
                source="worker_probe",
                operations=("generate",),
                model_kinds=("text",),
            )
        )
        security = dict(self._config.get("unsloth_security") or {})
        studio_configured = security.get("operating_mode") in {"studio_managed", "external_api"}
        facets.append(
            UnslothCapabilityFacet(
                facet_id="studio.management",
                available=False,
                reason_code=(
                    "unsloth_studio_client_unavailable"
                    if studio_configured
                    else "unsloth_studio_disabled"
                ),
                source="configuration",
                operations=("health", "status"),
            )
        )
        facets.append(
            UnslothCapabilityFacet(
                facet_id="mcp.control",
                available=False,
                reason_code=(
                    "unsloth_mcp_client_unavailable"
                    if security.get("mcp_enabled") is True
                    else "unsloth_mcp_disabled"
                ),
                source="configuration",
                operations=("status",),
            )
        )
        hardware_available = any(
            item["available"] and item["id"] != "none" for item in gpu_profiles
        ) and any(backend_capabilities[name]["available"] for name in UNSLOTH_BACKENDS)
        facets.append(
            UnslothCapabilityFacet(
                facet_id="hardware.cuda",
                available=hardware_available,
                reason_code=None if hardware_available else "unsloth_cuda_capability_unavailable",
                source="worker_probe",
                operations=("train_lora",),
            )
        )
        unsloth_snapshot = UnslothCapabilitySnapshot(
            operating_mode=str(security.get("operating_mode") or "core_worker"),
            detected_variant=(
                ",".join(
                    sorted(
                        str(state["variant"])
                        for backend, state in worker_probe["backends"].items()
                        if backend in UNSLOTH_BACKENDS and state["available"]
                    )
                )
                if worker_probe
                and any(
                    state["available"]
                    for backend, state in worker_probe["backends"].items()
                    if backend in UNSLOTH_BACKENDS
                )
                else None
            ),
            detected_version=(
                worker_probe["packages"]["unsloth"]["version"]
                if worker_probe and worker_probe["packages"]["unsloth"]["available"]
                else None
            ),
            facets=tuple(facets),
        )
        return {
            "contract_version": "ananta.ml-intern-training.v2",
            "available": enabled,
            "mode": mode,
            "runtime_available": runtime_available,
            "worker_probe_available": worker_probe is not None,
            "backends": backends,
            "gpu_profiles": gpu_profiles,
            "base_models": [
                {
                    "id": str(model_id),
                    "label": str(model_id),
                    "local": True,
                    "available": enabled,
                    "compatible_backends": list(backend_ids),
                    "reason_code": None if enabled else "training_disabled",
                }
                for model_id in configured_models
            ],
            "limits": {
                "max_dataset_bytes": int(self._config.get("max_dataset_bytes") or 100 * 1024 * 1024),
                "max_adapter_bytes": int(self._config.get("max_adapter_bytes") or 2 * 1024 * 1024 * 1024),
                "max_concurrent_jobs": max(1, min(int(self._config.get("max_concurrent_jobs") or 1), 16)),
                "max_queued_jobs": max(0, min(int(self._config.get("max_queued_jobs") or 0), 10_000)),
                "min_validation_ratio": 0.05,
                "max_validation_ratio": 0.5,
                "max_lora_rank": 256,
                "max_lora_alpha": 512,
                "max_batch_size": 128,
                "max_gradient_accumulation_steps": 1024,
                "min_sequence_length": 128,
                "max_sequence_length": 32_768,
                "max_steps": 1_000_000,
                "minimum_eval_score": float(self._config.get("minimum_eval_score") or 0.0),
            },
            "unsloth_capabilities": unsloth_snapshot.to_mapping(),
            "worker_capability_probe": worker_probe,
        }

    def create_job(
        self,
        principal: MlInternTrainingPrincipal,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], bool]:
        if not bool(self._config.get("enabled", False)):
            raise MlInternTrainingContractError("training_disabled", "ml_intern_training is disabled", status_code=403)
        command = CreateTrainingJobCommand.from_mapping(payload)
        gpu_profile = self._requested_gpu_profile(command)
        command.request_spec.setdefault("gpu_profile", gpu_profile)
        configured_mode = str(self._config.get("mode") or "dry_run")
        if command.mode == "live" and configured_mode != "live":
            raise MlInternTrainingContractError(
                "live_mode_disabled",
                "live training is disabled by the Hub policy",
                status_code=403,
            )
        if command.mode == "dry_run" and command.backend != "mock":
            raise MlInternTrainingContractError(
                "dry_run_backend_invalid",
                "dry_run jobs may only use the bounded mock backend",
                status_code=422,
            )
        self._assert_request_policy(command, gpu_profile)
        if command.job_type not in set(self._config.get("allowed_job_types") or []):
            raise MlInternTrainingContractError(
                "job_type_not_admitted",
                "job_type is disabled by the Hub training policy",
                status_code=403,
            )
        dataset = self._repository.get_dataset(principal, command.dataset_id)
        if dataset is None:
            raise MlInternTrainingContractError("dataset_not_found", "dataset was not found", status_code=404)
        self._bind_dataset_provenance(command, dataset)
        require_validation = bool(self._config.get("require_dataset_validation", True)) or bool(
            command.request_spec.get("require_dataset_validation", False)
        )
        require_secret_scan = bool(self._config.get("require_secret_scan", True)) or bool(
            command.request_spec.get("require_secret_scan", False)
        )
        validation = dict(dataset.validation_report or {})
        validation_ok = bool(validation.get("ok", validation.get("valid", False)))
        if command.mode == "live" and require_validation and not validation_ok:
            raise MlInternTrainingContractError(
                "dataset_validation_required", "live training requires a successful dataset validation", status_code=409
            )
        if command.mode == "live" and require_secret_scan and not self._secret_scan_passed(validation):
            raise MlInternTrainingContractError(
                "dataset_secret_scan_required",
                "live training requires successful secret scans for train and validation data",
                status_code=409,
            )
        configured_models = self._configured_model_ids()
        if command.base_model and configured_models and command.base_model not in configured_models:
            raise MlInternTrainingContractError(
                "base_model_not_admitted",
                "base model is not in the Hub training allowlist",
                status_code=403,
            )
        if command.job_type in {"train_lora", "evaluate_lora"} and command.mode == "live":
            if not dataset.validation_storage_ref:
                raise MlInternTrainingContractError(
                    "validation_split_required",
                    "live LoRA execution requires a validation split",
                    status_code=409,
                )
        worker_required = command.job_type == "evaluate_lora" or command.mode == "live" or command.backend != "mock"
        if worker_required and self._execution_port is None:
            raise MlInternTrainingContractError(
                "training_worker_unavailable", "a live training worker is not available", status_code=503
            )
        if worker_required and not self._worker_supports(command.job_type, command.backend, gpu_profile):
            raise MlInternTrainingContractError(
                "worker_capability_unavailable",
                "no configured LoRA worker advertises the requested backend capability",
                status_code=503,
            )

        digest = request_digest(command.request_spec)
        idem_digest = idempotency_digest(tenant_id=principal.tenant_id, subject=principal.subject, key=idempotency_key)
        job = MlInternTrainingJobDB(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            task_id="pending",
            dataset_id=dataset.id,
            job_type=command.job_type,
            mode=command.mode,
            backend=command.backend,
            base_model=command.base_model,
            idempotency_key_digest=idem_digest,
            request_digest=digest,
            request_spec=copy.deepcopy(command.request_spec),
            max_steps=self._max_steps(command.request_spec),
        )
        job.task_id = f"ml-intern-training-{job.id}"
        with self._lock:
            existing = self._repository.get_job_by_idempotency(principal, idem_digest)
            if existing is not None:
                if existing.request_digest != digest:
                    raise MlInternTrainingContractError(
                        "idempotency_payload_conflict",
                        "idempotency key conflicts with another request",
                        status_code=409,
                    )
                return self._accepted_read_model(existing, replayed=True), True
            try:
                saved, replayed = self._repository.create_job_with_capacity(
                    job,
                    outstanding_limit=self._max_outstanding_jobs(),
                )
            except MlInternTrainingRepositoryConflict as exc:
                if str(exc) == "training_capacity_exhausted":
                    raise MlInternTrainingContractError(
                        "training_capacity_exhausted",
                        "the configured LoRA training running and queue capacity is currently exhausted",
                        status_code=429,
                    ) from exc
                raise MlInternTrainingContractError(
                    str(exc), "idempotency key conflicts with another request", status_code=409
                ) from exc
        if replayed:
            return self._accepted_read_model(saved, replayed=True), True

        task_kind = "ml_intern_evaluate_lora" if saved.job_type == "evaluate_lora" else "ml_intern_train_lora"
        capability = "lora_evaluation" if saved.job_type == "evaluate_lora" else "lora_training"
        try:
            get_task_queue_service().ingest_task(
                task_id=saved.task_id,
                status="todo",
                title=f"Local LoRA job: {saved.job_type}",
                description="Hub-governed local LoRA/QLoRA worker delegation.",
                priority="high" if saved.mode == "live" else "medium",
                created_by=principal.subject,
                source="ml_intern_training",
                tags=["ml_intern_training", saved.job_type, saved.mode],
                event_type="ml_intern_training_queued",
                event_channel="hub_task_queue",
                event_details={"job_id": saved.id, "dataset_id": saved.dataset_id, "backend": saved.backend},
                extra_fields={
                    "task_kind": task_kind,
                    "required_capabilities": [
                        capability,
                        saved.backend,
                        f"device:{self._device_for_gpu_profile(gpu_profile)}",
                        f"gpu_profile:{gpu_profile}",
                    ],
                    "worker_execution_context": {
                        "ml_intern_training": {
                            "job_id": saved.id,
                            "dataset_id": saved.dataset_id,
                            "request_digest": saved.request_digest,
                            "dataset_hash": saved.request_spec.get("dataset_hash"),
                            "source_ids": list(saved.request_spec.get("source_ids") or []),
                            "run_ids": list(saved.request_spec.get("run_ids") or []),
                            "provenance_status": saved.request_spec.get("provenance_status"),
                            "mode": saved.mode,
                            "gpu_profile": gpu_profile,
                        }
                    },
                },
            )
        except Exception as exc:
            # The domain record remains an auditable terminal tombstone, not
            # an executable orphan outside the Hub task system.
            current = self._repository.get_job(principal, saved.id)
            if current is not None and current.status == "queued":
                current.error_code = "task_materialization_failed"
                current.error_message = "Hub task materialization failed"
                current.retryable = True
                try:
                    self._transition(principal, current, "failed", phase="admission_failed", progress_percent=100.0)
                except Exception:
                    pass
            raise MlInternTrainingContractError(
                "task_materialization_failed",
                "Hub task materialization failed; the training job was not scheduled",
                status_code=503,
            ) from exc
        self._repository.append_event(
            principal,
            saved.id,
            event_type="job_queued",
            dedupe_key="job-created",
            payload={"status": "queued", "phase": "queued", "progress_percent": 0},
        )
        self.schedule_reconciled_job(principal, saved.id)
        current = self._repository.get_job(principal, saved.id) or saved
        return self._accepted_read_model(current, replayed=False), False

    def list_jobs(
        self,
        principal: MlInternTrainingPrincipal,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        backend: str | None = None,
        dataset_id: str | None = None,
    ) -> dict[str, Any]:
        bounded_limit = max(1, min(limit, 200))
        bounded_offset = max(0, offset)
        jobs = self._repository.list_jobs(
            principal,
            limit=bounded_limit,
            offset=bounded_offset,
            status=status,
            backend=backend,
            dataset_id=dataset_id,
        )
        total = self._repository.count_jobs(
            principal,
            status=status,
            backend=backend,
            dataset_id=dataset_id,
        )
        return {
            "items": [self._read_models.job(job) for job in jobs],
            "count": len(jobs),
            "total": total,
            "offset": bounded_offset,
            "next_offset": bounded_offset + len(jobs) if bounded_offset + len(jobs) < total else None,
        }

    def get_job(self, principal: MlInternTrainingPrincipal, job_id: str) -> dict[str, Any]:
        job = self._repository.get_job(principal, job_id)
        if job is None:
            raise MlInternTrainingContractError("job_not_found", "training job was not found", status_code=404)
        return self._read_models.job(job, detail=True)

    def list_events(
        self,
        principal: MlInternTrainingPrincipal,
        job_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        try:
            events = self._repository.list_events(
                principal, job_id, after_sequence=max(0, after_sequence), limit=max(1, min(limit, 500))
            )
        except KeyError as exc:
            raise MlInternTrainingContractError("job_not_found", "training job was not found", status_code=404) from exc
        items = [self._read_models.event(event) for event in events]
        return {
            "items": items,
            "count": len(items),
            "next_sequence": items[-1]["sequence"] if items else max(0, after_sequence),
        }

    def cancel_job(
        self,
        principal: MlInternTrainingPrincipal,
        job_id: str,
        *,
        idempotency_key: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        cancel_digest = hashlib.sha256(
            f"lora-cancel-v1\0{principal.tenant_id}\0{principal.subject}\0{idempotency_key}".encode()
        ).hexdigest()
        with self._lock:
            job = self._repository.get_job(principal, job_id)
            if job is None:
                raise MlInternTrainingContractError("job_not_found", "training job was not found", status_code=404)
            previous_cancel_digest = str((job.result_summary or {}).get("cancel_request_digest") or "")
            if previous_cancel_digest == cancel_digest:
                return self._read_models.job(job, detail=True)
            if job.status in {"cancelled", "completed", "failed"}:
                raise MlInternTrainingContractError(
                    "job_not_cancellable", "terminal job cannot be cancelled", status_code=409
                )
            expected = job.version
            assert_job_transition(job.status, "cancel_requested")
            job.status = "cancel_requested"
            job.phase = "cancel_requested"
            job.cancel_requested = True
            job.result_summary = {
                **dict(job.result_summary or {}),
                "cancel_request_digest": cancel_digest,
                **(
                    {"cancel_reason_digest": hashlib.sha256(reason.encode()).hexdigest()}
                    if reason
                    else {}
                ),
            }
            saved = self._repository.save_job(job, expected_version=expected)
            self._repository.append_event(
                principal,
                job_id,
                event_type="cancel_requested",
                dedupe_key=f"cancel-request-{cancel_digest}",
                payload={
                    "status": "cancel_requested",
                    "phase": "cancel_requested",
                    "progress_percent": saved.progress_percent,
                    "reason_code": "operator_cancel_requested",
                },
            )
            return self._read_models.job(saved, detail=True)

    def schedule_reconciled_job(self, principal: MlInternTrainingPrincipal, job_id: str) -> bool:
        """Offer a persisted job to the capacity-bounded Hub scheduler."""

        with self._lock:
            if not self._accepting_claims:
                return False
            current = self._repository.get_job(principal, job_id)
            if current is None or current.status != "queued":
                return False
            self._dispatch_queued_jobs_locked()
            return current.id in self._scheduled_job_ids or (
                (refreshed := self._repository.get_job(principal, job_id)) is not None
                and refreshed.status in {"claimed", "running", "completed", "failed", "cancelled"}
            )

    def begin_shutdown(self) -> None:
        """Stop new claims while allowing already-running worker leases to finish."""

        with self._lock:
            if not self._accepting_claims:
                return
            self._accepting_claims = False
        if not self._owns_executor:
            return
        shutdown = getattr(self._executor, "shutdown", None)
        if not callable(shutdown):
            return
        try:
            shutdown(wait=False, cancel_futures=True)
        except TypeError:  # compatibility with executor implementations without cancel_futures
            shutdown(wait=False)

    def _execute_job(self, principal: MlInternTrainingPrincipal, job_id: str) -> None:
        attempt: MlInternTrainingAttemptDB | None = None
        execution_slot_acquired = False
        execution_slot_deferred = False
        fencing_token = 0
        worker_job_id: str | None = None
        worker_ref = "local:hub-dry-run"
        task_id = job_id
        try:
            now = self._clock()
            execution_slot = self._repository.try_acquire_execution_slot(
                job_id,
                limit=self._max_concurrent_jobs(),
                now=now,
                lease_expires_at=now + int(self._config.get("timeout_seconds") or 3600),
            )
            if execution_slot is None:
                execution_slot_deferred = True
                return
            execution_slot_acquired = True
            with self._lock:
                if not self._accepting_claims:
                    return
                job = self._repository.get_job(principal, job_id)
                if job is None or job.status != "queued":
                    return
                worker_id = str(getattr(self._execution_port, "worker_id", "hub-local-dry-run"))[:191]
                worker_ref = str(getattr(self._execution_port, "worker_ref", "local:hub-dry-run"))[:512]
                task_id = job.task_id
                tenant_scope_digest = _tenant_scope_digest(principal)
                attempt_number = self._repository.next_attempt_number(job.id)
                # The high bits are the durable, strictly monotone attempt
                # number; 128 cryptographic random low bits make the bearer
                # fence non-guessable without sacrificing restart ordering.
                fencing_token = (attempt_number << 128) | secrets.randbits(128)
                attempt = self._repository.create_attempt(
                    MlInternTrainingAttemptDB(
                        job_id=job.id,
                        tenant_id=principal.tenant_id,
                        owner_subject=principal.subject,
                        attempt_number=attempt_number,
                        status="claimed",
                        worker_id=worker_id,
                        worker_url=worker_ref,
                        fencing_token_digest=hashlib.sha256(str(fencing_token).encode()).hexdigest(),
                        lease_expires_at=self._clock() + int(self._config.get("timeout_seconds") or 3600),
                        deadline_at=self._clock() + int(self._config.get("timeout_seconds") or 3600),
                    )
                )
                job.active_attempt_id = attempt.id
                job = self._transition(principal, job, "claimed", phase="claimed", progress_percent=0.5)
                worker_job_id = self._worker_job_projection.claim(
                    task_id=job.task_id,
                    job_id=job.id,
                    attempt_id=attempt.id,
                    worker_id=worker_id,
                    worker_ref=worker_ref,
                    backend=job.backend,
                    gpu_profile=str(job.request_spec.get("gpu_profile") or "none"),
                    tenant_scope_digest=tenant_scope_digest,
                )
                job.worker_job_id = worker_job_id
                job = self._repository.save_job(job, expected_version=job.version)
                job = self._transition(principal, job, "running", phase="preparing", progress_percent=1.0)
                attempt.status = "running"
                attempt.last_heartbeat_at = self._clock()
                attempt = self._repository.save_attempt(attempt, expected_version=attempt.version)
                # The durable running state now owns the slot; the transient
                # reservation only protects the submit-to-claim interval.
                self._scheduled_job_ids.discard(job_id)
                self._dispatch_queued_jobs_locked()
            dataset = self._repository.get_dataset(principal, str(job.dataset_id or ""))
            if dataset is None:
                raise MlInternTrainingContractError("dataset_not_found", "dataset disappeared", status_code=404)
            dataset_path = Path(dataset.train_storage_ref or dataset.storage_ref)
            validation_path = Path(dataset.validation_storage_ref) if dataset.validation_storage_ref else None

            def on_event(event: Mapping[str, Any]) -> None:
                self._apply_execution_event(principal, job_id, attempt.id, event)

            def cancelled() -> bool:
                current = self._repository.get_job(principal, job_id)
                return bool(current and current.cancel_requested)

            execution_spec = copy.deepcopy(job.request_spec)
            execution_spec["_tenant_scope_digest"] = _tenant_scope_digest(principal)
            execution_spec["_tenant_storage_key"] = _tenant_storage_key(principal)
            resume_checkpoint = self._decode_checkpoint_ref(job.checkpoint_ref)
            if resume_checkpoint is not None and job.job_type == "train_lora":
                execution_spec["resume_checkpoint"] = resume_checkpoint
            if self._execution_port is not None and (
                job.job_type == "evaluate_lora" or job.mode == "live" or job.backend != "mock"
            ):
                result = self._execution_port.execute(
                    job_id=job.id,
                    spec=execution_spec,
                    dataset_path=dataset_path,
                    validation_path=validation_path,
                    attempt_id=attempt.id,
                    fencing_token=fencing_token,
                    on_event=on_event,
                    cancel_check=cancelled,
                )
            else:
                result = self._execute_local_bounded(job, dataset_path)
            current = self._repository.get_job(principal, job_id)
            if current is None:
                return
            if not self._attempt_owns_job(current, attempt.id) or not self._attempt_token_is_live(
                attempt.id, fencing_token
            ):
                self._record_stale_attempt_signal(principal, current, attempt.id, signal_type="result")
                return
            if current.status == "cancel_requested" or bool(result.get("cancelled")):
                raw_cancel_mode = str(result.get("cancel_mode") or "").strip().lower()
                cancel_mode = "cooperative" if raw_cancel_mode == "graceful" else raw_cancel_mode
                if cancel_mode in {"cooperative", "forced"}:
                    current.result_summary = {**dict(current.result_summary or {}), "cancel_mode": cancel_mode}
                self._transition(principal, current, "cancelled", phase="cancelled", progress_percent=100.0)
                self._finish_attempt(attempt, "cancelled")
                return
            status = str(result.get("status") or "failed")
            if status in {"completed", "trained", "dry_run_completed", "succeeded"}:
                current.result_ref = str(result.get("result_ref") or f"training-result:{job_id}")
                adapter_id = str(result.get("adapter_id") or current.request_spec.get("adapter_id") or "") or None
                if self._result_publisher is not None and result.get("artifacts"):
                    if current.job_type == "evaluate_lora":
                        adapter_id = self._result_publisher.publish_evaluation(
                            current,
                            {
                                **dict(result),
                                "_hub_execution_evidence": {
                                    "attempt_id": attempt.id,
                                    "fencing_token": fencing_token,
                                    "tenant_scope_digest": _tenant_scope_digest(
                                        principal
                                    ),
                                },
                            },
                        )
                    elif current.job_type == "train_lora":
                        adapter_id = self._result_publisher.publish(current, result)
                current.adapter_id = adapter_id
                terminal_checkpoint_ref = self._checkpoint_ref_from_mapping(result.get("resume_checkpoint"))
                if terminal_checkpoint_ref is not None:
                    current.checkpoint_ref = terminal_checkpoint_ref
                current.result_summary = self._safe_result_summary(result)
                self._transition(principal, current, "completed", phase="completed", progress_percent=100.0)
                self._finish_attempt(
                    attempt,
                    "completed",
                    checkpoint_ref=terminal_checkpoint_ref,
                    result_ref=current.result_ref,
                )
            else:
                current.error_code = str(result.get("error_code") or "training_failed")[:128]
                current.error_message = str(result.get("error_message") or "training worker reported failure")[:512]
                current.retryable = bool(result.get("retryable", False))
                self._transition(principal, current, "failed", phase="failed", progress_percent=100.0)
                self._finish_attempt(attempt, "failed", error_code=current.error_code)
        except Exception as exc:
            if isinstance(exc, MlInternTrainingRepositoryConflict) and str(exc) == "attempt_number_conflict":
                # Another Hub replica won the unique (job, attempt_number)
                # compare-and-set race. Its claim is authoritative.
                return
            current = self._repository.get_job(principal, job_id)
            if current is None or current.status in {"cancelled", "completed", "failed"}:
                return
            if attempt is not None and not self._attempt_owns_job(current, attempt.id):
                return
            if attempt is not None and not self._attempt_token_is_live(attempt.id, fencing_token):
                return
            if attempt is None and current.active_attempt_id is not None:
                return
            current.error_code = str(getattr(exc, "reason_code", "training_control_failed"))[:128]
            current.error_message = str(exc)[:512]
            current.retryable = bool(getattr(exc, "retryable", False))
            try:
                self._transition(principal, current, "failed", phase="failed", progress_percent=100.0)
                self._finish_attempt(attempt, "failed", error_code=current.error_code)
            except Exception:
                return
        finally:
            self._finalize_execution_dispatch(
                principal=principal,
                job_id=job_id,
                worker_job_id=worker_job_id,
                task_id=task_id,
                worker_ref=worker_ref,
                release_execution_slot=execution_slot_acquired,
                capacity_deferred=execution_slot_deferred,
            )

    def _finalize_execution_dispatch(
        self,
        *,
        principal: MlInternTrainingPrincipal,
        job_id: str,
        worker_job_id: str | None,
        task_id: str,
        worker_ref: str,
        release_execution_slot: bool,
        capacity_deferred: bool,
    ) -> None:
        """Release one execution lease and update non-authoritative projections."""

        if worker_job_id is not None:
            try:
                projected = self._repository.get_job(principal, job_id)
                projected_status = str(projected.status if projected is not None else "failed")
                if projected_status not in {"completed", "failed", "cancelled"}:
                    projected_status = "interrupted"
                reason_code = (
                    str(projected.error_code)
                    if projected is not None and projected.error_code
                    else ("attempt_interrupted" if projected_status == "interrupted" else None)
                )
                self._worker_job_projection.finish(
                    worker_job_id=worker_job_id,
                    task_id=task_id,
                    worker_ref=worker_ref,
                    status=projected_status,
                    reason_code=reason_code,
                )
            except Exception:
                # The domain job/attempt is authoritative. A projection
                # failure must not rewrite an already durable outcome.
                pass
        if release_execution_slot:
            try:
                self._repository.release_execution_slot(job_id)
            except Exception:
                pass
        with self._lock:
            self._scheduled_job_ids.discard(job_id)
            if capacity_deferred:
                return
            current = self._repository.get_job(principal, job_id)
            # A reconciler may have fenced and re-queued this exact job while
            # the superseded call was still unwinding.
            excluded = {job_id} if current is not None and current.status == "queued" else set()
            self._dispatch_queued_jobs_locked(excluded_job_ids=excluded)

    def _dispatch_queued_jobs_locked(self, *, excluded_job_ids: set[str] | None = None) -> None:
        """Reserve free slots and submit queued jobs in tenant round-robin order.

        The caller holds ``self._lock``. Reservations cover the short interval
        between executor submission and the durable ``claimed`` transition so
        that a slow executor cannot oversubscribe GPU capacity.
        """

        if not self._accepting_claims:
            return
        excluded = excluded_job_ids or set()
        queued = self._repository.list_queued_jobs(limit=self._max_outstanding_jobs())
        queued_ids = {job.id for job in queued}
        self._scheduled_job_ids.intersection_update(queued_ids)
        waiting = [
            job for job in queued if job.id not in self._scheduled_job_ids and job.id not in excluded
        ]
        available = max(
            0,
            self._max_concurrent_jobs()
            - self._repository.count_executing_jobs()
            - len(self._scheduled_job_ids),
        )
        ordered = self._fair_queue_order(waiting)
        selected = ordered[:available]
        for job in selected:
            self._set_queue_position(job, None)
            self._scheduled_job_ids.add(job.id)
            self._last_dispatched_tenant = job.tenant_id

        remaining = [job for job in waiting if job.id not in {selected_job.id for selected_job in selected}]
        for position, job in enumerate(self._fair_queue_order(remaining), start=1):
            self._set_queue_position(job, position)

        for job in selected:
            principal = MlInternTrainingPrincipal(job.tenant_id, job.owner_subject)
            try:
                self._executor.submit(self._execute_job, principal, job.id)
            except Exception:
                self._scheduled_job_ids.discard(job.id)
                current = self._repository.get_job(principal, job.id)
                if current is not None and current.status == "queued":
                    self._repository.append_event(
                        principal,
                        job.id,
                        event_type="dispatch_deferred",
                        dedupe_key=f"dispatch-deferred-{current.version}",
                        payload={
                            "status": "queued",
                            "phase": "queued",
                            "reason_code": "hub_executor_unavailable",
                            "retryable": True,
                        },
                    )
        if selected:
            queued_after_submit = self._repository.list_queued_jobs(limit=self._max_outstanding_jobs())
            waiting_after_submit = [
                job
                for job in queued_after_submit
                if job.id not in self._scheduled_job_ids and job.id not in excluded
            ]
            for position, job in enumerate(self._fair_queue_order(waiting_after_submit), start=1):
                self._set_queue_position(job, position)

    def _fair_queue_order(self, jobs: list[MlInternTrainingJobDB]) -> list[MlInternTrainingJobDB]:
        """Build a deterministic tenant round-robin projection of queued jobs."""

        by_tenant: dict[str, list[MlInternTrainingJobDB]] = {}
        tenant_order: list[str] = []
        for job in sorted(jobs, key=lambda item: (item.created_at, item.id)):
            if job.tenant_id not in by_tenant:
                by_tenant[job.tenant_id] = []
                tenant_order.append(job.tenant_id)
            by_tenant[job.tenant_id].append(job)
        if self._last_dispatched_tenant in tenant_order:
            pivot = tenant_order.index(self._last_dispatched_tenant) + 1
            tenant_order = tenant_order[pivot:] + tenant_order[:pivot]
        ordered: list[MlInternTrainingJobDB] = []
        while any(by_tenant.values()):
            for tenant_id in tenant_order:
                queue = by_tenant[tenant_id]
                if queue:
                    ordered.append(queue.pop(0))
        return ordered

    def _set_queue_position(self, job: MlInternTrainingJobDB, position: int | None) -> None:
        if job.queue_position == position:
            return
        principal = MlInternTrainingPrincipal(job.tenant_id, job.owner_subject)
        current = self._repository.get_job(principal, job.id)
        if current is None or current.status != "queued" or current.queue_position == position:
            return
        expected = current.version
        current.queue_position = position
        try:
            saved = self._repository.save_job(current, expected_version=expected)
            self._repository.append_event(
                principal,
                saved.id,
                event_type="queue_position_changed",
                dedupe_key=f"queue-position-{saved.version}",
                payload={
                    "status": "queued",
                    "phase": "queued",
                    "queue_position": position,
                    "progress_percent": saved.progress_percent,
                },
            )
        except MlInternTrainingRepositoryConflict:
            return

    def _max_concurrent_jobs(self) -> int:
        return max(1, min(int(self._config.get("max_concurrent_jobs") or 1), 16))

    def _max_queued_jobs(self) -> int:
        return max(0, min(int(self._config.get("max_queued_jobs") or 0), 10_000))

    def _max_outstanding_jobs(self) -> int:
        return max(1, self._max_concurrent_jobs() + self._max_queued_jobs())

    def _finish_attempt(
        self,
        attempt: MlInternTrainingAttemptDB | None,
        status: str,
        *,
        error_code: str | None = None,
        checkpoint_ref: str | None = None,
        result_ref: str | None = None,
    ) -> None:
        if attempt is None:
            return
        current = self._repository.get_attempt(attempt.id)
        if current is None or current.status in {"interrupted", "cancelled", "completed", "failed"}:
            return
        current.status = status
        current.error_code = error_code
        if checkpoint_ref is not None:
            current.checkpoint_ref = checkpoint_ref
        if result_ref is not None:
            current.result_ref = result_ref
        current.finished_at = self._clock()
        current.last_heartbeat_at = self._clock()
        try:
            self._repository.save_attempt(current, expected_version=current.version)
        except MlInternTrainingRepositoryConflict:
            return

    def _execute_local_bounded(self, job: MlInternTrainingJobDB, dataset_path: Path) -> Mapping[str, Any]:
        if job.mode == "live" and job.backend != "mock":
            return {"status": "failed", "error_code": "training_worker_required", "retryable": True}
        from agent.services.ml_intern_training_job_service import MlInternTrainingJobService

        artifact_root = str(self._config.get("artifact_root") or "artifacts/lora")
        cfg = {
            **self._config,
            "enabled": True,
            "mode": job.mode,
            "backend": job.backend,
            "dataset_root": str(dataset_path.parent),
            "artifact_root": artifact_root,
        }
        spec = self._legacy_spec(job.request_spec, dataset_path.name)
        result = MlInternTrainingJobService(cfg).submit_job(spec)
        return {
            "status": (
                "completed"
                if result.status == "dry_run_completed"
                else result.status
            ),
            "error_code": "legacy_job_failed" if result.errors else None,
            "error_message": "; ".join(result.errors)[:512],
            "result_ref": f"training-result:{job.id}",
        }

    def _apply_execution_event(
        self,
        principal: MlInternTrainingPrincipal,
        job_id: str,
        attempt_id: str,
        event: Mapping[str, Any],
    ) -> None:
        safe = sanitize_event_payload(event)
        current = self._repository.get_job(principal, job_id)
        if current is None:
            return
        if not self._attempt_owns_job(current, attempt_id):
            self._record_stale_attempt_signal(principal, current, attempt_id, signal_type="event")
            return
        active_attempt = self._repository.get_attempt(attempt_id)
        if active_attempt is None or active_attempt.status not in {"claimed", "running"}:
            self._record_stale_attempt_signal(principal, current, attempt_id, signal_type="event")
            return
        attempt_expected = active_attempt.version
        active_attempt.last_heartbeat_at = self._clock()
        active_attempt.lease_expires_at = self._clock() + int(self._config.get("timeout_seconds") or 3600)
        self._repository.renew_execution_slot(
            job_id,
            lease_expires_at=active_attempt.lease_expires_at,
        )
        checkpoint_ref = self._checkpoint_ref_from_mapping(event.get("resume_checkpoint"))
        if checkpoint_ref is None:
            legacy_ref = safe.get("checkpoint_ref")
            checkpoint_ref = legacy_ref[:512] if isinstance(legacy_ref, str) and legacy_ref else None
        if isinstance(checkpoint_ref, str) and checkpoint_ref:
            active_attempt.checkpoint_ref = checkpoint_ref
            current.checkpoint_ref = checkpoint_ref
            safe["checkpoint_ref"] = f"checkpoint:{hashlib.sha256(checkpoint_ref.encode()).hexdigest()[:24]}"
        try:
            self._repository.save_attempt(active_attempt, expected_version=attempt_expected)
        except MlInternTrainingRepositoryConflict:
            return
        expected = current.version
        progress = safe.get("progress_percent")
        if isinstance(progress, (int, float)):
            current.progress_percent = max(current.progress_percent, min(99.0, max(0.0, float(progress))))
        for target, source in (
            ("train_loss", "train_loss"),
            ("eval_loss", "eval_loss"),
            ("learning_rate", "learning_rate"),
        ):
            if source in safe:
                setattr(current, target, safe[source])
        if "current_step" in safe:
            current.current_step = max(int(current.current_step or 0), int(safe["current_step"]))
        if "max_steps" in safe:
            current.max_steps = max(int(current.max_steps or 0), int(safe["max_steps"]))
        if "epoch" in safe:
            current.epoch = max(float(current.epoch or 0.0), float(safe["epoch"]))
        current.phase = str(safe.get("phase") or current.phase)[:64]
        try:
            saved = self._repository.save_job(current, expected_version=expected)
        except MlInternTrainingRepositoryConflict:
            return
        sequence_hint = str(event.get("event_id") or event.get("sequence") or saved.version)
        self._repository.append_event(
            principal,
            job_id,
            event_type=str(event.get("type") or "progress")[:64],
            dedupe_key=f"worker-{attempt_id}-{sequence_hint}"[:191],
            payload=safe,
        )

    def _record_stale_attempt_signal(
        self,
        principal: MlInternTrainingPrincipal,
        job: MlInternTrainingJobDB,
        attempt_id: str,
        *,
        signal_type: str,
    ) -> None:
        """Audit one content-free marker per fenced attempt/signal class."""

        try:
            self._repository.append_event(
                principal,
                job.id,
                event_type="stale_attempt_signal_ignored",
                dedupe_key=f"stale-{attempt_id}-{signal_type}"[:191],
                payload={
                    "status": job.status,
                    "phase": job.phase,
                    "reason_code": "attempt_fenced_or_superseded",
                    "signal_type": signal_type,
                },
            )
        except (KeyError, MlInternTrainingRepositoryConflict):
            return

    @staticmethod
    def _attempt_owns_job(job: MlInternTrainingJobDB, attempt_id: str) -> bool:
        return job.active_attempt_id == attempt_id and job.status in {
            "claimed",
            "running",
            "cancel_requested",
        }

    def _attempt_token_is_live(self, attempt_id: str, fencing_token: int) -> bool:
        attempt = self._repository.get_attempt(attempt_id)
        expected_digest = hashlib.sha256(str(fencing_token).encode()).hexdigest()
        return bool(
            attempt
            and attempt.status in {"claimed", "running"}
            and secrets.compare_digest(attempt.fencing_token_digest, expected_digest)
        )

    @classmethod
    def _checkpoint_ref_from_mapping(cls, value: Any) -> str | None:
        if value is None:
            return None
        checkpoint = cls._normalize_resume_checkpoint(value)
        encoded = (
            base64.urlsafe_b64encode(
                json.dumps(
                    checkpoint,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            )
            .decode("ascii")
            .rstrip("=")
        )
        if len(encoded) > 4096:
            raise MlInternTrainingContractError(
                "resume_checkpoint_invalid",
                "resume checkpoint reference exceeds its bound",
            )
        return f"{_CHECKPOINT_REF_PREFIX}{encoded}"

    @classmethod
    def _decode_checkpoint_ref(cls, value: str | None) -> dict[str, Any] | None:
        reference = str(value or "")
        if not reference.startswith(_CHECKPOINT_REF_PREFIX):
            return None
        encoded = reference.removeprefix(_CHECKPOINT_REF_PREFIX)
        if not encoded or len(encoded) > 4096:
            raise MlInternTrainingContractError(
                "resume_checkpoint_invalid",
                "persisted resume checkpoint reference is invalid",
            )
        try:
            padding = "=" * (-len(encoded) % 4)
            decoded = base64.urlsafe_b64decode((encoded + padding).encode("ascii"))
            payload = json.loads(
                decoded,
                parse_constant=_reject_non_finite_json_constant,
            )
        except (UnicodeEncodeError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise MlInternTrainingContractError(
                "resume_checkpoint_invalid",
                "persisted resume checkpoint reference is invalid",
            ) from exc
        return cls._normalize_resume_checkpoint(payload)

    @staticmethod
    def _normalize_resume_checkpoint(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise MlInternTrainingContractError(
                "resume_checkpoint_invalid",
                "resume checkpoint must be an object",
            )
        if set(value) - {"relative_path", "binding"}:
            raise MlInternTrainingContractError(
                "resume_checkpoint_invalid",
                "resume checkpoint contains unknown fields",
            )
        supplied_path = value.get("relative_path")
        relative_path = supplied_path if isinstance(supplied_path, str) else ""
        binding = value.get("binding")
        if (
            not relative_path
            or len(relative_path) > 1024
            or relative_path.startswith(("/", "\\"))
            or ".." in Path(relative_path).parts
            or not isinstance(binding, Mapping)
        ):
            raise MlInternTrainingContractError(
                "resume_checkpoint_invalid",
                "resume checkpoint path or binding is invalid",
            )
        identifier_keys = ("job_id", "source_attempt_id")
        hash_keys = (
            "base_model_hash",
            "dataset_hash",
            "configuration_hash",
            "checkpoint_sha256",
        )
        if set(binding) != set(identifier_keys) | set(hash_keys):
            raise MlInternTrainingContractError(
                "resume_checkpoint_invalid",
                "resume checkpoint binding fields are invalid",
            )
        normalized_binding: dict[str, str] = {}
        for key in identifier_keys:
            supplied_child = binding.get(key)
            child = supplied_child if isinstance(supplied_child, str) else ""
            if not child or len(child) > 192:
                raise MlInternTrainingContractError(
                    "resume_checkpoint_invalid",
                    "resume checkpoint identity binding is invalid",
                )
            normalized_binding[key] = child
        for key in hash_keys:
            supplied_child = binding.get(key)
            child = supplied_child.lower() if isinstance(supplied_child, str) else ""
            if len(child) != 64 or any(character not in "0123456789abcdef" for character in child):
                raise MlInternTrainingContractError(
                    "resume_checkpoint_invalid",
                    "resume checkpoint hash binding is invalid",
                )
            normalized_binding[key] = child
        return {"relative_path": relative_path, "binding": normalized_binding}

    def _transition(
        self,
        principal: MlInternTrainingPrincipal,
        job: MlInternTrainingJobDB,
        target: str,
        *,
        phase: str,
        progress_percent: float,
    ) -> MlInternTrainingJobDB:
        assert_job_transition(job.status, target)
        expected = job.version
        now = self._clock()
        job.status = target
        job.phase = phase
        job.progress_percent = max(job.progress_percent, min(100.0, max(0.0, progress_percent)))
        if target == "running" and job.started_at is None:
            job.started_at = now
        if target in {"cancelled", "completed", "failed"}:
            job.finished_at = now
        saved = self._repository.save_job(job, expected_version=expected)
        self._repository.append_event(
            principal,
            saved.id,
            event_type=target,
            dedupe_key=f"transition-{target}-{saved.version}",
            payload=sanitize_event_payload(
                {
                    "status": target,
                    "phase": phase,
                    "progress_percent": saved.progress_percent,
                    "reason_code": saved.error_code,
                    "adapter_id": saved.adapter_id,
                    "cancel_mode": (saved.result_summary or {}).get("cancel_mode"),
                }
            ),
        )
        try:
            from agent.services.task_runtime_service import update_local_task_status

            task_status = {
                "running": "in_progress",
                "completed": "completed",
                "failed": "failed",
                "cancelled": "cancelled",
            }.get(target)
            if task_status:
                update_local_task_status(
                    saved.task_id,
                    task_status,
                    event_type=f"ml_intern_training_{target}",
                    event_actor="hub",
                    event_details={"job_id": saved.id, "reason_code": saved.error_code},
                )
        except Exception:
            pass
        return saved

    @staticmethod
    def _max_steps(spec: Mapping[str, Any]) -> int | None:
        hyperparameters = spec.get("hyperparameters")
        if not isinstance(hyperparameters, Mapping) or hyperparameters.get("max_steps") is None:
            return None
        return int(hyperparameters["max_steps"])

    def _configured_model_ids(self) -> set[str]:
        catalog = self._config.get("base_model_catalog")
        if isinstance(catalog, Mapping):
            return {str(value) for value in catalog}
        return {str(value) for value in self._config.get("base_models") or []}

    def _bind_dataset_provenance(self, command: CreateTrainingJobCommand, dataset: Any) -> None:
        metadata = dict(dataset.dataset_metadata or {})
        dataset_hash = str(dataset.content_sha256 or metadata.get("dataset_sha256") or "").strip().lower()
        if len(dataset_hash) != 64 or any(character not in "0123456789abcdef" for character in dataset_hash):
            raise MlInternTrainingContractError(
                "dataset_hash_unverified",
                "training requires the canonical dataset SHA-256 supplied by the dataset catalog",
                status_code=409,
            )

        dataset_source_ids = normalize_source_ids(metadata.get("source_ids"))
        dataset_run_ids = normalize_run_ids(metadata.get("run_ids"))
        requested_source_ids = normalize_source_ids(command.request_spec.get("source_ids"))
        requested_run_ids = normalize_run_ids(command.request_spec.get("run_ids"))
        if requested_source_ids and requested_source_ids != dataset_source_ids:
            raise MlInternTrainingContractError(
                "source_id_unverified",
                "provided source IDs are not bound to the canonical dataset version",
                status_code=409,
            )
        if requested_run_ids and requested_run_ids != dataset_run_ids:
            raise MlInternTrainingContractError(
                "run_id_unverified",
                "provided run IDs are not bound to the canonical dataset version",
                status_code=409,
            )

        security = dict(self._config.get("unsloth_security") or {})
        trusted_source_ids = set(normalize_source_ids(security.get("trusted_source_ids")))
        trusted_run_ids = set(normalize_run_ids(security.get("trusted_run_ids")))
        if trusted_source_ids and any(identifier not in trusted_source_ids for identifier in dataset_source_ids):
            raise MlInternTrainingContractError(
                "source_id_unverified",
                "dataset source ID is unknown to the configured provenance authority",
                status_code=409,
            )
        if trusted_run_ids and any(identifier not in trusted_run_ids for identifier in dataset_run_ids):
            raise MlInternTrainingContractError(
                "run_id_unverified",
                "dataset run ID is unknown to the configured provenance authority",
                status_code=409,
            )

        provenance_verified = (
            metadata.get("provenance_verified") is True
            and bool(dataset_source_ids)
            and bool(dataset_run_ids)
        )
        if security.get("require_grounded_provenance") is True and not provenance_verified:
            raise MlInternTrainingContractError(
                "grounded_provenance_required",
                "training policy requires verified provided SRC_* and RUN_* bindings",
                status_code=409,
            )

        command.request_spec["dataset_hash"] = dataset_hash
        command.request_spec["provenance_status"] = "verified" if provenance_verified else "unverified"
        if dataset_source_ids:
            command.request_spec["source_ids"] = list(dataset_source_ids)
        else:
            command.request_spec.pop("source_ids", None)
        if dataset_run_ids:
            command.request_spec["run_ids"] = list(dataset_run_ids)
        else:
            command.request_spec.pop("run_ids", None)

    def _worker_capability_probe(self) -> Mapping[str, Any] | None:
        if self._execution_port is None:
            return None
        probe = getattr(self._execution_port, "capability_probe", None)
        if not callable(probe):
            return None
        try:
            return validate_worker_capability_probe(probe())
        except (RuntimeError, TypeError, UnslothWorkerCapabilityContractError):
            return None

    def _worker_supports(
        self,
        job_type: str,
        backend: str,
        gpu_profile: str | None = None,
        *,
        probe: Mapping[str, Any] | None = None,
    ) -> bool:
        snapshot = probe if probe is not None else self._worker_capability_probe()
        if snapshot is None:
            return False
        backend_state = snapshot["backends"].get(backend)
        if (
            not isinstance(backend_state, Mapping)
            or backend_state.get("available") is not True
            or job_type not in backend_state.get("operations", ())
        ):
            return False
        if gpu_profile is None:
            return True
        profile_state = snapshot["gpu_profiles"].get(gpu_profile)
        return isinstance(profile_state, Mapping) and profile_state.get("available") is True

    def _requested_gpu_profile(self, command: CreateTrainingJobCommand) -> str:
        explicit = str(command.request_spec.get("gpu_profile") or "").strip().lower()
        if explicit:
            return explicit
        if command.backend == "mock":
            return "none"
        return str(self._config.get("gpu_profile") or "rtx3080-safe")

    def _assert_request_policy(self, command: CreateTrainingJobCommand, gpu_profile: str) -> None:
        for flag in ("require_dataset_validation", "require_secret_scan"):
            if self._config.get(flag, True) and command.request_spec.get(flag) is False:
                raise MlInternTrainingContractError(
                    "training_policy_override_denied",
                    f"{flag}=false cannot weaken the Hub safety policy",
                    status_code=403,
                )
        profile = get_gpu_profile_defaults(gpu_profile)
        values = command.request_spec.get("hyperparameters")
        hyperparameters = dict(values) if isinstance(values, Mapping) else {}
        batch_size = int(hyperparameters.get("batch_size") or profile.get("batch_size") or 1)
        max_sequence_length = int(
            hyperparameters.get("max_seq_length") or profile.get("max_seq_length") or 512
        )
        max_batch_size = int(profile.get("max_batch_size_hard_limit") or 1)
        max_profile_sequence = int(profile.get("max_seq_length_hard_limit") or 512)
        if batch_size > max_batch_size:
            raise MlInternTrainingContractError(
                "gpu_profile_batch_size_exceeded",
                f"batch_size exceeds the {gpu_profile} hard limit of {max_batch_size}",
            )
        if max_sequence_length > max_profile_sequence:
            raise MlInternTrainingContractError(
                "gpu_profile_sequence_length_exceeded",
                f"max_seq_length exceeds the {gpu_profile} hard limit of {max_profile_sequence}",
            )
        bounded_parameters = (
            (
                "gradient_accumulation_steps",
                int(profile.get("max_gradient_accumulation_steps_hard_limit") or 1),
            ),
            ("lora_rank", int(profile.get("max_lora_rank_hard_limit") or 1)),
            ("lora_alpha", int(profile.get("max_lora_alpha_hard_limit") or 1)),
        )
        for field, maximum in bounded_parameters:
            value = int(hyperparameters.get(field) or profile.get(field) or 1)
            if value > maximum:
                raise MlInternTrainingContractError(
                    "gpu_profile_adapter_parameter_exceeded",
                    f"{field} exceeds the {gpu_profile} hard limit of {maximum}",
                )
        dropout = float(hyperparameters.get("lora_dropout", profile.get("lora_dropout") or 0.0))
        if dropout > float(profile.get("max_lora_dropout_hard_limit") or 0.0):
            raise MlInternTrainingContractError(
                "gpu_profile_adapter_parameter_exceeded",
                f"lora_dropout exceeds the {gpu_profile} hard limit",
            )
        target_modules = hyperparameters.get("target_modules")
        if isinstance(target_modules, list) and len(target_modules) > int(
            profile.get("max_target_modules_hard_limit") or 1
        ):
            raise MlInternTrainingContractError(
                "gpu_profile_adapter_parameter_exceeded",
                f"target_modules exceeds the {gpu_profile} hard limit",
            )
        requires_4bit = profile.get("required_quantization") == "4bit"
        requested_4bit = bool(
            hyperparameters.get(
                "load_in_4bit",
                str(command.request_spec.get("method") or "").strip().lower()
                == "qlora",
            )
        )
        if requires_4bit and not requested_4bit:
            raise MlInternTrainingContractError(
                "gpu_profile_quantization_required",
                f"{gpu_profile} requires 4bit quantization",
            )

    @staticmethod
    def _secret_scan_passed(validation: Mapping[str, Any]) -> bool:
        reports = [validation.get("train")]
        if validation.get("validation") is not None:
            reports.append(validation.get("validation"))
        structured = [report for report in reports if isinstance(report, Mapping)]
        if structured:
            return all(report.get("secret_scan_passed") is True for report in structured)
        # Compatibility for pre-catalog validation reports.
        return validation.get("secret_scan_passed") is True or (
            validation.get("ok") is True
            and int(validation.get("secret_finding_count") or 0) == 0
            and not validation.get("secret_findings")
        )

    @staticmethod
    def _device_for_gpu_profile(gpu_profile: str) -> str:
        return "cpu" if gpu_profile == "none" else "nvidia"

    @classmethod
    def _safe_result_summary(cls, result: Mapping[str, Any]) -> dict[str, Any]:
        forbidden_keys = {
            "samples",
            "records",
            "prompt",
            "prompts",
            "output",
            "outputs",
            "base_output",
            "adapter_output",
            "logs",
        }

        def clean_metric(value: Any, *, depth: int = 0) -> Any:
            if depth > 3:
                return None
            if value is None or isinstance(value, bool):
                return value
            if isinstance(value, int):
                return value if abs(value) <= 2**63 - 1 else None
            if isinstance(value, float):
                return value if math.isfinite(value) else None
            if isinstance(value, str):
                return value[:128]
            if isinstance(value, Mapping):
                return {
                    str(key)[:64]: clean_metric(child, depth=depth + 1)
                    for key, child in list(value.items())[:64]
                    if str(key).strip().lower() not in forbidden_keys
                }
            return None

        summary: dict[str, Any] = {}
        metrics = result.get("metrics")
        if isinstance(metrics, Mapping):
            summary["metrics"] = clean_metric(metrics)
        artifacts = result.get("artifacts")
        if isinstance(artifacts, list):
            admitted: list[dict[str, Any]] = []
            for item in artifacts[:64]:
                if not isinstance(item, Mapping) or not item.get("name"):
                    continue
                size = item.get("size_bytes")
                if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= 2**63 - 1:
                    continue
                admitted.append(
                    {
                        "name": str(item.get("name") or "")[:191],
                        "sha256": str(item.get("sha256") or "")[:64],
                        "size_bytes": size,
                    }
                )
            summary["artifacts"] = admitted
        if result.get("resume_checkpoint") is not None:
            summary["resume_checkpoint"] = cls._normalize_resume_checkpoint(result["resume_checkpoint"])
        if result.get("cancel_mode") in {"cooperative", "forced"}:
            summary["cancel_mode"] = result["cancel_mode"]
        return summary

    @staticmethod
    def _legacy_spec(spec: Mapping[str, Any], dataset_path: str) -> dict[str, Any]:
        hyperparameters = dict(spec.get("hyperparameters") or {})
        return {
            "job_type": str(spec.get("job_type") or "train_lora"),
            "base_model": spec.get("base_model"),
            "dataset_path": dataset_path,
            "method": str(spec.get("method") or "qlora"),
            "output_dir": str(spec.get("output_name") or "adapter"),
            **hyperparameters,
        }

    def _accepted_read_model(self, job: MlInternTrainingJobDB, *, replayed: bool) -> dict[str, Any]:
        payload = self._read_models.job(job, detail=True)
        payload.update(
            {
                "idempotent_replay": replayed,
                "poll_url": f"/api/ml-intern-training/jobs/{job.id}",
                "events_url": f"/api/ml-intern-training/jobs/{job.id}/events",
            }
        )
        return payload


_control_service: MlInternTrainingControlService | None = None
_control_service_signature: str | None = None
_control_service_lock = threading.RLock()


def get_ml_intern_training_control_service(
    config: Mapping[str, Any] | None = None,
    *,
    execution_port: MlInternTrainingExecutionPort | None = None,
) -> MlInternTrainingControlService:
    if execution_port is not None:
        return MlInternTrainingControlService(config, execution_port=execution_port)
    raw = dict(config or {})
    try:
        signature = hashlib.sha256(json_dumps_canonical(raw).encode("utf-8")).hexdigest()
    except (TypeError, ValueError):
        signature = hashlib.sha256(repr(sorted(raw)).encode()).hexdigest()
    global _control_service, _control_service_signature
    with _control_service_lock:
        if _control_service is None or _control_service_signature != signature:
            previous = _control_service
            from agent.services.ml_intern_training_worker_port import training_worker_port_from_environment

            port = training_worker_port_from_environment(raw)
            publisher = None
            if port is not None:
                from agent.services.ml_intern_training_result_publisher import build_result_publisher

                publisher = build_result_publisher(raw)
            _control_service = MlInternTrainingControlService(
                raw,
                execution_port=port,
                result_publisher=publisher,
            )
            _control_service_signature = signature
            if previous is not None:
                previous.begin_shutdown()
        return _control_service


def begin_ml_intern_training_control_shutdown() -> None:
    """Drain the currently composed Hub control service without creating one."""

    with _control_service_lock:
        current = _control_service
    if current is not None:
        current.begin_shutdown()


def json_dumps_canonical(value: Mapping[str, Any]) -> str:
    import json

    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
