"""Bind specialist release runs to current training and both evaluation attempts."""

from typing import Protocol

from agent.db_models import MlInternTrainingAttemptDB, MlInternTrainingJobDB
from agent.ports.ml_intern_training_repository import MlInternTrainingPrincipal
from agent.services.hub_registered_release_evidence import HubRegisteredReleaseEvidence, ReleaseExecutionBinding
from agent.services.ml_intern_training_contract import CreateTrainingJobCommand
from agent.services.specialist_hub_trainer import SpecialistOutcomePort
from ananta_contracts.specialist_decision import canonical_digest


class SpecialistReleaseJobPort(Protocol):
    def get_job(self, principal: MlInternTrainingPrincipal, job_id: str) -> MlInternTrainingJobDB | None: ...
    def get_attempt(self, attempt_id: str) -> MlInternTrainingAttemptDB | None: ...


class SpecialistExecutionBindingPort(Protocol):
    def require(self, principal: MlInternTrainingPrincipal, report: dict, evidence: dict) -> None: ...


class SpecialistReleaseExecutionBindings:
    """Read only; Hub reservation must precede execution, never filled in after it."""

    def __init__(self, *, repository: SpecialistReleaseJobPort, evidence: HubRegisteredReleaseEvidence,
                 outcomes: SpecialistOutcomePort):
        self._repository = repository
        self._evidence = evidence
        self._outcomes = outcomes

    def require(self, principal: MlInternTrainingPrincipal, report: dict, evidence: dict) -> None:
        self._evidence.require_scope(tenant_id=principal.tenant_id)
        self._evidence.resolve(source_ids=evidence.get("source_ids", ()), run_ids=evidence.get("run_ids", ()))
        evaluations = report["evaluations"]
        requested = ((report["training_job_id"], "train_lora", None), *(
            (evaluations[role]["job_id"], "evaluate_lora", evaluations[role])
            for role in ("validation", "holdout")
        ))
        if len({job_id for job_id, _, _ in requested}) != 3:
            raise ValueError("specialist_release_execution_jobs_reused")
        jobs = []
        bindings = []
        for job_id, job_type, receipt in requested:
            job = self._repository.get_job(principal, job_id)
            if (job is None or job.status != "completed" or job.job_type != job_type
                    or job.tenant_id != principal.tenant_id or job.owner_subject != principal.subject
                    or not job.worker_job_id or job.adapter_id != report["adapter_id"]
                    or job.base_model != report["lineage"]["base_model"]):
                raise ValueError("specialist_release_execution_job_invalid")
            attempt = self._repository.get_attempt(str(job.active_attempt_id or ""))
            if (attempt is None or attempt.status != "completed" or attempt.job_id != job.id
                    or attempt.tenant_id != principal.tenant_id or attempt.owner_subject != principal.subject):
                raise ValueError("specialist_release_execution_attempt_invalid")
            if receipt is not None and (
                receipt["job_version"] != job.version or receipt["attempt_id"] != attempt.id
                or receipt["request_digest"] != job.request_digest
            ):
                raise ValueError("specialist_release_execution_receipt_changed")
            if job_type == "train_lora":
                command = CreateTrainingJobCommand(
                    dataset_id=str(job.dataset_id), job_type=job.job_type, mode=job.mode,
                    backend=job.backend, base_model=job.base_model, request_spec=dict(job.request_spec),
                )
                outcome = self._outcomes.read(principal, job.id, command)
                if (outcome.job_id != job.id or outcome.adapter_id != report["adapter_id"]
                        or outcome.adapter_digest != report["artifact_sha256"]
                        or outcome.base_model_digest != report["lineage"]["base_model_digest"]):
                    raise ValueError("specialist_release_execution_training_mismatch")
            if job.id == evidence["job_id"] and attempt.fencing_token_digest != evidence["fencing_token_digest"]:
                raise ValueError("specialist_release_execution_fence_mismatch")
            bindings.append(ReleaseExecutionBinding(
                task_id=job.task_id, assignment_id=job.worker_job_id, dispatch_lease_id=attempt.id,
                input_digest=job.request_digest, result_digest=canonical_digest(job.result_summary),
            ))
            jobs.append((job, attempt))
        self._evidence.require_execution_bindings(bindings)
        for job, attempt in jobs:
            current = self._repository.get_job(principal, job.id)
            current_attempt = self._repository.get_attempt(attempt.id)
            if current != job or current_attempt != attempt:
                raise ValueError("specialist_release_execution_changed")
