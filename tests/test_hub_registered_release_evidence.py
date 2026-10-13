"""Isolated Hub Registry policy fixtures; never evidence of a real production release."""

from dataclasses import replace

import pytest

from agent.services.hub_registered_release_evidence import HubRegisteredReleaseEvidence, ReleaseExecutionBinding
from tests.hub.test_hub_evidence_registry_service import _run, _source
from tests.hub.test_hub_evidence_registry_service import service as _service


def _completed(scope="production", synthetic=False):
    registry = _service.__wrapped__()
    source = _source(registry, scope=scope, synthetic=synthetic)
    run = _run(registry, source_id=source.source_id, scope=scope, synthetic=synthetic)
    run = registry.record_result(
        tenant_id=run.tenant_id, project_id=run.project_id, run_id=run.run_id,
        assignment_id=run.assignment_id, dispatch_lease_id=run.dispatch_lease_id,
        terminal_state="succeeded", result_digest="f" * 64,
    )
    return registry, source, run


def test_release_adapter_uses_current_registry_for_fully_pinned_completed_run():
    registry, source, run = _completed()
    adapter = HubRegisteredReleaseEvidence(registry=registry, runs=[run])
    resolved = adapter.resolve(source_ids=[source.source_id], run_ids=[run.run_id])
    assert resolved.source_ids == (source.source_id,) and resolved.run_ids == (run.run_id,)


@pytest.mark.parametrize("changed_field", [None, "task_id", "assignment_id", "dispatch_lease_id",
                                           "input_digest", "result_digest"])
def test_release_pins_must_cover_exact_current_execution_bindings(changed_field):
    registry, _source_identity, run = _completed()
    evidence = HubRegisteredReleaseEvidence(registry=registry, runs=[run])
    binding = ReleaseExecutionBinding(
        run.task_id, run.assignment_id, run.dispatch_lease_id, run.input_digest, run.result_digest,
    )
    if changed_field is None:
        evidence.require_execution_bindings([binding])
    else:
        with pytest.raises(ValueError, match="current Hub executions"):
            evidence.require_execution_bindings([replace(binding, **{changed_field: "unrelated"})])


@pytest.mark.parametrize("scope,synthetic", [("test", True), ("test", False), ("local", False)])
def test_technical_evidence_cannot_satisfy_production_release(scope, synthetic):
    registry, source, run = _completed(scope, synthetic)
    adapter = HubRegisteredReleaseEvidence(registry=registry, runs=[run])
    with pytest.raises(ValueError):
        adapter.resolve(source_ids=[source.source_id], run_ids=[run.run_id])


@pytest.mark.parametrize("mutation", ["result", "assignment", "project"])
def test_changed_pin_cannot_replace_authoritative_binding(mutation):
    registry, source, run = _completed()
    changes = {"result": {"result_digest": "e" * 64}, "assignment": {"assignment_id": "other"},
               "project": {"project_id": "other"}}
    adapter = HubRegisteredReleaseEvidence(registry=registry, runs=[replace(run, **changes[mutation])])
    with pytest.raises(ValueError):
        adapter.resolve(source_ids=[source.source_id], run_ids=[run.run_id])


def test_foreign_tenant_scope_cannot_use_valid_release_pins():
    registry, _source_identity, run = _completed()
    adapter = HubRegisteredReleaseEvidence(registry=registry, runs=[run])
    with pytest.raises(ValueError, match="another tenant"):
        adapter.require_scope(tenant_id="foreign")


def test_promotion_facade_uses_injected_hub_scope_gate_before_registry_mutation(tmp_path):
    from agent.ports.ml_intern_training_repository import MlInternTrainingPrincipal
    from agent.services.ml_intern_evaluation_promotion_facade import MlInternEvaluationPromotionFacade
    from tests.test_unsloth_evaluation_promotion_chain import (
        RUN_ID,
        SOURCE_ID,
        _EvaluationStore,
        _evidence,
        _registry,
    )

    hub, _source_identity, run = _completed()
    registry, evaluated = _registry(tmp_path / "registry.json")
    facade = MlInternEvaluationPromotionFacade(
        evaluations=_EvaluationStore(_evidence()), registry=registry,
        trusted_source_ids=(SOURCE_ID,), trusted_run_ids=(RUN_ID,), audit_sink=lambda *_args: None,
        evidence_registry=HubRegisteredReleaseEvidence(registry=hub, runs=[run]),
    )
    with pytest.raises(ValueError, match="another tenant"):
        facade.promote(
            MlInternTrainingPrincipal("tenant-a", "admin-a"), evaluated,
            expected_revision=evaluated.registry_version, idempotency_key="hub-scope-refusal",
            approved_by="automated-test-policy", reason="synthetic test", minimum_score=0.8,
        )
    current = registry.get("adapter-a", tenant_id="tenant-a", owner_subject="admin-a")
    assert current.status == "evaluated" and current.registry_version == evaluated.registry_version


def test_promotion_rechecks_evidence_after_plan_before_registry_write(tmp_path):
    from agent.ports.ml_intern_training_repository import MlInternTrainingPrincipal
    from agent.services.ml_intern_evaluation_promotion_facade import MlInternEvaluationPromotionFacade
    from agent.services.unsloth_evidence import EvidenceVerificationError, ProvidedEvidenceRegistry
    from tests.test_unsloth_evaluation_promotion_chain import (
        RUN_ID,
        SOURCE_ID,
        _EvaluationStore,
        _evidence,
        _registry,
    )

    class RevokedAfterPlan:
        calls = 0

        def require_scope(self, *, tenant_id):
            assert tenant_id == "tenant-a"

        def resolve(self, *, source_ids, run_ids):
            self.calls += 1
            if self.calls > 1:
                raise EvidenceVerificationError("run_revoked", "run revoked after plan")
            return ProvidedEvidenceRegistry(source_ids=(SOURCE_ID,), run_ids=(RUN_ID,)).resolve(
                source_ids=source_ids, run_ids=run_ids
            )

    registry, evaluated = _registry(tmp_path / "registry.json")
    guard = RevokedAfterPlan()
    facade = MlInternEvaluationPromotionFacade(
        evaluations=_EvaluationStore(_evidence()), registry=registry,
        trusted_source_ids=(SOURCE_ID,), trusted_run_ids=(RUN_ID,), audit_sink=lambda *_args: None,
        evidence_registry=guard,
    )
    with pytest.raises(ValueError, match="run revoked after plan"):
        facade.promote(
            MlInternTrainingPrincipal("tenant-a", "admin-a"), evaluated,
            expected_revision=evaluated.registry_version, idempotency_key="evidence-revoked-after-plan",
            approved_by="automated-test-policy", reason="synthetic test", minimum_score=0.8,
        )
    assert guard.calls == 2
    assert registry.get("adapter-a", tenant_id="tenant-a", owner_subject="admin-a").status == "evaluated"
