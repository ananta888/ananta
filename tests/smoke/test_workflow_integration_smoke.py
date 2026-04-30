from __future__ import annotations

from agent.services.workflow_execution_adapter import WorkflowExecutionAdapter
from agent.services.workflow_providers.mock_provider import MockWorkflowProvider
from agent.services.workflow_registry import WorkflowRegistry


def test_workflow_integration_smoke_registry_policy_dry_run() -> None:
    reg = WorkflowRegistry(descriptors=[{"provider": "mock", "workflow_id": "wf-smoke", "display_name": "wf", "capability": "read", "risk_class": "low", "approval_required": False, "dry_run_supported": True, "callback_required": False, "input_schema": {"type": "object"}, "output_schema": {"type": "object"}}])
    adapter = WorkflowExecutionAdapter(registry=reg, providers={"mock": MockWorkflowProvider()})
    out = adapter.execute({"workflow_id": "wf-smoke", "dry_run": True, "input_payload": {"goal_id": "G1"}})
    assert out["status"] in {"degraded", "completed"}
