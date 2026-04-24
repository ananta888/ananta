from __future__ import annotations

import json


def build_fixture_transport():
    fixture_payloads = {
        "/health": {"state": "ready"},
        "/capabilities": {"capabilities": ["goals", "tasks", "artifacts", "approvals"]},
        "/tasks": {
            "items": [
                {"id": "T-1", "title": "Inspect runtime surface", "status": "in_progress"},
                {"id": "T-2", "title": "Run smoke flow", "status": "todo"},
            ]
        },
        "/artifacts": {
            "items": [
                {"id": "A-1", "title": "Runtime summary", "type": "markdown", "task_id": "T-1"},
            ]
        },
        "/approvals": {
            "items": [
                {"id": "AP-1", "scope": "repair_step", "state": "pending"},
            ]
        },
        "/repairs": {
            "items": [
                {
                    "session_id": "R-1",
                    "diagnosis": "disk pressure",
                    "proposed_steps": ["clean temp data"],
                    "verification_result": "pending",
                    "outcome": "not_executed",
                }
            ]
        },
    }

    def _transport(
        method: str,
        url: str,
        _headers: dict[str, str],
        body: bytes | None,
        _timeout: float,
    ) -> tuple[int, str]:
        if method == "POST" and url.endswith("/goals"):
            _ = json.loads((body or b"{}").decode("utf-8", "replace"))
            return 201, json.dumps({"goal_id": "G-1", "task_id": "T-1", "accepted": True})
        for endpoint, payload in fixture_payloads.items():
            if url.endswith(endpoint):
                return 200, json.dumps(payload)
        return 404, json.dumps({"error": "not_found"})

    return _transport
