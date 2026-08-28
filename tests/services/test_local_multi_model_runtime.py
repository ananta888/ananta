import json
from pathlib import Path

import pytest

from agent.services.local_multi_model_runtime import (
    GiB,
    LocalModelPlacementPolicy,
    LocalModelRoutingPolicy,
    MiB,
    ModelRouteRequest,
    ResourceSnapshot,
    RuntimeResourceMeasurement,
    rtx3080_local_model_capabilities,
)
from agent.services.model_profile_loader import ModelProfileLoader
from agent.services.model_profile_resolver import RoutingRules

ROOT = Path(__file__).resolve().parents[2]


def test_rtx3080_placement_starts_lfm_before_kat_and_keeps_reserve():
    decision = LocalModelPlacementPolicy().decide(
        rtx3080_local_model_capabilities(),
        ResourceSnapshot(10 * GiB, 10 * GiB, 60 * GiB),
    )

    assert decision.admitted is True
    assert decision.start_order == ("lfm", "kat", "needle")
    assert decision.reserve_vram_bytes >= int(1.5 * GiB)


def test_placement_reduces_lfm_context_then_fails_closed():
    degraded = LocalModelPlacementPolicy().decide(
        rtx3080_local_model_capabilities(),
        ResourceSnapshot(10 * GiB, 8 * GiB, 60 * GiB),
    )
    denied = LocalModelPlacementPolicy().decide(
        rtx3080_local_model_capabilities(),
        ResourceSnapshot(10 * GiB, int(7.5 * GiB), 60 * GiB),
    )

    assert degraded.admitted is True
    assert degraded.effective_contexts["lfm"] == 16384
    assert denied.admitted is False
    assert denied.reason_code == "insufficient_vram_with_reserve"
    assert denied.effective_contexts["lfm"] == 16384


def test_placement_reclaims_only_bounded_managed_resources_on_restart():
    decision = LocalModelPlacementPolicy().decide(
        rtx3080_local_model_capabilities(),
        ResourceSnapshot(
            total_vram_bytes=10 * GiB,
            free_vram_bytes=2 * GiB,
            available_ram_bytes=9 * GiB,
            runtime_usage={
                "kat": RuntimeResourceMeasurement(5 * GiB, 46 * GiB),
                "lfm": RuntimeResourceMeasurement(3 * GiB, 1 * GiB),
                "needle": RuntimeResourceMeasurement(0, 64 * MiB),
            },
        ),
    )

    assert decision.admitted is True
    assert decision.effective_contexts["lfm"] == 32768


def test_declared_ram_budgets_cover_measured_long_context_process_trees():
    capabilities = {row.runtime_id: row for row in rtx3080_local_model_capabilities()}

    assert capabilities["kat"].ram_budget_bytes == 54 * GiB
    assert capabilities["lfm"].ram_budget_bytes == 1 * GiB


def test_high_confidence_needle_candidate_never_gains_orchestration_authority():
    decision = LocalModelRoutingPolicy().decide(
        ModelRouteRequest(
            task_kind="classification",
            prompt_chars=100,
            requires_tools=True,
            needle_candidate_valid=True,
            needle_confidence=0.98,
            tool_risk_class="read",
        )
    )

    assert decision.target == "needle_tool"
    assert decision.fallback_chain == ("lfm", "kat")


def test_unknown_or_low_confidence_work_routes_conservatively_to_kat():
    policy = LocalModelRoutingPolicy()

    low_confidence = policy.decide(
        ModelRouteRequest(
            task_kind="classification",
            prompt_chars=50,
            requires_tools=True,
            needle_candidate_valid=True,
            needle_confidence=None,
            tool_risk_class="read",
        )
    )
    complex_task = policy.decide(ModelRouteRequest(task_kind="repo_analysis", prompt_chars=50))

    assert low_confidence.target == "kat"
    assert complex_task.target == "kat"


def test_nonfinite_confidence_cannot_bypass_conservative_tool_routing():
    with pytest.raises(ValueError, match="confidence_invalid"):
        ModelRouteRequest(
            task_kind="classification",
            prompt_chars=50,
            requires_tools=True,
            needle_candidate_valid=True,
            needle_confidence=float("nan"),
            tool_risk_class="read",
        )


def test_deployed_local_profiles_cover_consumer_roles_without_synthetic_default():
    profiles = ModelProfileLoader().load_file(ROOT / "config/models/local-kat-lfm-needle-rtx3080.model_profiles.yaml")
    routing = json.loads(
        (ROOT / "config/models/local-kat-lfm-needle-rtx3080.model_routing.json").read_text(encoding="utf-8")
    )
    rules = RoutingRules.from_dict(routing, strict=True)

    assert profiles.ok
    assert rules.role_rules == {
        "coder": "local_kat_coder_v25_heavy",
        "planner": "local_kat_coder_v25_heavy",
        "reviewer": "local_kat_coder_v25_heavy",
        "reasoning": "local_kat_coder_v25_heavy",
        "chat": "local_lfm25_agentic_fast",
        "summarizer": "local_lfm25_agentic_fast",
        "any": "local_lfm25_agentic_fast",
    }


def test_runtime_script_rehashes_projected_adapters_before_loading_them():
    script = (ROOT / "scripts/local-multi-model-runtime.sh").read_text(encoding="utf-8")

    assert "ananta.local-adapter-serving-activation.v1" in script
    assert "local adapter serving artifact escaped its root" in script
    assert "local adapter serving digest mismatch" in script
    assert 'lora_args=(--lora "$LFM_LORA")' in script
    assert 'ANANTA_NEEDLE_WEIGHTS="$NEEDLE_WEIGHTS"' in script
