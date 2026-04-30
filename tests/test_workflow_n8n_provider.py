from __future__ import annotations

from agent.services.workflow_providers.n8n_provider import N8nWorkflowProvider


def test_n8n_provider_dry_run_no_live_n8n() -> None:
    provider = N8nWorkflowProvider(webhook_base_url="https://example.invalid/webhook")
    out = provider.execute({"workflow_id": "wf", "dry_run": True, "correlation_id": "c1"})
    assert out["status"] == "degraded"
