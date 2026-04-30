from __future__ import annotations

from agent.services.workflow_execution_adapter import WorkflowExecutionAdapter
from agent.services.workflow_providers.mock_provider import MockWorkflowProvider
from agent.services.workflow_registry import WorkflowRegistry


def test_workflow_audit_event_paths_smoke() -> None:
    reg = WorkflowRegistry(descriptors=[{"provider": "mock", "workflow_id": "wf", "display_name": "wf", "capability": "read", "risk_class": "low", "approval_required": False, "input_schema": {}, "output_schema": {}}])
    adapter = WorkflowExecutionAdapter(registry=reg, providers={"mock": MockWorkflowProvider()})
    out = adapter.execute({"workflow_id": "wf", "task_id": "T1", "goal_id": "G1", "trace_id": "X1", "dry_run": True})
    assert out["provider"] == "mock"


def test_workflow_blocked_path_smoke() -> None:
    reg = WorkflowRegistry(descriptors=[{"provider": "mock", "workflow_id": "wfw", "display_name": "wfw", "capability": "write", "risk_class": "high", "approval_required": True, "input_schema": {}, "output_schema": {}}])
    adapter = WorkflowExecutionAdapter(registry=reg, providers={"mock": MockWorkflowProvider()})
    out = adapter.execute({"workflow_id": "wfw", "dry_run": True}, approved=False)
    assert out["status"] == "blocked"
