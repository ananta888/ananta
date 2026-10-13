"""Technical specialist reports with actual registry fixtures; no release Evidence."""

import json
from dataclasses import replace

import pytest

from agent.ports.ml_intern_training_repository import MlInternTrainingPrincipal
from agent.services.specialist_benchmark_projection import SpecialistBenchmarkProjectionStore
from agent.services.specialist_promotion_gate import PromotionDecision
from agent.services.specialist_registered_benchmark import SpecialistRegisteredBenchmarkReader
from agent.services.specialist_runtime_candidate import HubSpecialistCandidateRegistration
from tests.test_specialist_runtime_candidate import candidate as _candidate


@pytest.fixture
def report(app, tmp_path, monkeypatch):
    _service, _registry, principal, winner, _adapter = _candidate.__wrapped__(app, tmp_path, monkeypatch)
    decision = PromotionDecision(
        True, (), "quality", winner.result.to_mapping()["result_digest"], None, "a" * 64, None,
    )
    return SpecialistBenchmarkProjectionStore(artifact_root=tmp_path), principal, winner, decision


def test_specialist_report_preserves_native_metrics_and_stable_scoped_identity(report):
    store, principal, winner, decision = report
    reference = store.save(principal, winner, decision)
    payload = store.get(principal, reference)
    assert payload["benchmark"] == winner.result.to_mapping()
    assert payload["score_kind"] == "specialist_macro_f1"
    assert payload["aggregate_score"] == winner.result.classification["macro_f1"]
    assert payload["production_release_evidence"] is False
    assert store.save(principal, winner, decision) == reference
    with pytest.raises(FileNotFoundError):
        store.get(MlInternTrainingPrincipal("foreign", principal.subject), reference)


def test_other_candidate_decision_cannot_be_persisted_as_benchmark_result(report):
    store, principal, winner, decision = report
    with pytest.raises(ValueError, match="specialist_evaluation_projection_binding_invalid"):
        store.save(principal, winner, replace(decision, candidate_result_digest="f" * 64))


def test_real_registration_persists_report_without_productive_approval(app, tmp_path, monkeypatch):
    service, registry, principal, winner, _adapter = _candidate.__wrapped__(app, tmp_path, monkeypatch)
    store = SpecialistBenchmarkProjectionStore(artifact_root=tmp_path)
    decision = PromotionDecision(True, (), "quality", winner.result.to_mapping()["result_digest"], None, "a" * 64, None)
    registration = HubSpecialistCandidateRegistration(
        principal=principal, candidates=service, registry=registry, reports=store,
    )
    adapter_id, status = registration.register(winner, decision)
    assert status == "evaluated"
    reference = store.save(principal, winner, decision)
    assert store.get(principal, reference)["adapter_id"] == winner.outcome.adapter_id
    record = registry.get(adapter_id, tenant_id=principal.tenant_id, owner_subject=principal.subject)
    assert record.approved_by is None
    assert store.get_for_record(principal, record) == store.get(principal, reference)
    reader = SpecialistRegisteredBenchmarkReader(registry=registry, reports=store)
    assert reader.read(principal, adapter_id, decision) == store.get(principal, reference)
    with pytest.raises(ValueError, match="decision_mismatch"):
        reader.read(principal, adapter_id, replace(decision, policy_digest="b" * 64))
    with pytest.raises(ValueError, match="unavailable"):
        reader.read(MlInternTrainingPrincipal("foreign", principal.subject), adapter_id, decision)
    store.bind(principal, record, reference)
    with pytest.raises(ValueError, match="specialist_benchmark_registry_binding_invalid"):
        store.get_for_record(principal, replace(record, artifact_sha256="e" * 64))

    class ChangingRegistry:
        reads = 0

        def get(self, *args, **kwargs):
            self.reads += 1
            return record if self.reads == 1 else replace(record, registry_version=record.registry_version + 1)

    changed = SpecialistRegisteredBenchmarkReader(registry=ChangingRegistry(), reports=store)
    with pytest.raises(ValueError, match="registry_changed"):
        changed.read(principal, adapter_id, decision)


def test_mutated_persisted_report_cannot_be_reused(report, tmp_path):
    store, principal, winner, decision = report
    reference = store.save(principal, winner, decision)
    payload = store.get(principal, reference)
    payload["aggregate_score"] = 0.25
    (tmp_path / store._reference(principal, reference)).write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="specialist_benchmark_projection_changed"):
        store.get(principal, reference)
    with pytest.raises(ValueError, match="specialist_benchmark_projection_changed"):
        store.save(principal, winner, decision)
