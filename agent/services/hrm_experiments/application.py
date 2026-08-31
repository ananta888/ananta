"""Hub-owned HRM experiment lifecycle and Worker authorization service."""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from agent.db_models import HrmEvaluationReportDB, HrmRunDB
from agent.repositories.hrm_experiments import (
    HrmExperimentRepository,
    HrmRepositoryConflict,
    get_hrm_experiment_repository,
)
from agent.repositories.worker_slot_lease import WorkerSlotLeaseRepository
from agent.services.hrm_experiments.admission import (
    HrmAdmissionRepositoryPort,
    HrmAdmissionScope,
    HrmManifestAdmissionService,
)
from agent.services.hrm_experiments.application_paging import HrmCursorError, decode_cursor, encode_cursor, page
from agent.services.hrm_experiments.artifact_store import HrmArtifactStoreAdapter
from agent.services.hrm_experiments.contracts import (
    HrmContractValidator,
    default_hrm_contract_validator,
)
from agent.services.hrm_experiments.control_plane import (
    HrmExperimentControlPlaneService,
    default_hrm_experiment_control_plane_service,
)
from agent.services.hrm_experiments.digests import (
    canonical_digest,
    contract_schema_digest,
    run_payload_digest,
)

_TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled"})
_ISOLATION_CONTROLS = (
    "non_root",
    "no_new_privileges",
    "cap_drop_all",
    "read_only_rootfs",
    "network_denied",
    "cgroup_limits",
    "seccomp",
    "mac_policy",
)


class HrmApplicationError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 409) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class HrmPrincipal:
    tenant_id: str
    subject: str

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.subject:
            raise ValueError("HRM principal requires tenant and subject")


@dataclass(frozen=True, slots=True)
class HrmExecutionBinding:
    task_id: str
    worker_job_id: str
    assignment_id: str
    dispatch_lease_id: str
    worker_url: str
    deadline_epoch_ms: int


class HrmTaskQueuePort(Protocol):
    def create_run_task(self, *, run_id: str, profile_id: str, subject: str) -> str: ...

    def cancel_run_task(self, task_id: str, *, reason_code: str) -> None: ...


class HrmExecutionBindingPort(Protocol):
    def resolve(
        self, *, task_id: str, worker_job_id: str, worker_url: str
    ) -> HrmExecutionBinding: ...


class AnantaHrmTaskQueueAdapter:
    """Use the canonical Hub task queue without exposing it to Workers."""

    def create_run_task(self, *, run_id: str, profile_id: str, subject: str) -> str:
        from agent.services.task_queue_service import get_task_queue_service

        task_id = f"hrm-task-{uuid.uuid4()}"
        get_task_queue_service().ingest_task(
            task_id=task_id,
            status="todo",
            title=f"HRM experiment {run_id}",
            description="Execute one admitted HRM experiment through the isolated runner.",
            priority="medium",
            created_by=subject,
            source="hrm_experiments",
            tags=["hrm-experiment", "isolated-runner"],
            event_type="hrm_experiment_task_ingested",
            event_channel="hrm_experiments",
            extra_fields={
                "task_kind": "hrm_experiment",
                "required_capabilities": [
                    "hrm_experiment",
                    f"hrm_experiment.profile.{profile_id}",
                ],
                "worker_execution_context": {
                    "hrm_experiment": {"run_id": run_id}
                },
                "verification_spec": {
                    "schema": "ananta.hrm-experiments.worker-verification.v1",
                    "required_checks": [
                        "authority_binding",
                        "lease_fencing",
                        "run_result_contract",
                    ],
                },
            },
        )
        return task_id

    def cancel_run_task(self, task_id: str, *, reason_code: str) -> None:
        from agent.services.task_runtime_service import update_local_task_status

        update_local_task_status(
            task_id,
            "cancelled",
            event_type="hrm_experiment_task_cancelled",
            event_actor="hrm_experiments",
            event_details={"reason_code": reason_code},
            status_reason_code=reason_code,
        )


class AnantaHrmExecutionBindingAdapter:
    """Resolve current WorkerJob and slot lease from authoritative Hub state."""

    def resolve(
        self, *, task_id: str, worker_job_id: str, worker_url: str
    ) -> HrmExecutionBinding:
        from agent.services.repository_registry import get_repository_registry
        from agent.services.task_runtime_service import get_local_task_status

        task = get_local_task_status(task_id)
        job = get_repository_registry().worker_job_repo.get_by_id(worker_job_id)
        if task is None or job is None:
            raise HrmApplicationError("hrm.execution_binding_not_found", status_code=404)
        normalized_worker_url = worker_url.rstrip("/")
        if (
            str(task.get("current_worker_job_id") or "") != worker_job_id
            or str(job.parent_task_id or "") != task_id
            or str(job.worker_url or "").rstrip("/") != normalized_worker_url
            or str(task.get("assigned_agent_url") or "").rstrip("/")
            != normalized_worker_url
            or str(task.get("task_kind") or "") != "hrm_experiment"
        ):
            raise HrmApplicationError("hrm.execution_binding_mismatch")
        lease_id = str(job.slot_lease_id or "")
        lease = WorkerSlotLeaseRepository().get_by_id(lease_id) if lease_id else None
        if (
            lease is None
            or lease.status != "active"
            or str(lease.worker_job_id or "") != worker_job_id
            or str(lease.parent_task_id or "") != task_id
            or float(lease.deadline_at) <= time.time()
        ):
            raise HrmApplicationError("hrm.dispatch_lease_invalid")
        assignment_id = str(job.subtask_id or f"assignment:{worker_job_id}")
        return HrmExecutionBinding(
            task_id=task_id,
            worker_job_id=worker_job_id,
            assignment_id=assignment_id,
            dispatch_lease_id=lease_id,
            worker_url=normalized_worker_url,
            deadline_epoch_ms=int(float(lease.deadline_at) * 1000),
        )


class _ScopedAdmissionRepository(HrmAdmissionRepositoryPort):
    def __init__(
        self,
        *,
        repository: HrmExperimentRepository,
        artifacts: HrmArtifactStoreAdapter,
        owner_subject: str,
    ) -> None:
        self._repository = repository
        self._artifacts = artifacts
        self._owner_subject = owner_subject

    def save_dataset(self, manifest: Mapping[str, Any]) -> Mapping[str, Any]:
        records = self._artifacts.dataset_records(str(manifest["source"]["locator"]))
        return self._repository.save_dataset(
            manifest,
            records,
            owner_subject=self._owner_subject,
        ).manifest

    def save_checkpoint(self, manifest: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._repository.save_checkpoint(
            manifest,
            owner_subject=self._owner_subject,
        ).manifest


class HrmExperimentApplicationService:
    def __init__(
        self,
        *,
        repository: HrmExperimentRepository | None = None,
        artifacts: HrmArtifactStoreAdapter | None = None,
        task_queue: HrmTaskQueuePort | None = None,
        execution_bindings: HrmExecutionBindingPort | None = None,
        control_plane: HrmExperimentControlPlaneService | None = None,
        contracts: HrmContractValidator | None = None,
        clock=time.time,
    ) -> None:
        self._repository = repository or get_hrm_experiment_repository()
        self._artifacts = artifacts or HrmArtifactStoreAdapter()
        self._task_queue = task_queue or AnantaHrmTaskQueueAdapter()
        self._bindings = execution_bindings or AnantaHrmExecutionBindingAdapter()
        self._control_plane = control_plane or default_hrm_experiment_control_plane_service()
        self._contracts = contracts or default_hrm_contract_validator
        self._clock = clock

    def advertise_capability(
        self,
        *,
        worker_id: str,
        worker_url: str,
        capability: Mapping[str, Any],
        ttl_seconds: int,
    ) -> dict[str, Any]:
        projection = dict(capability)
        self._contracts.validate("capability_probe", projection)
        unsigned = {key: value for key, value in projection.items() if key != "capability_digest"}
        if (
            projection["worker_id"] != worker_id
            or projection["feature_enabled"] is not True
            or projection["capability_digest"] != canonical_digest(unsigned)
            or not all(projection["isolation"].get(key) is True for key in _ISOLATION_CONTROLS)
        ):
            raise HrmApplicationError("hrm.capability_advertisement_invalid", status_code=400)
        ttl = max(10, min(int(ttl_seconds), 300))
        row = self._repository.upsert_capability(
            worker_id=worker_id,
            worker_url=worker_url.rstrip("/"),
            projection=projection,
            expires_at=self._clock() + ttl,
        )
        return {
            "accepted": True,
            "capability_digest": row.capability_digest,
            "expires_at_epoch_ms": int(row.expires_at * 1000),
        }

    def register_dataset(
        self,
        principal: HrmPrincipal,
        manifest: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        self._require_enabled()
        self._require_principal_scope(principal, manifest)
        request_digest = canonical_digest(manifest)
        receipt, replayed = self._claim_idempotency(
            principal,
            operation="register_dataset",
            idempotency_key=idempotency_key,
            request_digest=request_digest,
        )
        if replayed and receipt.state == "completed":
            return dict(receipt.response)
        admission = HrmManifestAdmissionService(
            artifacts=self._artifacts,
            repository=_ScopedAdmissionRepository(
                repository=self._repository,
                artifacts=self._artifacts,
                owner_subject=principal.subject,
            ),
            contracts=self._contracts,
        )
        try:
            result = admission.admit_dataset(
                manifest,
                scope=HrmAdmissionScope(
                    principal.tenant_id,
                    str(manifest["scope"]["project_id"]),
                ),
            )
            self._repository.complete_idempotency(
                receipt.id,
                request_digest=request_digest,
                resource_id=str(result["dataset_id"]),
                response=result,
            )
            return result
        except (HrmRepositoryConflict, Exception) as exc:
            self._repository.release_idempotency(
                receipt.id, request_digest=request_digest
            )
            if not isinstance(exc, HrmRepositoryConflict):
                raise
            raise HrmApplicationError(exc.reason_code) from exc

    def list_datasets(
        self, principal: HrmPrincipal, *, project_id: str, cursor: str | None, limit: int
    ) -> dict[str, Any]:
        offset = self._decode_cursor(cursor)
        rows = self._repository.list_datasets(
            principal.tenant_id, project_id, offset=offset, limit=limit + 1
        )
        return page([dict(row.manifest) for row in rows], offset=offset, limit=limit)

    def admit_checkpoint(
        self,
        principal: HrmPrincipal,
        manifest: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        self._require_enabled()
        self._require_principal_scope(principal, manifest)
        request_digest = canonical_digest(manifest)
        receipt, replayed = self._claim_idempotency(
            principal,
            operation="admit_checkpoint",
            idempotency_key=idempotency_key,
            request_digest=request_digest,
        )
        if replayed and receipt.state == "completed":
            return dict(receipt.response)
        capability = self._control_plane.capability()
        if not capability["feature_enabled"] or capability["worker_id"] == "unavailable":
            raise HrmApplicationError("hrm.worker_unavailable")
        runtime_digest = canonical_digest(capability["runtime"])
        admission = HrmManifestAdmissionService(
            artifacts=self._artifacts,
            repository=_ScopedAdmissionRepository(
                repository=self._repository,
                artifacts=self._artifacts,
                owner_subject=principal.subject,
            ),
            contracts=self._contracts,
        )
        try:
            result = admission.admit_checkpoint(
                manifest,
                scope=HrmAdmissionScope(
                    principal.tenant_id,
                    str(manifest["scope"]["project_id"]),
                ),
                expected_runtime_digest=runtime_digest,
            )
            self._repository.complete_idempotency(
                receipt.id,
                request_digest=request_digest,
                resource_id=str(result["checkpoint_id"]),
                response=result,
            )
            return result
        except (HrmRepositoryConflict, Exception) as exc:
            self._repository.release_idempotency(
                receipt.id, request_digest=request_digest
            )
            if not isinstance(exc, HrmRepositoryConflict):
                raise
            raise HrmApplicationError(exc.reason_code) from exc

    def list_checkpoints(
        self, principal: HrmPrincipal, *, project_id: str, cursor: str | None, limit: int
    ) -> dict[str, Any]:
        offset = self._decode_cursor(cursor)
        rows = self._repository.list_checkpoints(
            principal.tenant_id, project_id, offset=offset, limit=limit + 1
        )
        return page([dict(row.manifest) for row in rows], offset=offset, limit=limit)

    def start_run(
        self,
        principal: HrmPrincipal,
        intent: Mapping[str, Any],
        *,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], bool]:
        self._require_enabled()
        candidate = dict(intent)
        self._contracts.validate("run_intent", candidate)
        self._require_principal_scope(principal, candidate)
        project_id = str(candidate["scope"]["project_id"])
        dataset = self._repository.get_dataset(
            principal.tenant_id, project_id, str(candidate["dataset_id"])
        )
        if dataset is None:
            raise HrmApplicationError("hrm.dataset_not_found", status_code=404)
        checkpoint = None
        checkpoint_id = candidate.get("checkpoint_id")
        if checkpoint_id is not None:
            checkpoint = self._repository.get_checkpoint(
                principal.tenant_id, project_id, str(checkpoint_id)
            )
            if checkpoint is None or checkpoint.state != "verified":
                raise HrmApplicationError("hrm.checkpoint_not_available", status_code=404)
            if dataset.puzzle_type not in checkpoint.manifest["compatibility"]["dataset_types"]:
                raise HrmApplicationError("hrm.checkpoint_dataset_incompatible")
        preflight = self._control_plane.preflight(
            project_id=project_id,
            profile_id=str(candidate["profile_id"]),
        )
        if not preflight["allowed"]:
            raise HrmApplicationError(str(preflight["reason_codes"][0]))
        self._require_limits_within(candidate["limits"], preflight["effective_limits"])
        request_digest = canonical_digest(candidate)
        key_digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        run_id = f"hrm-run-{uuid.uuid4()}"
        pending_attempt_id = f"hrm-attempt-{uuid.uuid4()}"
        task_id = f"pending:{run_id}"
        row = HrmRunDB(
            id=run_id,
            tenant_id=principal.tenant_id,
            project_id=project_id,
            owner_subject=principal.subject,
            task_id=task_id,
            profile_id=str(candidate["profile_id"]),
            mode=str(candidate["mode"]),
            dataset_id=dataset.dataset_id,
            dataset_digest=dataset.content_digest,
            checkpoint_id=checkpoint.checkpoint_id if checkpoint else None,
            checkpoint_digest=checkpoint.content_digest if checkpoint else None,
            status="queued",
            intent=candidate,
            idempotency_key_digest=key_digest,
            request_digest=request_digest,
            capability_digest=str(preflight["capability_digest"]),
            policy_digest=str(preflight["policy_digest"]),
            attempt_id=pending_attempt_id,
            epoch=1,
        )
        try:
            persisted, replayed = self._repository.create_run(row)
        except HrmRepositoryConflict as exc:
            raise HrmApplicationError(exc.reason_code) from exc
        if replayed:
            return self.run_status(persisted), True
        try:
            persisted.task_id = self._task_queue.create_run_task(
                run_id=persisted.id,
                profile_id=persisted.profile_id,
                subject=principal.subject,
            )
            persisted = self._repository.save_run(persisted)
            self._event(persisted, "accepted", message="HRM run accepted by Hub.")
        except Exception as exc:
            persisted.status = "failed"
            persisted.reason_code = "hrm.task_queue_unavailable"
            persisted.finished_at = self._clock()
            self._repository.save_run(persisted)
            raise HrmApplicationError("hrm.task_queue_unavailable", status_code=503) from exc
        return self.run_status(persisted), False

    def list_runs(
        self, principal: HrmPrincipal, *, project_id: str, cursor: str | None, limit: int
    ) -> dict[str, Any]:
        offset = self._decode_cursor(cursor)
        rows = self._repository.list_runs(
            principal.tenant_id, project_id, offset=offset, limit=limit + 1
        )
        return page([self.run_status(row) for row in rows], offset=offset, limit=limit)

    def get_run(self, principal: HrmPrincipal, *, project_id: str, run_id: str) -> dict[str, Any]:
        run = self._repository.get_run(principal.tenant_id, project_id, run_id)
        if run is None:
            raise HrmApplicationError("hrm.run_not_found", status_code=404)
        return self.run_status(run)

    def authorize_execution(
        self,
        *,
        run_id: str,
        task_id: str,
        worker_job_id: str,
        worker_url: str,
    ) -> dict[str, Any]:
        self._require_enabled()
        run = self._repository.get_run_internal(run_id)
        if run is None or run.task_id != task_id:
            raise HrmApplicationError("hrm.run_not_found", status_code=404)
        if run.cancel_requested or run.status in _TERMINAL_RUN_STATUSES:
            raise HrmApplicationError("hrm.execution_not_active")
        binding = self._bindings.resolve(
            task_id=task_id,
            worker_job_id=worker_job_id,
            worker_url=worker_url,
        )
        capability_row = self._repository.current_capability(worker_url=binding.worker_url)
        if capability_row is None:
            raise HrmApplicationError("hrm.worker_capability_expired")
        capability = dict(capability_row.projection)
        self._contracts.validate("capability_probe", capability)
        if (
            capability["capability_digest"] != run.capability_digest
            or run.profile_id not in capability["supported_profiles"]
            or not all(capability["isolation"].get(key) is True for key in _ISOLATION_CONTROLS)
        ):
            raise HrmApplicationError("hrm.worker_capability_mismatch")
        if run.execution_envelope and run.worker_job_id == worker_job_id:
            if int(run.deadline_epoch_ms or 0) <= int(self._clock() * 1000):
                raise HrmApplicationError("hrm.execution_authority_expired")
            return dict(run.execution_envelope)
        dataset = self._repository.get_dataset(run.tenant_id, run.project_id, run.dataset_id)
        if dataset is None or dataset.content_digest != run.dataset_digest:
            raise HrmApplicationError("hrm.dataset_not_available")
        checkpoint = None
        if run.checkpoint_id:
            checkpoint = self._repository.get_checkpoint(
                run.tenant_id, run.project_id, run.checkpoint_id
            )
            if checkpoint is None or checkpoint.content_digest != run.checkpoint_digest:
                raise HrmApplicationError("hrm.checkpoint_not_available")
            if checkpoint.manifest["compatibility"]["runtime_digest"] != canonical_digest(capability["runtime"]):
                raise HrmApplicationError("hrm.checkpoint_runtime_incompatible")
        if run.worker_job_id is not None:
            run.epoch += 1
            run.attempt_id = f"hrm-attempt-{uuid.uuid4()}"
        deadline = min(
            binding.deadline_epoch_ms,
            int(self._clock() * 1000) + int(run.intent["limits"]["wallclock_seconds"]) * 1000,
        )
        authority = {
            "task_id": binding.task_id,
            "assignment_id": binding.assignment_id,
            "worker_job_id": binding.worker_job_id,
            "dispatch_lease_id": binding.dispatch_lease_id,
            "attempt_id": str(run.attempt_id),
            "epoch": int(run.epoch),
            "deadline_epoch_ms": deadline,
            "policy_digest": run.policy_digest,
            "schema_digest": contract_schema_digest(),
            "payload_digest": "0" * 64,
        }
        request = {
            "schema": "ananta.hrm-experiments.run-request.v1",
            "run_id": run.id,
            "scope": dict(run.intent["scope"]),
            "authority": authority,
            "profile_id": run.profile_id,
            "mode": run.mode,
            "runtime": dict(capability["runtime"]),
            "dataset_id": run.dataset_id,
            "dataset_digest": run.dataset_digest,
            "checkpoint_id": run.checkpoint_id,
            "checkpoint_digest": run.checkpoint_digest,
            "limits": dict(run.intent["limits"]),
            "seed": int(run.intent["seed"]),
            "precision": str(run.intent["precision"]),
            "parameters": dict(run.intent["parameters"]),
        }
        request["authority"]["payload_digest"] = run_payload_digest(request)
        admission_unsigned = {
            "dataset_id": run.dataset_id,
            "dataset_digest": run.dataset_digest,
            "checkpoint_id": run.checkpoint_id,
            "checkpoint_digest": run.checkpoint_digest,
        }
        envelope = {
            "run_request": request,
            "expected_authority": {
                key: authority[key]
                for key in (
                    "task_id",
                    "assignment_id",
                    "worker_job_id",
                    "dispatch_lease_id",
                    "attempt_id",
                    "epoch",
                    "policy_digest",
                    "schema_digest",
                )
            },
            "admission": {
                **admission_unsigned,
                "admission_digest": canonical_digest(admission_unsigned),
            },
            "dataset": {
                "manifest": dict(dataset.manifest),
                "records": list(dataset.records),
            },
        }
        self._contracts.validate("run_request", request)
        run.worker_url = binding.worker_url
        run.worker_job_id = binding.worker_job_id
        run.assignment_id = binding.assignment_id
        run.dispatch_lease_id = binding.dispatch_lease_id
        run.deadline_epoch_ms = deadline
        run.execution_envelope = envelope
        run.status = "running"
        run.started_at = run.started_at or self._clock()
        self._repository.save_run(run)
        self._event(run, "status", message="HRM run authorized for assigned Worker.")
        return envelope

    def submit_result(
        self,
        *,
        run_id: str,
        worker_url: str,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        candidate = dict(result)
        self._contracts.validate("run_result", candidate)
        run = self._repository.get_run_internal(run_id)
        if run is None:
            raise HrmApplicationError("hrm.run_not_found", status_code=404)
        if run.worker_url != worker_url.rstrip("/"):
            raise HrmApplicationError("hrm.result_worker_mismatch")
        if (
            candidate["run_id"] != run.id
            or candidate["attempt_id"] != run.attempt_id
            or int(candidate["epoch"]) != run.epoch
        ):
            raise HrmApplicationError("hrm.result_authority_mismatch")
        unsigned = {key: value for key, value in candidate.items() if key != "result_digest"}
        if candidate["result_digest"] != canonical_digest(unsigned):
            raise HrmApplicationError("hrm.result_digest_mismatch")
        for artifact in candidate["artifacts"]:
            inspection = self._artifacts.inspect_result_digest(
                str(artifact["content_digest"])
            )
            if (
                artifact["state"] != "verified"
                or not inspection.verified
                or inspection.size_bytes != artifact["size_bytes"]
            ):
                raise HrmApplicationError("hrm.result_artifact_unverified")
        if run.status in _TERMINAL_RUN_STATUSES:
            if dict(run.result or {}) == candidate:
                return {"accepted": True, "idempotent_replay": True, "run_id": run.id}
            raise HrmApplicationError("hrm.result_terminal_conflict")
        if run.cancel_requested:
            run.status = "cancelled"
            run.reason_code = "hrm.cancelled_by_user"
            candidate = {
                **candidate,
                "status": "cancelled",
                "error": {
                    "class": "cancel",
                    "reason_code": "hrm.cancelled_by_user",
                    "retryable": False,
                    "message": "Run cancellation was accepted by the Hub.",
                },
            }
            unsigned = {key: value for key, value in candidate.items() if key != "result_digest"}
            candidate["result_digest"] = canonical_digest(unsigned)
            self._contracts.validate("run_result", candidate)
        else:
            run.status = str(candidate["status"])
            run.reason_code = (
                str((candidate.get("error") or {}).get("reason_code") or "") or None
            )
        run.result = candidate
        run.finished_at = self._clock()
        self._repository.save_run(run)
        self._event(
            run,
            "terminal",
            metrics=list(candidate["metrics"]),
            reason_code=run.reason_code,
            message="HRM run reached a terminal state.",
        )
        return {"accepted": True, "idempotent_replay": False, "run_id": run.id}

    def cancel_run(
        self,
        principal: HrmPrincipal,
        *,
        project_id: str,
        run_id: str,
        request_payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._require_enabled()
        candidate = dict(request_payload)
        self._contracts.validate("cancel_request", candidate)
        if candidate["run_id"] != run_id:
            raise HrmApplicationError("hrm.cancel_run_mismatch", status_code=400)
        run = self._repository.get_run(principal.tenant_id, project_id, run_id)
        if run is None:
            raise HrmApplicationError("hrm.run_not_found", status_code=404)
        request_digest = canonical_digest(
            {"project_id": project_id, "run_id": run_id, "request": candidate}
        )
        receipt, replayed = self._claim_idempotency(
            principal,
            operation=f"cancel_run:{run_id}",
            idempotency_key=idempotency_key,
            request_digest=request_digest,
        )
        if replayed and receipt.state == "completed":
            return dict(receipt.response)
        if replayed and run.cancel_requested:
            recovered = self.run_status(run)
            self._repository.complete_idempotency(
                receipt.id,
                request_digest=request_digest,
                resource_id=run.id,
                response=recovered,
            )
            return recovered
        try:
            if int(candidate["expected_epoch"]) != run.epoch:
                raise HrmApplicationError("hrm.cancel_epoch_conflict")
            if run.status in _TERMINAL_RUN_STATUSES:
                raise HrmApplicationError("hrm.run_already_terminal")
            run.cancel_requested = True
            run.reason_code = "hrm.cancel_requested"
            if run.status == "queued":
                run.status = "cancelled"
                run.finished_at = self._clock()
                self._task_queue.cancel_run_task(
                    run.task_id, reason_code="hrm.cancelled_by_user"
                )
            else:
                run.status = "cancel_requested"
            self._repository.save_run(run)
            self._event(
                run,
                "status",
                reason_code=run.reason_code,
                message=str(candidate["reason"]),
            )
            result = self.run_status(run)
            self._repository.complete_idempotency(
                receipt.id,
                request_digest=request_digest,
                resource_id=run.id,
                response=result,
            )
            return result
        except Exception:
            self._repository.release_idempotency(
                receipt.id, request_digest=request_digest
            )
            raise

    def list_events(
        self,
        principal: HrmPrincipal,
        *,
        project_id: str,
        run_id: str,
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        run = self._repository.get_run(principal.tenant_id, project_id, run_id)
        if run is None:
            raise HrmApplicationError("hrm.run_not_found", status_code=404)
        after = self._decode_cursor(cursor)
        rows = self._repository.list_events(
            principal.tenant_id,
            project_id,
            run_id,
            after=after,
            limit=limit + 1,
        )
        selected = rows[:limit]
        result = {
            "schema": "ananta.hrm-experiments.event-page.v1",
            "run_id": run.id,
            "attempt_id": str(run.attempt_id),
            "epoch": max(1, run.epoch),
            "events": [dict(row.event) for row in selected],
            "next_cursor": (
                encode_cursor(selected[-1].sequence)
                if len(rows) > limit and selected
                else None
            ),
        }
        self._contracts.validate("event_page", result)
        return result

    def create_evaluation(
        self,
        principal: HrmPrincipal,
        *,
        project_id: str,
        run_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        run = self._repository.get_run(principal.tenant_id, project_id, run_id)
        if run is None:
            raise HrmApplicationError("hrm.run_not_found", status_code=404)
        if run.status != "completed" or not run.result:
            raise HrmApplicationError("hrm.run_not_evaluable")
        dataset = self._repository.get_dataset(run.tenant_id, run.project_id, run.dataset_id)
        if dataset is None:
            raise HrmApplicationError("hrm.dataset_not_available")
        metrics = {str(item["name"]): float(item["value"]) for item in run.result["metrics"]}
        accuracy = metrics.get("exact_accuracy")
        exact = int(round((accuracy or 0.0) * len(dataset.records)))
        execution = dict(run.execution_envelope)
        runtime = dict(execution["run_request"]["runtime"])
        capability_row = self._repository.current_capability(worker_url=run.worker_url)
        device = dict((capability_row.projection if capability_row else {}).get("device") or {})
        evaluation_id = f"hrm-evaluation-{uuid.uuid4()}"
        unsigned = {
            "schema": "ananta.hrm-experiments.evaluation-report.v1",
            "evaluation_id": evaluation_id,
            "scope": dict(run.intent["scope"]),
            "puzzle_type": dataset.puzzle_type,
            "dataset_digest": run.dataset_digest,
            "run_digest": str(run.result["result_digest"]),
            "checkpoint_digest": run.checkpoint_digest,
            "evaluator_version": "hrm-evaluator-v1",
            "runtime_digest": canonical_digest(runtime),
            "hardware_digest": canonical_digest(device or {"kind": "unavailable"}),
            "policy_digest": run.policy_digest,
            "sample_count": len(dataset.records),
            "counts": {
                "exact_correct": exact,
                "domain_wrong": len(dataset.records) - exact,
                "parser_error": 0,
                "timeout": 0,
                "cancelled": 0,
                "runtime_error": 0,
            },
            "statistics": {
                "accuracy": accuracy,
                "runtime_seconds": metrics.get("runtime_seconds"),
                "cost": None,
                "energy": None,
            },
            "reproducibility": {
                "canonical_manifest_digest": canonical_digest(dataset.manifest),
                "repeat_count": 1,
                "tolerance": 0.0,
                "status": "not_run",
            },
        }
        report = {**unsigned, "content_digest": canonical_digest(unsigned)}
        self._contracts.validate("evaluation_report", report)
        request_digest = canonical_digest({"run_id": run_id, "project_id": project_id})
        row = HrmEvaluationReportDB(
            evaluation_id=evaluation_id,
            run_id=run.id,
            tenant_id=run.tenant_id,
            project_id=run.project_id,
            owner_subject=principal.subject,
            idempotency_key_digest=hashlib.sha256(idempotency_key.encode()).hexdigest(),
            request_digest=request_digest,
            content_digest=report["content_digest"],
            report=report,
        )
        try:
            persisted, replayed = self._repository.create_report(row)
        except HrmRepositoryConflict as exc:
            raise HrmApplicationError(exc.reason_code) from exc
        return {
            "report_id": persisted.id,
            "evaluation_id": persisted.evaluation_id,
            "idempotent_replay": replayed,
        }

    def get_report(
        self, principal: HrmPrincipal, *, project_id: str, report_id: str
    ) -> dict[str, Any]:
        row = self._repository.get_report(
            principal.tenant_id, project_id, report_id
        )
        if row is None:
            raise HrmApplicationError("hrm.report_not_found", status_code=404)
        report = dict(row.report)
        self._contracts.validate("evaluation_report", report)
        return report

    def run_status(self, run: HrmRunDB) -> dict[str, Any]:
        result = {
            "schema": "ananta.hrm-experiments.run-status.v1",
            "run_id": run.id,
            "attempt_id": str(run.attempt_id),
            "epoch": max(1, run.epoch),
            "status": run.status,
            "last_sequence": self._last_sequence(run),
            "reason_code": run.reason_code,
        }
        self._contracts.validate("run_status", result)
        return result

    def _last_sequence(self, run: HrmRunDB) -> int:
        return self._repository.last_event_sequence(
            run.tenant_id, run.project_id, run.id
        )

    def _claim_idempotency(
        self,
        principal: HrmPrincipal,
        *,
        operation: str,
        idempotency_key: str,
        request_digest: str,
    ):
        try:
            return self._repository.claim_idempotency(
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                operation=operation,
                key_digest=hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest(),
                request_digest=request_digest,
            )
        except HrmRepositoryConflict as exc:
            raise HrmApplicationError(exc.reason_code) from exc

    def _event(
        self,
        run: HrmRunDB,
        event_type: str,
        *,
        message: str | None = None,
        metrics: list[dict[str, Any]] | None = None,
        reason_code: str | None = None,
    ) -> None:
        self._repository.append_event(
            run,
            {
                "sequence": 1,
                "event_type": event_type,
                "timestamp_epoch_ms": int(self._clock() * 1000),
                "message": message,
                "metrics": list(metrics or []),
                "reason_code": reason_code,
            },
        )

    @staticmethod
    def _require_principal_scope(principal: HrmPrincipal, value: Mapping[str, Any]) -> None:
        scope = value.get("scope")
        if not isinstance(scope, Mapping) or scope.get("tenant_id") != principal.tenant_id:
            raise HrmApplicationError("hrm.scope_mismatch", status_code=403)

    @staticmethod
    def _require_limits_within(requested: Mapping[str, Any], effective: Mapping[str, Any]) -> None:
        for key in (
            "cpu_millis",
            "memory_bytes",
            "pids",
            "wallclock_seconds",
            "scratch_bytes",
            "output_bytes",
            "log_bytes",
            "event_count",
            "retries",
            "vram_bytes",
        ):
            if int(requested[key]) > int(effective[key]):
                raise HrmApplicationError("hrm.requested_limit_exceeds_policy")
        if not set(requested["gpu_device_ids"]).issubset(set(effective["gpu_device_ids"])):
            raise HrmApplicationError("hrm.requested_gpu_forbidden")

    @staticmethod
    def _decode_cursor(cursor: str | None) -> int:
        try:
            return decode_cursor(cursor)
        except HrmCursorError as exc:
            raise HrmApplicationError("hrm.cursor_invalid", status_code=400) from exc

    @staticmethod
    def _require_enabled() -> None:
        from agent.config import settings

        if not settings.hrm_experiments_enabled:
            raise HrmApplicationError("hrm.feature_disabled", status_code=403)


def default_hrm_experiment_application_service() -> HrmExperimentApplicationService:
    return HrmExperimentApplicationService()


__all__ = [
    "AnantaHrmExecutionBindingAdapter",
    "AnantaHrmTaskQueueAdapter",
    "HrmApplicationError",
    "HrmExecutionBinding",
    "HrmExecutionBindingPort",
    "HrmExperimentApplicationService",
    "HrmPrincipal",
    "HrmTaskQueuePort",
    "default_hrm_experiment_application_service",
]
