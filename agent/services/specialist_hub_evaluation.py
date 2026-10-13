"""Hub-owned specialist evaluation submission followed by authorized raw outputs."""

from __future__ import annotations

from typing import Protocol

from agent.db_models import MlInternTrainingJobDB
from agent.ports.ml_intern_training_repository import MlInternTrainingPrincipal
from agent.services.ml_intern_training_contract import CreateTrainingJobCommand
from agent.services.specialist_evaluation_receipt import SpecialistEvaluationReceipt, SpecialistEvaluationResult
from agent.services.specialist_hub_trainer import SpecialistSubmissionPort
from agent.services.specialist_prediction_artifact import SpecialistPredictionOutputs


class SpecialistEvaluationJobPort(Protocol):
    def get_job(self, principal: MlInternTrainingPrincipal, job_id: str) -> MlInternTrainingJobDB | None: ...


class SpecialistEvaluationPredictionsPort(Protocol):
    def read(self, principal: MlInternTrainingPrincipal, job_id: str) -> SpecialistPredictionOutputs: ...


class HubSpecialistEvaluation:
    def __init__(
        self,
        *,
        principal: MlInternTrainingPrincipal,
        repository: SpecialistEvaluationJobPort,
        submission: SpecialistSubmissionPort,
        predictions: SpecialistEvaluationPredictionsPort,
    ):
        self._principal = principal
        self._repository = repository
        self._submission = submission
        self._predictions = predictions

    def evaluate(self, command: CreateTrainingJobCommand) -> SpecialistPredictionOutputs:
        return self.evaluate_result(command).outputs

    def evaluate_result(self, command: CreateTrainingJobCommand) -> SpecialistEvaluationResult:
        spec = command.request_spec
        if (
            command.job_type != "evaluate_lora"
            or spec.get("task_family") != "specialist_decision"
            or spec.get("scorer_name") != "specialist_decision"
            or not spec.get("adapter_id")
            or not spec.get("expected_adapter_sha256")
            or type(spec.get("expected_dataset_version")) is not int
        ):
            raise ValueError("specialist_evaluation_admission_required")
        job_id = self._submission.run(command)
        job = self._repository.get_job(self._principal, job_id)
        if (
            job is None
            or job.tenant_id != self._principal.tenant_id
            or job.owner_subject != self._principal.subject
            or job.status != "completed"
            or job.job_type != command.job_type
            or job.adapter_id != spec["adapter_id"]
            or any(job.request_spec.get(key) != value for key, value in spec.items())
        ):
            raise ValueError("specialist_evaluation_job_binding_invalid")
        outputs = self._predictions.read(self._principal, job_id)
        current = self._repository.get_job(self._principal, job_id)
        if current is None or current.version != job.version or current.status != "completed":
            raise ValueError("specialist_evaluation_job_changed")
        if not current.active_attempt_id or not current.dataset_id:
            raise ValueError("specialist_evaluation_receipt_binding_missing")
        return SpecialistEvaluationResult(outputs, SpecialistEvaluationReceipt(
            job_id=current.id, job_version=current.version, attempt_id=current.active_attempt_id,
            dataset_id=current.dataset_id, dataset_version=spec["expected_dataset_version"],
            adapter_id=current.adapter_id, adapter_sha256=spec["expected_adapter_sha256"],
            base_model_sha256=spec.get("expected_base_model_sha256"), request_digest=current.request_digest,
        ))
