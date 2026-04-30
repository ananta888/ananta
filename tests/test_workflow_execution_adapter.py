from __future__ import annotations

from agent.services.workflow_execution_adapter import WorkflowExecutionAdapter
from agent.services.workflow_providers.mock_provider import MockWorkflowProvider
from agent.services.workflow_registry import WorkflowRegistry


def _registry() -> WorkflowRegistry:
    return WorkflowRegistry(descriptors=[
        {
            "provider": "mock",
            "workflow_id": "wf",
            "display_name": "wf",
            "capability": "read",
            "risk_class": "low",
            "approval_required": False,
            "dry_run_supported": True,
            "callback_required": False,
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
        }
    ])


def test_execution_adapter_dry_run_with_mock_provider() -> None:
    adapter = WorkflowExecutionAdapter(registry=_registry(), providers={"mock": MockWorkflowProvider()})
    out = adapter.execute({"workflow_id": "wf", "dry_run": True, "input_payload": {"a": 1}})
    assert out["status"] in {"degraded", "completed"}
    assert out["provider"] == "mock"


def test_execution_adapter_blocks_unknown_workflow() -> None:
    adapter = WorkflowExecutionAdapter(registry=_registry(), providers={"mock": MockWorkflowProvider()})
    out = adapter.execute({"workflow_id": "missing"})
    assert out["status"] == "blocked"
