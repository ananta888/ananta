"""Factory partition predictor through existing evaluation-only Hub jobs."""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from agent.services.ml_intern_training_contract import CreateTrainingJobCommand
from agent.services.specialist_dataset_split import SplitManifest
from agent.services.specialist_evaluation_receipt import SpecialistEvaluationResult, SpecialistPartitionOutputs
from agent.services.specialist_model_factory import TrainingOutcome
from agent.services.specialist_training_examples import TrainingExample
from agent.services.specialist_training_task_family import SpecialistTrainingTaskFamilyStrategy
from ananta_contracts.specialist_dataset_admission import AdmittedSpecialistDataset


class SpecialistEvaluationDatasetPort(Protocol):
    def admit_evaluation(
        self, manifest: SplitManifest, train: Sequence[Mapping[str, Any]],
        evaluation: Sequence[Mapping[str, Any]], *, partition: str,
    ) -> AdmittedSpecialistDataset: ...


class SpecialistEvaluationPort(Protocol):
    def evaluate_result(self, command: CreateTrainingJobCommand) -> SpecialistEvaluationResult: ...


class HubSpecialistPartitionPredictor:
    def __init__(
        self, *, strategy: SpecialistTrainingTaskFamilyStrategy,
        datasets: SpecialistEvaluationDatasetPort, evaluation: SpecialistEvaluationPort,
    ):
        self._strategy = strategy
        self._datasets = datasets
        self._evaluation = evaluation

    def predict_partition(
        self, training: CreateTrainingJobCommand, outcome: TrainingOutcome, manifest: SplitManifest,
        train_records: Sequence[Mapping[str, Any]], examples: Sequence[TrainingExample], *, partition: str,
    ) -> Sequence[str]:
        if (
            not outcome.adapter_id
            or training.job_type != "train_lora"
            or training.request_spec.get("output_schema_digest") != self._strategy.schema_digest
            or (training.request_spec.get("specialist") or {}).get("dataset_digest") != manifest.dataset_digest
            or (training.request_spec.get("specialist") or {}).get("holdout_digest") != manifest.holdout.holdout_digest
        ):
            raise ValueError("specialist_partition_training_binding_invalid")
        admitted = self._datasets.admit_evaluation(
            manifest, train_records, self._strategy.training_records(examples), partition=partition
        )
        spec = dict(training.request_spec)
        spec.update(
            job_type="evaluate_lora", dataset_id=admitted.dataset_id,
            expected_dataset_version=admitted.version, adapter_id=outcome.adapter_id,
            expected_adapter_sha256=outcome.adapter_digest,
            expected_base_model_sha256=outcome.base_model_digest, scorer_name="specialist_decision",
        )
        result = self._evaluation.evaluate_result(CreateTrainingJobCommand.from_mapping(spec))
        outputs = result.outputs
        if len(outputs.adapter) != len(examples) or len(outputs.base) != len(examples):
            raise ValueError("specialist_predictor_output_count_mismatch")
        if (
            result.receipt.dataset_id != admitted.dataset_id
            or result.receipt.dataset_version != admitted.version
            or result.receipt.adapter_id != outcome.adapter_id
            or result.receipt.adapter_sha256 != outcome.adapter_digest
            or result.receipt.base_model_sha256 != outcome.base_model_digest
        ):
            raise ValueError("specialist_partition_evaluation_receipt_mismatch")
        return SpecialistPartitionOutputs(outputs.adapter, result.receipt)
