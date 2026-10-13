"""Versioned runtime candidates preserve real registry binding; all model bytes synthetic."""

from dataclasses import replace

import pytest

from agent.services.ml_intern_adapter_registry_service import MlInternAdapterRegistryService
from agent.services.specialist_benchmark import Prediction, benchmark
from agent.services.specialist_candidates import ArtifactLineage
from agent.services.specialist_evaluation_receipt import SpecialistEvaluationReceipt
from agent.services.specialist_model_factory import CandidateOutcome, SpecialistModelFactory
from agent.services.specialist_promotion_gate import PromotionDecision
from agent.services.specialist_runtime_candidate import (
    HubSpecialistCandidateRegistration,
    SpecialistRuntimeCandidateService,
)
from tests.test_specialist_model_factory import GATE
from tests.test_specialist_model_factory_e2e import POLICY
from tests.test_specialist_training_outcome import completed as _completed


@pytest.fixture
def candidate(app, tmp_path, monkeypatch):
    reader, principal, job, command, _manifest, adapter, _repo = _completed.__wrapped__(app, tmp_path, monkeypatch)
    outcome = reader.read(principal, job.id, command)
    metadata = command.request_spec["specialist"]
    result = benchmark(
        GATE, [Prediction("case-one", "allow", "allow")], benchmark_version=metadata["benchmark_version"],
        holdout_digest=metadata["holdout_digest"], candidate_id="candidate-one", base_model=job.base_model,
        method=command.request_spec["method"], usage=outcome.usage,
    )
    lineage = ArtifactLineage(
        specialist_id=metadata["specialist_id"], contract_version=metadata["contract_version"], version="v1",
        base_model=job.base_model, base_model_digest=outcome.base_model_digest,
        adapter_digest=outcome.adapter_digest, dataset_digest=metadata["dataset_digest"],
        holdout_digest=metadata["holdout_digest"], training_job_id=job.id, method=command.request_spec["method"],
    ).with_evaluation(result)
    winner = CandidateOutcome("candidate-one", command, outcome, result, lineage)
    registry = MlInternAdapterRegistryService(tmp_path / "registry.json")
    return SpecialistRuntimeCandidateService(registry=registry, outcomes=reader), registry, principal, winner, adapter


def test_actual_adapter_paths_and_unverified_provenance_are_preserved_without_approval(candidate):
    service, registry, principal, winner, _adapter = candidate
    original = registry.get(winner.outcome.adapter_id, tenant_id=principal.tenant_id, owner_subject=principal.subject)
    record = service.prepare(principal, winner)
    assert record.status == "evaluated" and record.approved_by is None
    assert record.artifact_paths == original.artifact_paths
    assert record.dataset_hash == original.dataset_hash and record.config_hash == original.config_hash
    assert record.source_ids == original.source_ids and record.run_ids == original.run_ids
    assert record.provenance_verified is False
    assert record.tenant_id == principal.tenant_id and record.owner_subject == principal.subject
    assert service.prepare(principal, winner).registry_version == record.registry_version
    assert registry.get(record.adapter_id) is None


@pytest.mark.parametrize("mutation", ["artifact", "lineage", "outcome"])
def test_changed_training_or_evaluation_cannot_create_runtime_alias(candidate, mutation):
    service, registry, principal, winner, adapter = candidate
    if mutation == "artifact":
        (adapter / "adapter_config.json").write_text('{"changed":true}')
    elif mutation == "lineage":
        winner = replace(winner, lineage=replace(winner.lineage, holdout_digest="c" * 64))
    else:
        winner = replace(winner, outcome=replace(winner.outcome, adapter_digest="d" * 64))
    with pytest.raises(ValueError):
        service.prepare(principal, winner)
    assert registry.get(
        "specialist-code-risk-gate-v1", tenant_id=principal.tenant_id, owner_subject=principal.subject
    ) is None


@pytest.mark.parametrize("allowed", [True, False])
def test_factory_real_registration_preserves_artifacts_and_never_uses_legacy_approval(candidate, allowed):
    service, registry, principal, winner, _adapter = candidate
    registration = HubSpecialistCandidateRegistration(principal=principal, candidates=service, registry=registry)
    factory = SpecialistModelFactory(
        GATE, trainer=None, predictor=None, partition_predictor=object(), registry=registry, policy=POLICY,
        benchmark_version="bench-v1", candidate_registration=registration,
    )
    decision = PromotionDecision(
        allowed, () if allowed else ("quality_rejected",), "quality" if allowed else "none",
        winner.result.to_mapping()["result_digest"], None, "a" * 64, None,
    )
    adapter_id, status = factory._register(winner, decision)
    assert status == ("evaluated" if allowed else "rejected")
    record = registry.get(adapter_id, tenant_id=principal.tenant_id, owner_subject=principal.subject)
    assert record.artifact_paths and record.approved_by is None
    assert registry.get(adapter_id) is None


def test_real_adapter_cannot_fall_back_to_reference_registration(candidate):
    _service, registry, _principal, winner, _adapter = candidate
    factory = SpecialistModelFactory(
        GATE, trainer=None, partition_predictor=object(), registry=registry, policy=POLICY,
        benchmark_version="bench-v1",
    )
    with pytest.raises(ValueError, match="specialist_runtime_candidate_registration_required"):
        factory._register(winner, None)


def test_other_candidate_decision_cannot_register_or_reject(candidate):
    service, registry, principal, winner, _adapter = candidate
    registration = HubSpecialistCandidateRegistration(principal=principal, candidates=service, registry=registry)
    decision = PromotionDecision(False, ("rejected",), "none", "b" * 64, None, "a" * 64, None)
    with pytest.raises(ValueError, match="specialist_runtime_candidate_decision_mismatch"):
        registration.register(winner, decision)
    assert registry.get(
        f"specialist-{winner.lineage.specialist_id}-{winner.lineage.version}",
        tenant_id=principal.tenant_id, owner_subject=principal.subject,
    ) is None


def test_revoked_evaluation_receipt_blocks_runtime_registry_write(candidate):
    service, registry, principal, winner, _adapter = candidate
    receipt = SpecialistEvaluationReceipt(
        "synthetic-eval", 1, "synthetic-attempt", "synthetic-dataset", 1,
        winner.outcome.adapter_id, winner.outcome.adapter_digest, winner.outcome.base_model_digest, "a" * 64,
    )
    winner = replace(winner, evaluations=(("validation", receipt), ("holdout", receipt)))

    class RevokedReceipts:
        def read(self, received_principal, received_receipt, **purpose):
            assert received_principal == principal and received_receipt == receipt
            raise ValueError("receipt_revoked")

    guarded = SpecialistRuntimeCandidateService(
        registry=registry, outcomes=service._outcomes, evaluation_receipts=RevokedReceipts()
    )
    with pytest.raises(ValueError, match="receipt_revoked"):
        guarded.prepare(principal, winner)
    assert registry.get(
        f"specialist-{winner.lineage.specialist_id}-{winner.lineage.version}",
        tenant_id=principal.tenant_id, owner_subject=principal.subject,
    ) is None
