"""Revalidate technical evaluation references before consuming promotion inputs."""

from typing import Protocol

from agent.db_models import MlInternDatasetDB
from agent.ports.ml_intern_training_repository import MlInternTrainingPrincipal
from agent.services.specialist_evaluation_dataset_purpose import evaluation_dataset_purpose
from agent.services.specialist_evaluation_receipt import SpecialistEvaluationReceipt
from agent.services.specialist_hub_evaluation import SpecialistEvaluationJobPort, SpecialistEvaluationPredictionsPort
from agent.services.specialist_prediction_artifact import SpecialistPredictionOutputs


class SpecialistReceiptRepositoryPort(SpecialistEvaluationJobPort, Protocol):
    def get_dataset(self, principal: MlInternTrainingPrincipal, dataset_id: str) -> MlInternDatasetDB | None: ...


class SpecialistEvaluationReceiptReader:
    def __init__(
        self, *, repository: SpecialistReceiptRepositoryPort, predictions: SpecialistEvaluationPredictionsPort,
    ):
        self._repository = repository
        self._predictions = predictions

    def read(
        self, principal: MlInternTrainingPrincipal, receipt: SpecialistEvaluationReceipt,
        *, partition: str | None = None, manifest_digest: str | None = None, holdout_digest: str | None = None,
    ) -> SpecialistPredictionOutputs:
        self._require_current(principal, receipt)
        self._require_purpose(principal, receipt, partition, manifest_digest, holdout_digest)
        # Existing consumer verifies current attempt, dataset pins and actual prediction bytes.
        outputs = self._predictions.read(principal, receipt.job_id)
        self._require_current(principal, receipt)
        self._require_purpose(principal, receipt, partition, manifest_digest, holdout_digest)
        return outputs

    def _require_purpose(self, principal, receipt, partition, manifest_digest, holdout_digest):
        if partition is None and manifest_digest is None and holdout_digest is None:
            return
        expected = evaluation_dataset_purpose({
            "execution_purpose": "specialist_evaluation", "evaluation_partition": partition,
            "specialist_manifest_digest": manifest_digest, "holdout_digest": holdout_digest,
        })
        dataset = self._repository.get_dataset(principal, receipt.dataset_id)
        if (
            dataset is None or dataset.tenant_id != principal.tenant_id or dataset.owner_subject != principal.subject
            or dataset.version != receipt.dataset_version
            or evaluation_dataset_purpose(dataset.split_manifest or {}) != expected
        ):
            raise ValueError("specialist_evaluation_receipt_partition_mismatch")

    def _require_current(self, principal: MlInternTrainingPrincipal, receipt: SpecialistEvaluationReceipt) -> None:
        job = self._repository.get_job(principal, receipt.job_id)
        if (
            job is None
            or type(receipt.job_version) is not int
            or type(receipt.dataset_version) is not int
            or job.tenant_id != principal.tenant_id
            or job.owner_subject != principal.subject
            or job.status != "completed"
            or job.job_type != "evaluate_lora"
            or job.version != receipt.job_version
            or job.active_attempt_id != receipt.attempt_id
            or job.dataset_id != receipt.dataset_id
            or job.adapter_id != receipt.adapter_id
            or job.request_digest != receipt.request_digest
            or job.request_spec.get("expected_dataset_version") != receipt.dataset_version
            or job.request_spec.get("expected_adapter_sha256") != receipt.adapter_sha256
            or job.request_spec.get("expected_base_model_sha256") != receipt.base_model_sha256
        ):
            raise ValueError("specialist_evaluation_receipt_stale_or_mismatched")
