from __future__ import annotations

import pytest

from agent.services.dspy_evaluation_attestation_service import DspyEvaluationAttestationService
from agent.services.dspy_evaluation_bridge_service import DspyEvaluationBridgeService
from agent.services.dspy_promotion_service import DspyPromotionConflict, DspyPromotionService

ATTESTATIONS = DspyEvaluationAttestationService(b"t" * 32)


def _result(
    *,
    quality: float,
    program_digest: str = "e" * 64,
    parse_rate: float = 1.0,
    policy_violations: int = 0,
    cost: int = 100,
) -> dict:
    return {
        "program_digest": program_digest,
        "dataset_digest": "a" * 64,
        "metric_set_digest": "b" * 64,
        "provider_binding_id": "provider-binding:" + "c" * 64,
        "runtime_profile": "mock-v1",
        "test_split_digest": "d" * 64,
        "prompt_digest": "1" * 64,
        "dspy_version": "3.2.1",
        "hardware_profile": "cpu-x86_64",
        "cache_mode": "memory-only",
        "sampling_digest": "2" * 64,
        "seed": 17,
        "repetitions": 3,
        "warmups": 1,
        "sample_count": 40,
        "quality_standard_error": 0.001,
        "metrics": {
            "quality": quality,
            "parse_rate": parse_rate,
            "policy_violations": policy_violations,
            "tokens": 100,
            "cost_micros": cost,
            "latency_ms": 100,
        },
    }


def test_deterministic_failure_cannot_be_overridden_by_quality_score() -> None:
    evaluation = DspyEvaluationBridgeService(ATTESTATIONS).compare(
        baseline=_result(quality=0.5), candidate=_result(quality=0.9, parse_rate=0.8, policy_violations=1)
    )
    assert evaluation["promotion_eligible"] is False
    assert "dspy_deterministic_gate_failed" in evaluation["reason_codes"]


def test_comparison_fails_closed_for_incomparable_or_uncertain_runs() -> None:
    baseline = _result(quality=0.5)
    candidate = {**_result(quality=0.53), "hardware_profile": "gpu-a100", "quality_standard_error": 0.02}
    evaluation = DspyEvaluationBridgeService(ATTESTATIONS).compare(baseline=baseline, candidate=candidate)
    assert evaluation["promotion_eligible"] is False
    assert evaluation["deltas"] is None
    assert "dspy_evaluation_not_comparable" in evaluation["reason_codes"]
    assert "dspy_quality_improvement_insufficient" in evaluation["reason_codes"]


def test_policy_can_promote_and_rollback_automatically_after_all_gates(tmp_path) -> None:
    evaluation = DspyEvaluationBridgeService(ATTESTATIONS).compare(
        baseline=_result(quality=0.5, program_digest="f" * 64),
        candidate=_result(quality=0.7, program_digest="e" * 64),
    )
    service = DspyPromotionService(tmp_path / "registry.sqlite3", attestations=ATTESTATIONS)
    promoted = service.promote(
        tenant_id="tenant-1",
        scope_id="planning-en-strict",
        candidate_digest="e" * 64,
        baseline_digest="f" * 64,
        evaluation=evaluation,
        expected_revision=0,
        canary_percent=25,
    )
    assert promoted["human_intervention_required"] is False
    assert service.assignment(tenant_id="tenant-1", scope_id="planning-en-strict", subject_id="subject-1")[
        "variant"
    ] in {"baseline", "candidate"}
    rolled_back = service.rollback(tenant_id="tenant-1", scope_id="planning-en-strict", expected_revision=1)
    assert rolled_back["active_digest"] == "f" * 64
    with pytest.raises(DspyPromotionConflict):
        service.rollback(tenant_id="tenant-1", scope_id="planning-en-strict", expected_revision=1)


def test_promotion_refuses_red_or_missing_evaluation() -> None:
    with pytest.raises(PermissionError, match="attestation_invalid"):
        DspyPromotionService(":memory:", attestations=ATTESTATIONS).promote(
            tenant_id="tenant-1",
            scope_id="planning",
            candidate_digest="e" * 64,
            baseline_digest="f" * 64,
            evaluation={"promotion_eligible": False, "reason_codes": ["red"]},
            expected_revision=0,
        )


def test_promotion_refuses_tampered_or_program_unbound_evaluation(tmp_path) -> None:
    evaluation = DspyEvaluationBridgeService(ATTESTATIONS).compare(
        baseline=_result(quality=0.5, program_digest="f" * 64),
        candidate=_result(quality=0.7, program_digest="e" * 64),
    )
    service = DspyPromotionService(tmp_path / "registry.sqlite3", attestations=ATTESTATIONS)
    tampered = {**evaluation, "promotion_eligible": False}
    with pytest.raises(PermissionError, match="attestation_invalid"):
        service.promote(
            tenant_id="tenant-1",
            scope_id="planning",
            candidate_digest="e" * 64,
            baseline_digest="f" * 64,
            evaluation=tampered,
            expected_revision=0,
        )
    with pytest.raises(PermissionError, match="program_binding_invalid"):
        service.promote(
            tenant_id="tenant-1",
            scope_id="planning",
            candidate_digest="a" * 64,
            baseline_digest="f" * 64,
            evaluation=evaluation,
            expected_revision=0,
        )
