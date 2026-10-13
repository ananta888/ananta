"""Explicit Hub composition of the real specialist training/evaluation ports."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent.ports.ml_intern_training_repository import MlInternTrainingPrincipal
from agent.repositories.ml_intern_training import MlInternTrainingRepository
from agent.services.ml_intern_adapter_registry_service import MlInternAdapterRegistryService
from agent.services.ml_intern_dataset_catalog_service import MlInternDatasetCatalogService
from agent.services.ml_intern_dataset_repository_bridge_service import MlInternDatasetRepositoryBridgeService
from agent.services.specialist_benchmark_projection import SpecialistBenchmarkProjectionStore
from agent.services.specialist_dataset_admission import HubSpecialistDatasetAdmission
from agent.services.specialist_evaluation_receipt_reader import SpecialistEvaluationReceiptReader
from agent.services.specialist_hub_evaluation import HubSpecialistEvaluation
from agent.services.specialist_hub_trainer import HubSpecialistTrainer
from agent.services.specialist_job_predictions import SpecialistJobPredictionService
from agent.services.specialist_model_factory import SpecialistModelFactory
from agent.services.specialist_partition_predictor import HubSpecialistPartitionPredictor
from agent.services.specialist_prediction_storage import ConfinedSpecialistPredictionStorage
from agent.services.specialist_promotion_gate import SpecialistPromotionPolicy
from agent.services.specialist_runtime_candidate import (
    HubSpecialistCandidateRegistration,
    SpecialistRuntimeCandidateService,
)
from agent.services.specialist_scoped_lifecycle import HubSpecialistLifecycle
from agent.services.specialist_training_outcome import SpecialistTrainingOutcomeService
from agent.services.specialist_training_submission import HubSpecialistJobSubmission, SpecialistTrainingControlPort
from agent.services.specialist_training_task_family import SpecialistTrainingTaskFamilyStrategy
from ananta_contracts.specialist_decision import SpecialistDecisionContract


def build_hub_specialist_factory(
    *, principal: MlInternTrainingPrincipal, contract: SpecialistDecisionContract,
    control: SpecialistTrainingControlPort, repository: MlInternTrainingRepository,
    catalog: MlInternDatasetCatalogService, bridge: MlInternDatasetRepositoryBridgeService,
    registry: MlInternAdapterRegistryService, artifact_root: Path, dataset_root: Path,
    model_catalog: Mapping[str, Mapping[str, Any]], policy: SpecialistPromotionPolicy,
    benchmark_version: str, backend: str, mode: str, timeout_seconds: float = 3600,
) -> SpecialistModelFactory:
    """Wire existing Hub-owned services; productive promotion remains Evidence-gated."""
    storage = ConfinedSpecialistPredictionStorage(artifact_root=artifact_root, dataset_root=dataset_root)
    outcomes = SpecialistTrainingOutcomeService(
        repository=repository, registry=registry, artifact_root=artifact_root,
        model_catalog=model_catalog, open_artifact=storage.open_artifact,
    )
    datasets = HubSpecialistDatasetAdmission(catalog=catalog, bridge=bridge, principal=principal)
    training = HubSpecialistTrainer(
        principal=principal, repository=repository, outcomes=outcomes,
        submission=HubSpecialistJobSubmission(control=control, principal=principal, timeout_seconds=timeout_seconds),
    )
    predictions = SpecialistJobPredictionService(repository=repository, storage=storage)
    evaluation = HubSpecialistEvaluation(
        principal=principal, repository=repository,
        submission=HubSpecialistJobSubmission(
            control=control, principal=principal, job_type="evaluate_lora", timeout_seconds=timeout_seconds,
        ),
        predictions=predictions,
    )
    registration = HubSpecialistCandidateRegistration(
        principal=principal, registry=registry,
        reports=SpecialistBenchmarkProjectionStore(artifact_root=artifact_root),
        candidates=SpecialistRuntimeCandidateService(
            registry=registry, outcomes=outcomes,
            evaluation_receipts=SpecialistEvaluationReceiptReader(repository=repository, predictions=predictions),
        ),
    )
    return SpecialistModelFactory(
        contract, trainer=training,
        partition_predictor=HubSpecialistPartitionPredictor(
            strategy=SpecialistTrainingTaskFamilyStrategy(contract), datasets=datasets, evaluation=evaluation,
        ),
        registry=registry, policy=policy, benchmark_version=benchmark_version, backend=backend, mode=mode,
        dataset_admission=datasets, candidate_registration=registration,
        lifecycle=HubSpecialistLifecycle(principal=principal, registry=registry),
    )
