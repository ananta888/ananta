"""Specialist adapter promotion uses the established Hub registry/Evidence gate."""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from agent.services.hub_registered_release_evidence import HubRegisteredReleaseEvidence
from agent.services.ml_intern_adapter_registry_contract import AdapterRecord
from agent.services.ml_intern_adapter_registry_service import MlInternAdapterRegistryService
from agent.services.ml_intern_evaluation_promotion_facade import (
    EvaluationPromotionReadPort,
    MlInternEvaluationPromotionFacade,
)
from agent.services.specialist_benchmark_projection import SpecialistBenchmarkProjectionStore
from agent.services.specialist_hub_trainer import SpecialistOutcomePort
from agent.services.specialist_promotion_evaluation import (
    SpecialistPromotionEvaluationReadModel,
    SpecialistPromotionReceiptPort,
)
from agent.services.specialist_promotion_gate import PromotionDecision
from agent.services.specialist_registered_benchmark import (
    SpecialistBenchmarkPromotionGuard,
    SpecialistRegisteredBenchmarkReader,
)
from agent.services.specialist_release_execution import SpecialistReleaseExecutionBindings, SpecialistReleaseJobPort
from agent.services.unsloth_storage_contracts import StorageReferencePort


def build_hub_specialist_promotion(
    *, registry: MlInternAdapterRegistryService, artifact_root: Path,
    record: AdapterRecord, decision: PromotionDecision, receipts: SpecialistPromotionReceiptPort,
    evaluation_evidence: EvaluationPromotionReadPort, release_evidence: HubRegisteredReleaseEvidence,
    repository: SpecialistReleaseJobPort,
    outcomes: SpecialistOutcomePort,
    audit_sink: Callable[[str, Mapping[str, Any]], None],
    storage_references: StorageReferencePort | None = None,
) -> MlInternEvaluationPromotionFacade:
    """Caller retains existing Hub governance; no whitelist fallback or Worker orchestration."""
    if not isinstance(release_evidence, HubRegisteredReleaseEvidence):
        raise ValueError("specialist_promotion_hub_evidence_required")
    reader = SpecialistRegisteredBenchmarkReader(
        registry=registry, reports=SpecialistBenchmarkProjectionStore(artifact_root=artifact_root),
        allow_promotion_replay=True,
    )
    evaluations = SpecialistPromotionEvaluationReadModel(
        record=record, decision=decision, benchmarks=reader, receipts=receipts,
        evaluation_evidence=evaluation_evidence,
        execution_bindings=SpecialistReleaseExecutionBindings(
            repository=repository, evidence=release_evidence, outcomes=outcomes,
        ),
    )
    return MlInternEvaluationPromotionFacade(
        registry=registry, evaluations=evaluations,
        trusted_source_ids=(), trusted_run_ids=(), evidence_registry=release_evidence,
        audit_sink=audit_sink, storage_references=storage_references,
        promotion_guard=_SpecialistReleaseGuard(
            quality=SpecialistBenchmarkPromotionGuard(reader=reader, decision=decision), evaluations=evaluations,
        ),
    )


class _SpecialistReleaseGuard:
    def __init__(self, *, quality: SpecialistBenchmarkPromotionGuard,
                 evaluations: SpecialistPromotionEvaluationReadModel):
        self._quality = quality
        self._evaluations = evaluations

    def require(self, principal, record, *, expected_revision):
        self._quality.require(principal, record, expected_revision=expected_revision)
        self._evaluations.get_promotion_evidence(principal, str(record.eval_report_ref or ""))
