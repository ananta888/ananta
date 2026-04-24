from unittest.mock import patch

from agent.services.context_bundle_service import ContextBundleService
from agent.services.doom_loop_service import get_doom_loop_service
from agent.services.tool_routing_service import get_tool_routing_service
from agent.services.approval_policy_service import get_approval_policy_service


def test_control_layer_contract_doom_loop_detects_repeated_failure_pattern():
    service = get_doom_loop_service()
    signals = [
        service.build_signal(
            task_id="task-1",
            trace_id=f"trace-{idx}",
            backend_name="opencode",
            action_type="command",
            failure_type="timeout",
            iteration_count=idx + 1,
            action_signature="npm test",
            progress_made=False,
        )
        for idx in range(6)
    ]
    decision = service.detect(signals=signals, policy={"repeated_failure_threshold": 3, "no_progress_threshold": 3})
    payload = decision.as_dict()
    assert payload["detected"] is True
    assert payload["classification"] in {"repeated_failure", "no_progress"}
    assert payload["action"] in {"inject_correction", "require_review", "pause", "abort"}


def test_control_layer_contract_router_honors_capabilities_and_fallbacks():
    service = get_tool_routing_service()
    fake_backends = {
        "capabilities": {
            "sgpt": {"available": True},
            "opencode": {"available": True},
        }
    }
    with patch("agent.services.tool_routing_service.get_integration_registry_service") as mock_registry:
        mock_registry.return_value.list_execution_backends.return_value = fake_backends
        routed = service.route_execution_backend(
            task_kind="coding",
            requested_backend="sgpt",
            required_capabilities=["patching"],
            governance_mode="balanced",
            agent_cfg={},
        )
    decision = routed["decision"]
    assert decision["selected_target"] == "opencode"
    assert any(item["target"] == "sgpt" and item["reason"].startswith("missing_capabilities") for item in decision["alternatives"])


def test_control_layer_contract_approval_enforces_specialized_backend_confirmation():
    service = get_approval_policy_service()
    decision = service.evaluate(
        command="echo run",
        tool_calls=None,
        task={"id": "approval-1", "last_proposal": {"routing": {"effective_backend": "ml_intern"}}},
        agent_cfg={
            "governance_mode": "balanced",
            "unified_approval_policy": {"enabled": True, "enforce_confirm_required": True},
            "specialized_worker_profiles": {
                "enabled": True,
                "profiles": {"ml_intern": {"enabled": True, "requires_approval": True, "risk_class": "medium"}},
            },
        },
    )
    payload = decision.as_dict()
    assert payload["classification"] == "confirm_required"
    assert payload["required_confirmation_level"] == "operator"
    assert payload["details"]["specialized_backend"]["backend_id"] == "ml_intern"


def test_control_layer_contract_context_compaction_preserves_provenance_metadata():
    service = ContextBundleService()
    chunks = []
    for idx in range(8):
        chunks.append(
            {
                "engine": "knowledge_index",
                "source": f"artifact:{idx}",
                "score": 1.0 - (idx * 0.03),
                "content": ("policy and implementation details " * 140),
                "metadata": {
                    "record_kind": "policy" if idx < 2 else "guide",
                    "source_type": "artifact" if idx < 4 else "wiki",
                    "chunk_id": f"chunk-{idx}",
                },
            }
        )
    bundle = service.build_bundle(
        query="stabilize orchestration controls",
        context_payload={"chunks": chunks, "strategy": {"fusion": {"candidate_counts": {"all": 8, "final": 8}}}},
        include_context_text=False,
        policy_mode="compact",
        task_kind="bugfix",
        total_budget_tokens=4096,
        budget_tokens_by_mode={"compact": 4096, "standard": 4096, "full": 4096},
    )
    assert bundle["compaction"]["version"] == "priority-budget-compaction-v1"
    assert bundle["compaction"]["provenance_preserved"] is True
    assert bundle["why_this_context"]["compaction_summary"]["provenance_preserved"] is True
    assert bundle["context_policy"]["source_prioritization_rules"]
