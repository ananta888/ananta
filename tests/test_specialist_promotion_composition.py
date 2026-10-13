"""Native promotion with real Registry and synthetic headless policy fixtures."""

from dataclasses import replace

import pytest

from agent.services.hub_registered_release_evidence import HubRegisteredReleaseEvidence
from agent.services.ml_intern_evaluation_promotion_facade import MlInternEvaluationPromotionFacade
from agent.services.specialist_benchmark_projection import SpecialistBenchmarkProjectionStore
from agent.services.specialist_evaluation_receipt import SpecialistEvaluationReceipt
from agent.services.specialist_prediction_artifact import SpecialistPredictionOutputs
from agent.services.specialist_promotion_composition import build_hub_specialist_promotion
from agent.services.specialist_promotion_evaluation import SpecialistPromotionEvaluationReadModel
from agent.services.specialist_promotion_gate import PromotionDecision
from agent.services.specialist_registered_benchmark import (
    SpecialistBenchmarkPromotionGuard,
    SpecialistRegisteredBenchmarkReader,
)
from tests.hub.test_hub_evidence_registry_service import _run, _source
from tests.hub.test_hub_evidence_registry_service import service as _evidence_service
from tests.test_specialist_runtime_candidate import candidate as _candidate
from tests.test_unsloth_evaluation_promotion_chain import (
    RUN_ID,
    SOURCE_ID,
    _EvaluationStore,
    _evidence,
    _registry,
)


class ReceiptPolicyFixture:
    """Deterministic receipt seam only; not a claim of productive execution."""

    def __init__(self):
        self.roles = []
        self.revoked = False

    def read(self, principal, receipt, *, partition, manifest_digest, holdout_digest):
        if self.revoked:
            raise ValueError("synthetic_receipt_revoked")
        assert manifest_digest == "c" * 64 and len(holdout_digest) == 64
        self.roles.append(partition)
        return SpecialistPredictionOutputs(base=("allow",), adapter=("allow",))


class SyntheticExecutionPolicyFixture:
    """Exercise read-model composition only, never authorize productive promotion."""

    def require(self, principal, report, evidence):
        assert evidence["job_id"] == report["evaluations"]["holdout"]["job_id"]


class RoleEvidenceFixture(_EvaluationStore):
    """Separate admitted role records; fixture identifiers are never release proof."""

    def __init__(self, evidence):
        super().__init__(evidence)
        self.validation = dict(evidence, job_id="synthetic-validation", attempt_id="synthetic-validation-attempt")

    def get_promotion_evidence(self, principal, evaluation_id):
        if evaluation_id == "synthetic-validation":
            return dict(self.validation)
        return super().get_promotion_evidence(principal, evaluation_id)


@pytest.fixture
def prepared(app, tmp_path, monkeypatch):
    service, registry, principal, winner, _adapter = _candidate.__wrapped__(app, tmp_path, monkeypatch)
    # Prepare actual trained bytes first, then persist synthetic evaluation references.
    record = service.prepare(principal, winner)
    receipts = tuple((role, SpecialistEvaluationReceipt(
        job_id=f"synthetic-{role}", job_version=2, attempt_id=f"synthetic-{role}-attempt",
        dataset_id=f"synthetic-{role}-dataset", dataset_version=1,
        adapter_id=winner.outcome.adapter_id, adapter_sha256=winner.outcome.adapter_digest,
        base_model_sha256=winner.outcome.base_model_digest, request_digest="d" * 64,
    )) for role in ("validation", "holdout"))
    winner = replace(winner, evaluations=receipts, split_manifest_digest="c" * 64)
    decision = PromotionDecision(True, (), "quality", record.eval_report_ref, None, "a" * 64, None)
    store = SpecialistBenchmarkProjectionStore(artifact_root=tmp_path)
    store.bind(principal, record, store.save(principal, winner, decision))
    reader = SpecialistRegisteredBenchmarkReader(registry=registry, reports=store)
    evidence = _evidence()
    evidence.update(
        job_id="synthetic-holdout", attempt_id="synthetic-holdout-attempt",
        adapter_id=winner.outcome.adapter_id, artifact_sha256=record.artifact_sha256,
        base_model_id=record.base_model, base_model_sha256=winner.outcome.base_model_digest,
    )
    policy = ReceiptPolicyFixture()
    legacy = RoleEvidenceFixture(evidence)
    model = SpecialistPromotionEvaluationReadModel(
        record=record, decision=decision, benchmarks=reader, receipts=policy, evaluation_evidence=legacy,
        execution_bindings=SyntheticExecutionPolicyFixture(),
    )
    return registry, principal, record, winner, decision, store, reader, policy, legacy, model


def test_native_read_model_preserves_macro_f1_and_consumes_both_roles(prepared):
    _registry_service, principal, record, winner, _decision, _store, _reader, policy, evidence, model = prepared
    report = model.get(principal, record.eval_report_ref)
    assert report["score_kind"] == "specialist_macro_f1"
    assert report["aggregate_score"] == winner.result.classification["macro_f1"]
    assert model.get_promotion_evidence(principal, record.eval_report_ref) == evidence._evidence
    assert policy.roles == ["validation", "holdout"]


def test_validation_run_references_are_retained_without_mutating_admitted_records(prepared):
    *_, evidence, model = prepared
    principal, record = prepared[1:3]
    hub = _evidence_service.__wrapped__()
    source = _source(hub, scope="test", synthetic=True, tenant_id=principal.tenant_id)
    run = _run(hub, source_id=source.source_id, scope="test", synthetic=True, tenant_id=principal.tenant_id)
    evidence.validation.update(source_ids=[source.source_id], run_ids=[run.run_id])
    result = model.get_promotion_evidence(principal, record.eval_report_ref)
    assert result["source_ids"] == sorted([SOURCE_ID, source.source_id])
    assert result["run_ids"] == sorted([RUN_ID, run.run_id])
    assert evidence._evidence["run_ids"] == [RUN_ID]
    assert evidence.validation["run_ids"] == [run.run_id]


@pytest.mark.parametrize("field", ["job_id", "attempt_id", "artifact_sha256", "base_model_sha256"])
def test_validation_evidence_must_match_its_own_receipt(prepared, field):
    *_, evidence, model = prepared
    principal, record = prepared[1:3]
    evidence.validation[field] = "wrong-binding"
    with pytest.raises(ValueError, match="execution_evidence_mismatch"):
        model.get_promotion_evidence(principal, record.eval_report_ref)


@pytest.mark.parametrize("role", ["validation", "holdout"])
@pytest.mark.parametrize("field", ["source_ids", "run_ids"])
def test_each_role_requires_admitted_references(prepared, role, field):
    *_, evidence, model = prepared
    principal, record = prepared[1:3]
    target = evidence.validation if role == "validation" else evidence._evidence
    target.pop(field)
    with pytest.raises(ValueError, match="references_required"):
        model.get_promotion_evidence(principal, record.eval_report_ref)


@pytest.mark.parametrize("field", ["source_ids", "run_ids"])
@pytest.mark.parametrize("value", ["caller-string", ["unknown"], [None]])
def test_malformed_validation_references_are_refused(prepared, field, value):
    *_, evidence, model = prepared
    principal, record = prepared[1:3]
    evidence.validation[field] = value
    with pytest.raises(ValueError):
        model.get_promotion_evidence(principal, record.eval_report_ref)


@pytest.mark.parametrize("mutation", ["adapter", "attempt", "base", "revocation"])
def test_changed_admitted_evidence_or_receipt_is_refused(prepared, mutation):
    _registry_service, principal, record, _winner, _decision, _store, _reader, policy, evidence, model = prepared
    if mutation == "revocation":
        policy.revoked = True
    else:
        key = {"adapter": "artifact_sha256", "attempt": "attempt_id", "base": "base_model_sha256"}[mutation]
        evidence._evidence[key] = "e" * 64
    with pytest.raises(ValueError, match="revoked|execution_evidence_mismatch"):
        model.get_promotion_evidence(principal, record.eval_report_ref)
    assert record.status == "evaluated" and record.approved_by is None


@pytest.mark.parametrize("mutation", ["metrics", "score", "holdout"])
def test_report_content_cannot_claim_an_unchanged_benchmark_digest(prepared, mutation):
    registry, principal, record, _winner, decision, store, _reader, *_ = prepared

    class ChangedReport:
        def get_for_record(self, principal, record):
            payload = store.get_for_record(principal, record)
            if mutation == "metrics":
                payload["benchmark"]["classification"]["accuracy"] = 0.123
            elif mutation == "score":
                payload["aggregate_score"] = 0.123
            else:
                payload["lineage"]["holdout_digest"] = "e" * 64
            return payload

    reader = SpecialistRegisteredBenchmarkReader(registry=registry, reports=ChangedReport())
    with pytest.raises(ValueError, match="report_invalid"):
        reader.read(principal, record.adapter_id, decision)


def test_real_hub_test_evidence_never_promotes_native_candidate(prepared, tmp_path):
    registry, principal, record, _winner, decision, _store, _reader, policy, evaluation, _model = prepared
    hub = _evidence_service.__wrapped__()
    source = _source(hub, scope="test", synthetic=True, tenant_id=principal.tenant_id)
    run = _run(hub, source_id=source.source_id, scope="test", synthetic=True, tenant_id=principal.tenant_id)
    run = hub.record_result(
        tenant_id=run.tenant_id, project_id=run.project_id, run_id=run.run_id,
        assignment_id=run.assignment_id, dispatch_lease_id=run.dispatch_lease_id,
        terminal_state="succeeded", result_digest="f" * 64,
    )
    evaluation._evidence.update(source_ids=[source.source_id], run_ids=[run.run_id])
    evaluation.validation.update(source_ids=[source.source_id], run_ids=[run.run_id])

    class StorageMustNotMutate:
        def bind_reference(self, **kwargs):
            pytest.fail("Synthetic evidence must stop promotion before Storage mutation")

    facade = build_hub_specialist_promotion(
        registry=registry, artifact_root=tmp_path, record=record, decision=decision,
        receipts=policy, evaluation_evidence=evaluation,
        release_evidence=HubRegisteredReleaseEvidence(registry=hub, runs=[run]),
        audit_sink=lambda *_args: pytest.fail("No promotion audit without promotion"),
        storage_references=StorageMustNotMutate(),
        repository=None,  # Synthetic Hub evidence is refused before any execution lookup.
        outcomes=None,
    )
    with pytest.raises(ValueError):
        facade.promote(
            principal, record, expected_revision=record.registry_version,
            idempotency_key="synthetic-specialist-promotion-denial", approved_by="synthetic-policy",
            reason="headless synthetic policy test", minimum_score=0,
        )
    current = registry.get(record.adapter_id, tenant_id=principal.tenant_id, owner_subject=principal.subject)
    assert current == record and not current.promotion_history
    assert policy.roles == ["validation", "holdout"]


def test_native_guard_rejects_other_policy_and_stale_caller_record(prepared):
    _registry_service, principal, record, _winner, decision, _store, reader, *_ = prepared
    changed = SpecialistBenchmarkPromotionGuard(reader=reader, decision=replace(decision, policy_digest="b" * 64))
    with pytest.raises(ValueError, match="decision_mismatch"):
        changed.require(principal, record, expected_revision=record.registry_version)
    correct = SpecialistBenchmarkPromotionGuard(reader=reader, decision=decision)
    stale = replace(record, registry_version=record.registry_version + 1)
    with pytest.raises(ValueError, match="registry_changed"):
        correct.require(principal, stale, expected_revision=stale.registry_version)


@pytest.mark.parametrize("history_bound", [True, False])
def test_native_reader_allows_only_exact_atomic_promotion_replay(prepared, history_bound):
    registry, principal, record, _winner, decision, store, _reader, *_ = prepared
    if history_bound:
        promoted, replayed = registry.promote_evaluated(
            record.adapter_id, artifact_sha256=record.artifact_sha256, evaluation_id=record.eval_report_ref,
            evidence={key: value for key, value in {**_evidence(), "metrics": {"aggregate_score": record.eval_score}}
                      .items() if key not in {"artifact_sha256", "validation_dataset_hash"}},
            tenant_id=principal.tenant_id, owner_subject=principal.subject,
            expected_version=record.registry_version, approved_by="synthetic-policy",
            reason="synthetic replay fixture", idempotency_key="synthetic-native-replay-fixture",
        )
        assert promoted.status == "approved" and not replayed
    else:
        registry.approve(
            record.adapter_id, approved_by="synthetic-policy", reason="synthetic legacy approval",
            tenant_id=principal.tenant_id, owner_subject=principal.subject, expected_version=record.registry_version,
        )
    reader = SpecialistRegisteredBenchmarkReader(registry=registry, reports=store, allow_promotion_replay=True)
    guard = SpecialistBenchmarkPromotionGuard(reader=reader, decision=decision)
    if history_bound:
        guard.require(principal, record, expected_revision=record.registry_version)
    else:
        with pytest.raises(ValueError, match="unavailable"):
            guard.require(principal, record, expected_revision=record.registry_version)


def test_promotion_rechecks_guard_before_storage_mutation(tmp_path):
    registry, record = _registry(tmp_path / "registry.json")

    class RevokedGuard:
        calls = 0

        def require(self, principal, observed, *, expected_revision):
            self.calls += 1
            if self.calls > 1:
                raise ValueError("quality_policy_revoked")

    class StorageMustNotMutate:
        def bind_reference(self, **kwargs):
            pytest.fail("Revoked quality must stop before Storage mutation")

    from agent.ports.ml_intern_training_repository import MlInternTrainingPrincipal

    guard = RevokedGuard()
    facade = MlInternEvaluationPromotionFacade(
        evaluations=_EvaluationStore(_evidence()), registry=registry,
        trusted_source_ids=(SOURCE_ID,), trusted_run_ids=(RUN_ID,), audit_sink=lambda *_args: None,
        promotion_guard=guard, storage_references=StorageMustNotMutate(),
    )
    with pytest.raises(ValueError, match="quality_policy_revoked"):
        facade.promote(
            MlInternTrainingPrincipal("tenant-a", "admin-a"), record,
            expected_revision=record.registry_version, idempotency_key="revoked-specialist-quality",
            approved_by="synthetic-policy", reason="synthetic quality revocation", minimum_score=0.8,
        )
    assert guard.calls == 2
    assert registry.get(record.adapter_id, tenant_id="tenant-a", owner_subject="admin-a") == record
