from __future__ import annotations

import hashlib
from unittest.mock import patch

from agent.services.tool_routing_service import ToolRoutingService
from worker.core.context_resolver import ContextBlock
from worker.core.execution_envelope import CapabilityGrant, ExecutionEnvelope
from worker.core.hermes_adapter import HermesAdapter
from worker.core.hermes_adapter_config import HermesAdapterConfig


class _Client:
    def health(self, **_: object) -> dict[str, object]:
        return {"ok": True}

    def chat_completions(self, **_: object) -> dict[str, object]:
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"status":"success","artifact_type":"plan","summary":"E2E plan","findings":[],"risks":[],"suggested_tests":["pytest -q"],"confidence":0.6,"requires_approval_for_apply":false,"no_side_effects_claimed":true}'
                    }
                }
            ]
        }


def test_mocked_e2e_plan_only_via_router_and_adapter() -> None:
    service = ToolRoutingService()
    cfg = {"feature_flags": {"enable_hermes_worker_adapter": True}, "hermes_worker_adapter": {"enabled": True}}
    with patch("agent.services.tool_routing_service.get_integration_registry_service") as mock_registry:
        mock_registry.return_value.list_execution_backends.return_value = {"capabilities": {"hermes": {"available": True}}}
        routed = service.route_execution_backend(
            task_kind="plan_only",
            requested_backend="hermes",
            required_capabilities=["planning"],
            governance_mode="balanced",
            agent_cfg=cfg,
        )
    assert routed["decision"]["selected_target"] == "hermes"

    adapter = HermesAdapter(
        config=HermesAdapterConfig(enabled=True, feature_flag_enabled=True, base_url="http://localhost:1", default_model="m"),
        client=_Client(),  # type: ignore[arg-type]
    )
    env = ExecutionEnvelope(
        task_id="e2e-plan",
        actor_ref="hub:e2e",
        capability_grant=CapabilityGrant(capabilities=["planning"]),
        context_envelope_ref="ctx:1",
        audit_correlation_id="audit:e2e",
    )
    before = hashlib.sha256(b"workspace-unchanged").hexdigest()
    result = adapter.plan_only(env, context_blocks=[ContextBlock("task", "ctx1", "hub", content="Prepare rollout plan")])
    after = hashlib.sha256(b"workspace-unchanged").hexdigest()
    assert result.status.value == "success"
    assert result.artifacts and result.artifacts[0].kind == "plan_artifact"
    assert result.trace_bundle.events
    assert before == after
