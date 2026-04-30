from __future__ import annotations

from typing import Any


class MockWorkflowProvider:
    provider_id = "mock"

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "degraded" if request.get("dry_run") else "completed",
            "provider": self.provider_id,
            "workflow_id": request.get("workflow_id"),
            "correlation_id": request.get("correlation_id"),
            "output": {"mock": True},
            "external_run_ref": "mock-run-1",
        }
