from __future__ import annotations

from typing import Any

import requests


class GenericWebhookWorkflowProvider:
    provider_id = "generic_webhook"

    def __init__(self, endpoint: str | None = None, timeout_seconds: int = 8) -> None:
        self.endpoint = str(endpoint or "").strip()
        self.timeout_seconds = max(1, int(timeout_seconds))

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        if request.get("dry_run"):
            return {
                "status": "degraded",
                "provider": self.provider_id,
                "workflow_id": request.get("workflow_id"),
                "correlation_id": request.get("correlation_id"),
                "output": {"dry_run": True},
                "external_run_ref": None,
            }
        if not self.endpoint:
            return {
                "status": "failed",
                "provider": self.provider_id,
                "workflow_id": request.get("workflow_id"),
                "correlation_id": request.get("correlation_id"),
                "output": {"error": "endpoint_not_configured"},
                "external_run_ref": None,
            }
        resp = requests.post(self.endpoint, json=request.get("input_payload") or {}, timeout=self.timeout_seconds)
        return {
            "status": "completed" if 200 <= resp.status_code < 300 else "failed",
            "provider": self.provider_id,
            "workflow_id": request.get("workflow_id"),
            "correlation_id": request.get("correlation_id"),
            "output": {"status_code": resp.status_code, "body": resp.text[:2000]},
            "external_run_ref": None,
        }
