from __future__ import annotations

from agent.services.workflow_providers.generic_webhook import GenericWebhookWorkflowProvider


def test_generic_webhook_provider_dry_run_without_network() -> None:
    provider = GenericWebhookWorkflowProvider(endpoint="")
    out = provider.execute({"workflow_id": "wf", "dry_run": True, "correlation_id": "c1", "input_payload": {}})
    assert out["status"] == "degraded"


def test_unknown_provider_blocked_by_adapter() -> None:
    from agent.services.workflow_execution_adapter import WorkflowExecutionAdapter
    from agent.services.workflow_registry import WorkflowRegistry
    reg = WorkflowRegistry(descriptors=[{"provider": "missing", "workflow_id": "wf", "display_name": "wf", "capability": "read", "risk_class": "low", "approval_required": False, "input_schema": {}, "output_schema": {}}])
    adapter = WorkflowExecutionAdapter(registry=reg, providers={})
    out = adapter.execute({"workflow_id": "wf"})
    assert out["status"] == "degraded"
