from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from agent.services.ml_intern_adapter_registry_service import (
    MlInternAdapterRegistryService,
    RegistryIdempotencyConflict,
)
from agent.services.ml_intern_evaluation_promotion_facade import (
    MlInternEvaluationPromotionFacade,
    PromotionGateError,
)
from agent.services.ml_intern_training_repository_port import (
    MlInternTrainingPrincipal,
)
from agent.services.unsloth_evaluation_promotion_service import (
    EvaluationSnapshot,
    PromotionRequest,
    UnslothEvaluationPromotionService,
)
from agent.services.unsloth_evidence import ProvidedEvidenceRegistry

SOURCE_ID = "SRC_training-source"
RUN_ID = "RUN_evaluation-run"
HASHES = {name: character * 64 for name, character in zip(
    (
        "artifact",
        "dataset",
        "fence",
        "model",
        "adapter",
        "export",
    ),
    "abcdef",
)}


class _EvaluationStore:
    def __init__(self, evidence):
        self._evidence = evidence

    def get(self, principal, evaluation_id):
        return {
            "id": evaluation_id,
            "passed": True,
            "aggregate_score": 0.9,
        }

    def get_promotion_evidence(self, principal, evaluation_id):
        return dict(self._evidence)


def _evidence():
    return {
        "job_id": "evaluation-a",
        "attempt_id": "attempt-a",
        "fencing_token_digest": HASHES["fence"],
        "dataset_hash": HASHES["dataset"],
        "validation_dataset_hash": "1" * 64,
        "base_model_id": "model-a",
        "base_model_sha256": HASHES["model"],
        "adapter_id": "adapter-a",
        "adapter_sha256": HASHES["adapter"],
        "artifact_sha256": HASHES["artifact"],
        "export_sha256": HASHES["export"],
        "source_ids": [SOURCE_ID],
        "run_ids": [RUN_ID],
    }


def _registry(path: Path):
    registry = MlInternAdapterRegistryService(path)
    registered = registry.register_trained(
        adapter_id="adapter-a",
        display_name="Adapter A",
        version="1",
        base_model="model-a",
        method="lora",
        artifact_paths={"adapter_dir": "unused-in-promotion-test"},
        config_hash="9" * 64,
        artifact_sha256=HASHES["artifact"],
        dataset_hash=HASHES["dataset"],
        source_ids=[SOURCE_ID],
        run_ids=[RUN_ID],
        provenance_verified=True,
        tenant_id="tenant-a",
        owner_subject="admin-a",
    )
    return registry, registry.set_eval_report(
        registered.adapter_id,
        eval_report_ref="evaluation-a",
        eval_score=0.9,
        tenant_id="tenant-a",
        owner_subject="admin-a",
        expected_version=registered.registry_version,
    )


def test_facade_appends_one_immutable_idempotent_promotion(tmp_path: Path):
    registry, evaluated = _registry(tmp_path / "registry.json")
    audit = []
    facade = MlInternEvaluationPromotionFacade(
        evaluations=_EvaluationStore(_evidence()),
        registry=registry,
        trusted_source_ids=(SOURCE_ID,),
        trusted_run_ids=(RUN_ID,),
        audit_sink=lambda event, details: audit.append((event, dict(details))),
    )
    principal = MlInternTrainingPrincipal("tenant-a", "admin-a")

    promoted, replayed = facade.promote(
        principal,
        evaluated,
        expected_revision=evaluated.registry_version,
        idempotency_key="promotion-idempotency-001",
        approved_by="admin-a",
        reason="Promote the fully evaluated adapter",
        minimum_score=0.8,
    )
    replay, replayed_again = facade.promote(
        principal,
        evaluated,
        expected_revision=evaluated.registry_version,
        idempotency_key="promotion-idempotency-001",
        approved_by="admin-a",
        reason="Promote the fully evaluated adapter",
        minimum_score=0.8,
    )

    assert replayed is False
    assert replayed_again is True
    assert promoted.registry_version == replay.registry_version
    assert len(promoted.promotion_history) == 1
    assert promoted.promotion_history[0]["revision_before"] == (
        evaluated.registry_version
    )
    assert promoted.promotion_history[0]["revision_after"] == (
        evaluated.registry_version + 1
    )
    assert "reason" not in promoted.promotion_history[0]
    assert audit[0][0] == "unsloth.artifact_promoted"


def test_promotion_idempotency_and_evidence_fail_closed(tmp_path: Path):
    registry, evaluated = _registry(tmp_path / "registry.json")
    facade = MlInternEvaluationPromotionFacade(
        evaluations=_EvaluationStore(_evidence()),
        registry=registry,
        trusted_source_ids=(SOURCE_ID,),
        trusted_run_ids=(RUN_ID,),
        audit_sink=lambda _event, _details: None,
    )
    principal = MlInternTrainingPrincipal("tenant-a", "admin-a")
    facade.promote(
        principal,
        evaluated,
        expected_revision=evaluated.registry_version,
        idempotency_key="promotion-idempotency-002",
        approved_by="admin-a",
        reason="Promote the fully evaluated adapter",
        minimum_score=0.8,
    )
    with pytest.raises(RegistryIdempotencyConflict):
        facade.promote(
            principal,
            evaluated,
            expected_revision=evaluated.registry_version,
            idempotency_key="promotion-idempotency-002",
            approved_by="admin-a",
            reason="A different approval reason",
            minimum_score=0.8,
        )

    missing = dict(_evidence())
    missing["source_ids"] = []
    blocked = MlInternEvaluationPromotionFacade(
        evaluations=_EvaluationStore(missing),
        registry=registry,
        trusted_source_ids=(SOURCE_ID,),
        trusted_run_ids=(RUN_ID,),
        audit_sink=lambda _event, _details: None,
    )
    with pytest.raises(PromotionGateError) as error:
        blocked.promote(
            principal,
            evaluated,
            expected_revision=evaluated.registry_version,
            idempotency_key="promotion-idempotency-003",
            approved_by="admin-a",
            reason="Promote only with complete evidence",
            minimum_score=0.8,
        )
    assert error.value.code == "source_id_missing"


def test_gate_rejects_changed_attempt_or_export_hash():
    snapshot = EvaluationSnapshot(
        evaluation_id="evaluation-a",
        tenant_id="tenant-a",
        artifact_id="adapter-a",
        artifact_sha256=HASHES["artifact"],
        dataset_hash=HASHES["dataset"],
        state="passed",
        metrics={"aggregate_score": 0.9},
        source_ids=(SOURCE_ID,),
        run_ids=(RUN_ID,),
        job_id="evaluation-a",
        attempt_id="attempt-a",
        fencing_token_digest=HASHES["fence"],
        base_model_id="model-a",
        base_model_sha256=HASHES["model"],
        adapter_id="adapter-a",
        adapter_sha256=HASHES["adapter"],
        export_sha256=HASHES["export"],
    )

    class Catalog:
        def get(self, **_kwargs):
            return snapshot

    class Promotions:
        def promote(self, **_kwargs):
            return 3

    class Audit:
        def record(self, **_kwargs):
            return None

    service = UnslothEvaluationPromotionService(
        evaluations=Catalog(),
        promotions=Promotions(),
        evidence=ProvidedEvidenceRegistry(
            source_ids=(SOURCE_ID,),
            run_ids=(RUN_ID,),
        ),
        audit=Audit(),
    )
    request = PromotionRequest(
        tenant_id="tenant-a",
        artifact_id="adapter-a",
        artifact_sha256=HASHES["artifact"],
        dataset_hash=HASHES["dataset"],
        evaluation_id="evaluation-a",
        minimum_metrics={"aggregate_score": 0.8},
        expected_registry_revision=2,
        job_id="evaluation-a",
        attempt_id="attempt-a",
        fencing_token_digest=HASHES["fence"],
        base_model_id="model-a",
        base_model_sha256=HASHES["model"],
        adapter_id="adapter-a",
        adapter_sha256=HASHES["adapter"],
        export_sha256=HASHES["export"],
    )

    with pytest.raises(PromotionGateError) as attempt_error:
        service.plan(replace(request, attempt_id="attempt-b"))
    assert attempt_error.value.code == "promotion_execution_identity_mismatch"
    with pytest.raises(PromotionGateError) as export_error:
        service.plan(replace(request, export_sha256="0" * 64))
    assert export_error.value.code == "promotion_execution_hash_mismatch"
