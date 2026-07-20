from __future__ import annotations

import pytest

from agent.services.semantic_media_rollout_policy import (
    ROLLOUT_STAGES,
    SemanticMediaHealthSignals,
    evaluate_rollout_health,
)


@pytest.mark.parametrize(
    "signals",
    (
        SemanticMediaHealthSignals(security_findings=1),
        SemanticMediaHealthSignals(privacy_findings=1),
        SemanticMediaHealthSignals(e2ee_downgrades=1),
        SemanticMediaHealthSignals(live_p95_ratio_micros=1_050_001),
        SemanticMediaHealthSignals(live_p99_ratio_micros=1_050_001),
        SemanticMediaHealthSignals(quality_gate_passed=False),
        SemanticMediaHealthSignals(budget_ratio_micros=1_000_001),
        SemanticMediaHealthSignals(resource_ratio_micros=1_000_001),
    ),
)
@pytest.mark.parametrize("stage", ROLLOUT_STAGES)
def test_release_signal_rolls_semantic_features_back_without_ending_ordinary_call(
    stage: str,
    signals: SemanticMediaHealthSignals,
) -> None:
    decision = evaluate_rollout_health(stage, signals)
    assert decision.semantic_action == "stop_and_rollback"
    assert decision.ordinary_call_action == "preserve"
    assert decision.target_stage == "observe_only"
    assert decision.reason_codes


def test_healthy_stage_remains_active_and_unknown_stage_is_rejected() -> None:
    for stage in ROLLOUT_STAGES:
        decision = evaluate_rollout_health(stage, SemanticMediaHealthSignals())
        assert decision.semantic_action == "continue"
        assert decision.ordinary_call_action == "preserve"
        assert decision.target_stage == stage
        assert decision.reason_codes == ()
    with pytest.raises(ValueError, match="semantic_media_rollout_stage_invalid"):
        evaluate_rollout_health("unknown", SemanticMediaHealthSignals())
