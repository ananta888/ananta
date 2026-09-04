from __future__ import annotations

import pytest

from agent.services.dspy_evaluation_attestation_service import DspyEvaluationAttestationService
from agent.services.dspy_evaluation_bridge_service import DspyEvaluationBridgeService
from agent.services.dspy_promotion_service import DspyPromotionService
from agent.services.dspy_rollout_service import DspyRolloutService
from ananta_contracts.dspy_optimization import PromotionPlanV1, canonical_digest


def _result(*, quality: float, program_digest: str) -> dict:
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
            "parse_rate": 1,
            "policy_violations": 0,
            "tokens": 100,
            "cost_micros": 10,
            "latency_ms": 100,
        },
    }


def test_promotion_plan_is_atomic_and_canary_can_only_expand_or_stop_by_policy(tmp_path) -> None:
    attestations = DspyEvaluationAttestationService(b"x" * 32)
    evaluation = DspyEvaluationBridgeService(attestations).compare(
        baseline=_result(quality=0.5, program_digest="f" * 64),
        candidate=_result(quality=0.7, program_digest="e" * 64),
    )
    plan = PromotionPlanV1(
        tenant_id="tenant-1",
        scope_id="planning-en-v1",
        candidate_digest="e" * 64,
        baseline_digest="f" * 64,
        evaluation_digest=evaluation["evaluation_digest"],
        dataset_digest="a" * 64,
        metric_set_digest="b" * 64,
        thresholds_digest=canonical_digest({"quality_delta": 0.02}),
        expected_registry_revision=0,
        canary_percent=10,
        automatic_stop_reason_codes=["security_regression", "cost_regression"],
        canary_duration_seconds=3600,
        minimum_sample_size=40,
    )
    service = DspyPromotionService(tmp_path / "registry.sqlite3", attestations=attestations)
    promoted = service.promote_plan(plan=plan.to_dict(), evaluation=evaluation)
    assert promoted["revision"] == 1
    assert promoted["promotion_plan_digest"] == plan.digest
    expanded = service.set_canary_percent(
        tenant_id="tenant-1", scope_id="planning-en-v1", canary_percent=50, expected_revision=1
    )
    assert expanded["canary_percent"] == 50
    with pytest.raises(PermissionError, match="stop_reason_denied"):
        service.stop_canary(
            tenant_id="tenant-1", scope_id="planning-en-v1", reason_code="quality_guess", expected_revision=2
        )
    stopped = service.stop_canary(
        tenant_id="tenant-1", scope_id="planning-en-v1", reason_code="security_regression", expected_revision=2
    )
    assert stopped["state"] == "auto_stopped"
    assert stopped["active_digest"] == "f" * 64
    provenance = service.provenance(tenant_id="tenant-1", scope_id="planning-en-v1")
    assert [value["revision"] for value in provenance["history"]] == [1, 2, 3]
    assert provenance["history"][0]["evaluation_digest"] == evaluation["evaluation_digest"]
    assert provenance["history"][-1]["previous_digest"] == "e" * 64


def test_shadow_observation_cannot_change_product_state() -> None:
    called = 0

    def candidate():
        nonlocal called
        called += 1
        return {"tasks": [{"id": "candidate"}]}

    result = DspyRolloutService().shadow(
        tenant_id="tenant-1",
        scope_id="planning-en-v1",
        input_payload={"goal": "x"},
        execute_candidate=candidate,
        max_observations=10,
        observation_index=0,
    )
    assert called == 1
    assert result["user_result_changed"] is False
    assert result["task_state_changed"] is False
    assert result["artifact_promoted"] is False


def test_canary_regression_is_stopped_automatically_without_human_gate() -> None:
    result = DspyRolloutService().evaluate_canary(
        observations={
            "sample_count": 50,
            "security_violations": 0,
            "parse_error_rate": 0,
            "cost_ratio": 1.2,
            "latency_ratio": 1,
        },
        minimum_sample_size=40,
    )
    assert result == {
        "decision": "stop",
        "reason_code": "cost_regression",
        "automatic_stop": True,
        "human_intervention_required": False,
    }
