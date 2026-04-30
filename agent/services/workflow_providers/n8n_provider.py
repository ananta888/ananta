from __future__ import annotations

from typing import Any

import requests


class N8nWorkflowProvider:
    provider_id = "n8n"

    def __init__(self, webhook_base_url: str, token: str | None = None, timeout_seconds: int = 10) -> None:
        self.webhook_base_url = webhook_base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = max(1, int(timeout_seconds))

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        workflow_id = str(request.get("workflow_id") or "").strip()
        if not workflow_id:
            return {"status": "failed", "output": {"error": "workflow_id_required"}}
        if request.get("dry_run"):
            return {
                "status": "degraded",
                "provider": self.provider_id,
                "workflow_id": workflow_id,
                "correlation_id": request.get("correlation_id"),
                "output": {"dry_run": True},
                "external_run_ref": None,
            }
        url = f"{self.webhook_base_url}/{workflow_id}"
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        resp = requests.post(url, json=request.get("input_payload") or {}, headers=headers, timeout=self.timeout_seconds)
        return {
            "status": "completed" if 200 <= resp.status_code < 300 else "failed",
            "provider": self.provider_id,
            "workflow_id": workflow_id,
            "correlation_id": request.get("correlation_id"),
            "output": {"status_code": resp.status_code, "body": resp.text[:2000]},
            "external_run_ref": resp.headers.get("x-n8n-run-id"),
        }
