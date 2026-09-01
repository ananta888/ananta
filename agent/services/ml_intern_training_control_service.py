from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from agent.db_models import MlInternTrainingJobDB
from agent.repositories.ml_intern_training import (
    MlInternTrainingRepository,
    MlInternTrainingRepositoryConflict,
    get_ml_intern_training_repository,
)
from agent.services.ml_intern_backend_selection_service import MlInternBackendSelectionService
from agent.services.ml_intern_training_config_service import get_gpu_profile_defaults
from agent.services.ml_intern_training_contract import (
    UNSLOTH_BACKENDS,
    CreateTrainingJobCommand,
    MlInternTrainingContractError,
    UnslothCapabilityFacet,
    UnslothCapabilitySnapshot,
    assert_job_transition,
    idempotency_digest,
    request_digest,
)
from agent.services.ml_intern_training_control_execution import (
    MlInternTrainingControlExecutionMixin,
)
from agent.services.ml_intern_training_control_policy import (
    MlInternTrainingControlPolicyMixin,
)
from agent.services.ml_intern_training_read_model_service import MlInternTrainingReadModelService
from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal
from agent.services.ml_intern_training_result_publisher import MlInternTrainingResultPublisher
from agent.services.ml_intern_training_worker_job_projection import (
    MlInternTrainingWorkerJobProjectionPort,
    get_ml_intern_training_worker_job_projection,
)


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


class MlInternTrainingControlService(MlInternTrainingControlExecutionMixin, MlInternTrainingControlPolicyMixin):
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
        backend_ids = (
            "mock",
            "needle",
            "peft_trl",
            "unsloth",
            "unsloth_vision",
            "unsloth_audio",
            "unsloth_embedding",
            "axolotl",
            "llamafactory",
            "autotrain",
            "torchtune",
        )
        backends = []
        for backend in backend_ids:
            worker_supports = worker_probe is not None and self._worker_supports(
                "train_lora", backend, probe=worker_probe
            )
            policy_allows = backend == "mock" or mode == "live"
            available = enabled and policy_allows and (backend == "mock" or (runtime_available and worker_supports))
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
                            else ("worker_unavailable" if not runtime_available else "worker_capability_unavailable")
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
                    "max_batch_size": int(get_gpu_profile_defaults(name).get("max_batch_size_hard_limit", 1)),
                    "max_sequence_length": int(get_gpu_profile_defaults(name).get("max_seq_length_hard_limit", 512)),
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
                reason_code=("unsloth_studio_client_unavailable" if studio_configured else "unsloth_studio_disabled"),
                source="configuration",
                operations=("health", "status"),
            )
        )
        facets.append(
            UnslothCapabilityFacet(
                facet_id="mcp.control",
                available=False,
                reason_code=(
                    "unsloth_mcp_client_unavailable" if security.get("mcp_enabled") is True else "unsloth_mcp_disabled"
                ),
                source="configuration",
                operations=("status",),
            )
        )
        hardware_available = any(item["available"] and item["id"] != "none" for item in gpu_profiles) and any(
            backend_capabilities[name]["available"] for name in UNSLOTH_BACKENDS
        )
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
            "backends": MlInternBackendSelectionService().enrich_catalog(backends),
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
        """Admit and automatically offer a job to the Hub scheduler.

        This compatibility entry point preserves the original service contract.
        Request handlers that still have request-owned persistence or audit work
        must use :meth:`admit_job` and dispatch only after those writes finish.
        """

        return self._create_job(
            principal,
            payload,
            idempotency_key=idempotency_key,
            auto_dispatch=True,
        )

    def admit_job(
        self,
        principal: MlInternTrainingPrincipal,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], bool]:
        """Persist and materialize a queued job without starting execution."""

        return self._create_job(
            principal,
            payload,
            idempotency_key=idempotency_key,
            auto_dispatch=False,
        )

    def _create_job(
        self,
        principal: MlInternTrainingPrincipal,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str,
        auto_dispatch: bool,
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
        if auto_dispatch:
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
                **({"cancel_reason_digest": hashlib.sha256(reason.encode()).hexdigest()} if reason else {}),
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
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
