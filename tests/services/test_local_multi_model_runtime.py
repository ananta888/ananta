from agent.services.local_multi_model_runtime import (
    GiB,
    LocalModelPlacementPolicy,
    LocalModelRoutingPolicy,
    ModelRouteRequest,
    ResourceSnapshot,
    rtx3080_local_model_capabilities,
)


def test_rtx3080_placement_starts_lfm_before_kat_and_keeps_reserve():
    decision = LocalModelPlacementPolicy().decide(
        rtx3080_local_model_capabilities(),
        ResourceSnapshot(10 * GiB, 10 * GiB, 60 * GiB),
    )

    assert decision.admitted is True
    assert decision.start_order == ("lfm", "kat", "needle")
    assert decision.reserve_vram_bytes >= int(1.5 * GiB)


def test_placement_reduces_lfm_context_then_fails_closed():
    decision = LocalModelPlacementPolicy().decide(
        rtx3080_local_model_capabilities(),
        ResourceSnapshot(10 * GiB, 8 * GiB, 60 * GiB),
    )

    assert decision.admitted is False
    assert decision.reason_code == "insufficient_vram_with_reserve"
    assert decision.effective_contexts["lfm"] == 16384


def test_high_confidence_needle_candidate_never_gains_orchestration_authority():
    decision = LocalModelRoutingPolicy().decide(ModelRouteRequest(
        task_kind="classification",
        prompt_chars=100,
        requires_tools=True,
        needle_candidate_valid=True,
        needle_confidence=0.98,
        tool_risk_class="read",
    ))

    assert decision.target == "needle_tool"
    assert decision.fallback_chain == ("lfm", "kat")


def test_unknown_or_low_confidence_work_routes_conservatively_to_kat():
    policy = LocalModelRoutingPolicy()

    low_confidence = policy.decide(ModelRouteRequest(
        task_kind="classification", prompt_chars=50, requires_tools=True,
        needle_candidate_valid=True, needle_confidence=None, tool_risk_class="read",
    ))
    complex_task = policy.decide(ModelRouteRequest(task_kind="repo_analysis", prompt_chars=50))

    assert low_confidence.target == "kat"
    assert complex_task.target == "kat"
