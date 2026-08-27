from dataclasses import replace

import pytest

from agent.services.local_adapter_lifecycle import (
    AdapterGateEvidence,
    LiveAdapterSignals,
    LocalAdapterPromotionPolicy,
    LocalAdapterRollbackPolicy,
)


def passing_evidence():
    return AdapterGateEvidence(
        candidate_id="needle2-20260823-001",
        target="needle2",
        dataset_sha256="a" * 64,
        golden_set_sha256="b" * 64,
        json_validity=1.0,
        known_tool_rate=1.0,
        required_fields_rate=1.0,
        argument_type_rate=1.0,
        known_arguments_rate=1.0,
        selection_accuracy=0.95,
        baseline_selection_accuracy=0.94,
        argument_match=0.93,
        baseline_argument_match=0.92,
        deterministic=True,
        safety_passed=True,
        latency_p95_ms=20,
        latency_limit_ms=30,
        memory_peak_bytes=100,
        memory_limit_bytes=200,
        slice_regressions={"abstain": 0.0, "ood": 0.0},
        max_slice_regression=0.0,
        shadow_examples=500,
        minimum_shadow_examples=500,
        shadow_match_rate=1.0,
        minimum_shadow_match_rate=0.99,
        shadow_unsafe_actions=0,
        canary_examples=100,
        minimum_canary_examples=100,
        canary_error_rate=0.0,
        maximum_canary_error_rate=0.01,
        canary_accuracy=1.0,
        minimum_canary_accuracy=0.99,
        canary_escalation_rate=0.0,
        maximum_canary_escalation_rate=0.01,
        canary_latency_p95_ms=20,
        canary_latency_limit_ms=30,
        confidence_calibrated=True,
        evaluation_seed=42,
    )


def test_all_hard_gates_are_required_for_automatic_promotion():
    assert LocalAdapterPromotionPolicy().evaluate(passing_evidence()).promote is True
    denied = LocalAdapterPromotionPolicy().evaluate(
        replace(passing_evidence(), json_validity=0.999, confidence_calibrated=False)
    )
    assert denied.promote is False
    assert denied.reason_codes == (
        "json_validity_regression",
        "external_confidence_calibration_required",
    )


def test_live_safety_signal_requests_automatic_rollback():
    decision = LocalAdapterRollbackPolicy().evaluate(LiveAdapterSignals(safety_violations=1))
    assert decision.promote is False
    assert decision.reason_codes == ("live_safety_violation",)


@pytest.mark.parametrize(
    ("factory", "expected"),
    [
        (lambda: replace(passing_evidence(), canary_error_rate=float("nan")), "canary_error_rate"),
        (lambda: LiveAdapterSignals(tool_error_rate=float("nan")), "tool_error_rate"),
        (lambda: replace(passing_evidence(), memory_peak_bytes=-1), "memory_peak_bytes"),
    ],
)
def test_lifecycle_inputs_reject_nonfinite_or_negative_measurements(factory, expected):
    with pytest.raises(ValueError, match=expected):
        factory()
