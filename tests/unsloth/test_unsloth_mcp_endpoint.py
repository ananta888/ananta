from __future__ import annotations

from typing import Any


class _FakeMcpAdapter:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def execute(self, **values: Any) -> dict[str, Any]:
        self.calls.append(dict(values))
        return {
            "status": "queued",
            "tool_id": values["tool_id"],
            "correlation_id": values["correlation_id"],
            "receipt": {"task_id": "unsloth-task-1"},
        }


def test_admin_can_queue_bounded_unsloth_mcp_mutation_through_hub_route(
    client,
    app,
    admin_auth_header,
):
    adapter = _FakeMcpAdapter()
    app.extensions["unsloth_mcp_adapter"] = adapter
    headers = {
        **admin_auth_header,
        "Idempotency-Key": "idempotency-stop-0001",
    }

    response = client.post(
        "/api/ml-intern-training/unsloth/mcp/tools/stop_training",
        headers=headers,
        json={
            "arguments": {"save": True},
            "replay_nonce": "nonce-stop-training-0001",
            "replay_expires_at": 200.0,
            "confirmation_id": "approval-stop-0001",
            "correlation_id": "correlation-stop-0001",
        },
    )

    assert response.status_code == 202
    assert response.json["data"]["status"] == "queued"
    assert adapter.calls == [
        {
            "tool_id": "stop_training",
            "arguments": {"save": True},
            "tenant_id": adapter.calls[0]["tenant_id"],
            "actor_id": adapter.calls[0]["actor_id"],
            "roles": ("admin",),
            "replay_nonce": "nonce-stop-training-0001",
            "replay_expires_at": 200.0,
            "correlation_id": "correlation-stop-0001",
            "confirmation_id": "approval-stop-0001",
            "idempotency_key": "idempotency-stop-0001",
        }
    ]
