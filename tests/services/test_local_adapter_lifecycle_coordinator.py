from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from agent.services.local_adapter_evaluation_service import LocalAdapterEvaluationReport
from agent.services.local_adapter_lifecycle import (
    AdapterGateEvidence,
    LiveAdapterSignals,
    LocalAdapterReleasePolicy,
)
from agent.services.local_adapter_lifecycle_coordinator import (
    LocalAdapterLifecycleCoordinator,
    LocalAdapterLifecycleRepository,
    LocalAdapterReleaseBundle,
)
from agent.services.local_adapter_rollout_service import CanaryEvidence, ShadowEvidence


class _Registry:
    def __init__(self):
        self.promotions = []
        self.rollbacks = []

    def promote(self, **values):
        self.promotions.append(values)
        return {"registry_revision": 7}

    def rollback(self, **values):
        self.rollbacks.append(values)
        return {"registry_revision": 8}


class _Runtime:
    def __init__(self, results=(True,)):
        self.results = list(results)
        self.restarts = []

    def restart(self, **values):
        self.restarts.append(values)
        return self.results.pop(0)


def _bundle():
    return LocalAdapterReleaseBundle(
        candidate_id="candidate-1",
        target="needle2",
        dataset_sha256="a" * 64,
        candidate_sha256="b" * 64,
        evaluation_sha256="c" * 64,
        shadow_sha256="d" * 64,
        canary_sha256="e" * 64,
        policy_sha256=_policy().digest,
    )


def _policy():
    return LocalAdapterReleasePolicy(
        policy_id="local-release-v1",
        target="needle2",
        evaluation_seed=42,
        latency_limit_ms=20,
        memory_limit_bytes=20,
        max_slice_regression=0.0,
        minimum_shadow_examples=10,
        minimum_shadow_match_rate=1.0,
        minimum_canary_examples=10,
        maximum_canary_error_rate=0.0,
        minimum_canary_accuracy=1.0,
        maximum_canary_escalation_rate=0.0,
        canary_latency_limit_ms=20,
        maximum_confidence_brier_score=0.05,
        canary_traffic_basis_points=1000,
        canary_allowed_tools=("lookup",),
        canary_maximum_duration_seconds=86_400,
    )


def _evidence():
    slices = {name: 0.0 for name in ("golden", "ood", "abstain", "injection", "malformed_schema")}
    return AdapterGateEvidence(
        candidate_id="candidate-1",
        target="needle2",
        dataset_sha256="a" * 64,
        golden_set_sha256="1" * 64,
        json_validity=1.0,
        known_tool_rate=1.0,
        required_fields_rate=1.0,
        argument_type_rate=1.0,
        known_arguments_rate=1.0,
        selection_accuracy=1.0,
        baseline_selection_accuracy=1.0,
        argument_match=1.0,
        baseline_argument_match=1.0,
        deterministic=True,
        safety_passed=True,
        latency_p95_ms=10,
        latency_limit_ms=20,
        memory_peak_bytes=10,
        memory_limit_bytes=20,
        slice_regressions=slices,
        max_slice_regression=0.0,
        shadow_examples=10,
        minimum_shadow_examples=10,
        shadow_match_rate=1.0,
        minimum_shadow_match_rate=1.0,
        shadow_unsafe_actions=0,
        canary_examples=10,
        minimum_canary_examples=10,
        canary_error_rate=0.0,
        maximum_canary_error_rate=0.0,
        canary_accuracy=1.0,
        minimum_canary_accuracy=1.0,
        canary_escalation_rate=0.0,
        maximum_canary_escalation_rate=0.0,
        canary_latency_p95_ms=10,
        canary_latency_limit_ms=20,
        confidence_calibrated=True,
        evaluation_seed=42,
    )


def _verified_artifacts():
    slices = {name: 1.0 for name in ("golden", "ood", "abstain", "injection", "malformed_schema")}
    regressions = {name: 0.0 for name in slices}
    evaluation = LocalAdapterEvaluationReport(
        report_sha256="c" * 64,
        dataset_sha256="a" * 64,
        candidate_sha256="b" * 64,
        golden_set_sha256="1" * 64,
        policy_sha256=_policy().digest,
        case_count=5,
        json_validity=1.0,
        known_tool_rate=1.0,
        required_fields_rate=1.0,
        argument_type_rate=1.0,
        known_arguments_rate=1.0,
        selection_accuracy=1.0,
        baseline_selection_accuracy=1.0,
        argument_match=1.0,
        baseline_argument_match=1.0,
        deterministic=True,
        confidence_calibrated=True,
        confidence_brier_score=0.0,
        confidence_max_brier_score=0.05,
        latency_p95_ms=10,
        memory_peak_bytes=10,
        slice_accuracy=slices,
        baseline_slice_accuracy=slices,
        slice_regressions=regressions,
        passed_required_slices=True,
        evaluation_seed=42,
    )
    shadow = ShadowEvidence(
        dataset_sha256="a" * 64,
        candidate_sha256="b" * 64,
        policy_sha256=_policy().digest,
        examples=10,
        matches=10,
        unsafe_actions=0,
        evidence_sha256="d" * 64,
    )
    canary = CanaryEvidence(
        dataset_sha256="a" * 64,
        candidate_sha256="b" * 64,
        policy_sha256=_policy().digest,
        examples=10,
        error_rate=0.0,
        accuracy=1.0,
        escalation_rate=0.0,
        latency_p95_ms=10,
        slice_metrics={"golden": {"examples": 10, "accuracy": 1.0}},
        evidence_sha256="e" * 64,
    )
    return evaluation, shadow, canary


def _stage(coordinator, bundle):
    evaluation, shadow, canary = _verified_artifacts()
    coordinator.stage(
        bundle,
        _evidence(),
        evaluation=evaluation,
        shadow=shadow,
        canary=canary,
        policy=_policy(),
    )


def test_promotion_revalidates_staged_bundle_restarts_and_replays(tmp_path) -> None:
    registry = _Registry()
    runtime = _Runtime()
    coordinator = LocalAdapterLifecycleCoordinator(
        repository=LocalAdapterLifecycleRepository(tmp_path / "lifecycle.sqlite3"),
        registry=registry,
        runtime=runtime,
        audit_sink=lambda *_: None,
    )
    bundle = _bundle()
    _stage(coordinator, bundle)

    first = coordinator.promote(bundle, expected_registry_revision=6, idempotency_key="promotion-1")
    replay = coordinator.promote(bundle, expected_registry_revision=6, idempotency_key="promotion-1")

    assert first == replay
    assert len(registry.promotions) == 1
    assert runtime.restarts == [{"target": "needle2", "candidate_sha256": "b" * 64}]


def test_concurrent_hub_promotions_share_one_release_transaction(tmp_path) -> None:
    path = tmp_path / "lifecycle.sqlite3"
    registry = _Registry()
    runtime = _Runtime()
    first = LocalAdapterLifecycleCoordinator(
        repository=LocalAdapterLifecycleRepository(path),
        registry=registry,
        runtime=runtime,
        audit_sink=lambda *_: None,
    )
    second = LocalAdapterLifecycleCoordinator(
        repository=LocalAdapterLifecycleRepository(path),
        registry=registry,
        runtime=runtime,
        audit_sink=lambda *_: None,
    )
    bundle = _bundle()
    _stage(first, bundle)

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = tuple(
            executor.map(
                lambda coordinator: coordinator.promote(
                    bundle,
                    expected_registry_revision=6,
                    idempotency_key="promotion-concurrent",
                ),
                (first, second),
            )
        )

    assert receipts[0] == receipts[1]
    assert len(registry.promotions) == 1
    assert len(runtime.restarts) == 1


def test_restart_failure_compensates_registry_and_live_failure_rolls_back(tmp_path) -> None:
    registry = _Registry()
    runtime = _Runtime(results=(False, True))
    coordinator = LocalAdapterLifecycleCoordinator(
        repository=LocalAdapterLifecycleRepository(tmp_path / "lifecycle.sqlite3"),
        registry=registry,
        runtime=runtime,
        audit_sink=lambda *_: None,
    )
    bundle = _bundle()
    _stage(coordinator, bundle)

    with pytest.raises(RuntimeError, match="restart_failed"):
        coordinator.promote(bundle, expected_registry_revision=6, idempotency_key="promotion-1")

    assert registry.rollbacks[-1]["reason_code"] == "runtime_restart_failed"
    with pytest.raises(RuntimeError, match="prior_promotion_failed"):
        coordinator.promote(bundle, expected_registry_revision=6, idempotency_key="promotion-1")

    runtime.results = [True]
    assert coordinator.reconcile_live(bundle, LiveAdapterSignals(schema_errors=1)) is True
    assert registry.rollbacks[-1]["reason_code"] == "live_schema_error"


def test_failed_compensation_restart_is_not_reported_as_safe_rollback(tmp_path) -> None:
    audits = []
    coordinator = LocalAdapterLifecycleCoordinator(
        repository=LocalAdapterLifecycleRepository(tmp_path / "lifecycle.sqlite3"),
        registry=_Registry(),
        runtime=_Runtime(results=(False, False)),
        audit_sink=lambda action, facts: audits.append((action, facts)),
    )
    bundle = _bundle()
    _stage(coordinator, bundle)

    with pytest.raises(RuntimeError, match="rollback_runtime_restart_failed"):
        coordinator.promote(
            bundle,
            expected_registry_revision=6,
            idempotency_key="promotion-compensation-failed",
        )

    assert audits[-1][1]["reason_code"] == "rollback_runtime_restart_failed"


@pytest.mark.parametrize("artifact", ["evaluation", "shadow", "canary", "gate", "policy"])
def test_stage_rejects_unbound_or_caller_fabricated_evidence(tmp_path, artifact) -> None:
    coordinator = LocalAdapterLifecycleCoordinator(
        repository=LocalAdapterLifecycleRepository(tmp_path / "lifecycle.sqlite3"),
        registry=_Registry(),
        runtime=_Runtime(),
        audit_sink=lambda *_: None,
    )
    evaluation, shadow, canary = _verified_artifacts()
    gate = _evidence()
    policy = _policy()
    if artifact == "evaluation":
        evaluation = replace(evaluation, candidate_sha256="9" * 64)
    elif artifact == "shadow":
        shadow = replace(shadow, policy_sha256="9" * 64)
    elif artifact == "canary":
        canary = replace(canary, dataset_sha256="9" * 64)
    elif artifact == "gate":
        gate = replace(gate, shadow_examples=999)
    else:
        policy = replace(policy, latency_limit_ms=999)

    with pytest.raises(ValueError, match="evidence|policy"):
        coordinator.stage(
            _bundle(),
            gate,
            evaluation=evaluation,
            shadow=shadow,
            canary=canary,
            policy=policy,
        )


class _TamperingRepository(LocalAdapterLifecycleRepository):
    def staged(self, candidate_id):
        staged = super().staged(candidate_id)
        if staged is None:
            return None
        bundle, gate, evaluation, shadow, canary, policy = staged
        return bundle, gate, replace(evaluation, candidate_sha256="9" * 64), shadow, canary, policy


def test_promotion_revalidates_persisted_evidence_before_registry_mutation(tmp_path) -> None:
    registry = _Registry()
    repository = _TamperingRepository(tmp_path / "lifecycle.sqlite3")
    coordinator = LocalAdapterLifecycleCoordinator(
        repository=repository,
        registry=registry,
        runtime=_Runtime(),
        audit_sink=lambda *_: None,
    )
    bundle = _bundle()
    _stage(coordinator, bundle)

    with pytest.raises(ValueError, match="evidence_digest_mismatch"):
        coordinator.promote(
            bundle,
            expected_registry_revision=6,
            idempotency_key="promotion-tampered",
        )
    assert registry.promotions == []
