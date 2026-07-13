from __future__ import annotations

from flask import Flask

from agent.routes.workflow_runtime_internal import workflow_runtime_internal_bp
from ananta_contracts.hub_task_gateway import (
    HUB_TASK_COMMAND_SCHEMA,
    HUB_TASK_RECEIPT_SCHEMA,
    RETRY_BUDGET_RECEIPT_SCHEMA,
)
from ananta_contracts.workflow_worker_gateway import WORKFLOW_WORKER_COMMAND_SCHEMA


class _Gateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    @staticmethod
    def _receipt(operation_id: str = "operation-1") -> dict:
        return {
            "schema": HUB_TASK_RECEIPT_SCHEMA,
            "hub_task_id": "hub-task-1",
            "operation_id": operation_id,
            "status": "created",
            "authorization_state": "valid",
            "ledger_state": "authorized",
            "artifact_refs": [],
            "canonical_event_refs": [],
            "checkpoint_ref": "",
            "reason_code": "",
        }

    def submit(self, body: dict) -> dict:
        self.calls.append(("submit", body))
        return self._receipt(str(body.get("operation_id") or "operation-1"))

    def consume_retry(self, body: dict) -> dict:
        self.calls.append(("retry", body))
        return {
            "schema": RETRY_BUDGET_RECEIPT_SCHEMA,
            "retry_id": str(body.get("retry_id") or "retry-1"),
            "category": str(body.get("retry_category") or "temporal_activity"),
            "used": 1,
            "maximum": 2,
            "remaining": 1,
        }

    def get(self, *, hub_task_id: str, operation_id: str) -> dict:
        self.calls.append(("get", (hub_task_id, operation_id)))
        return self._receipt(operation_id)

    def dispatch_payload(self, *, hub_task_id: str, operation_id: str) -> dict:
        self.calls.append(("payload", (hub_task_id, operation_id)))
        return {"operation_id": operation_id}

    def finish(self, *, hub_task_id: str, command: dict) -> dict:
        self.calls.append(("result", (hub_task_id, command)))
        return self._receipt(str(command.get("operation_id") or "operation-1"))

    def cancel(self, *, hub_task_id: str, operation_id: str, reason: str) -> dict:
        self.calls.append(("cancel", (hub_task_id, operation_id, reason)))
        receipt = self._receipt(operation_id)
        receipt["status"] = "cancelled"
        receipt["ledger_state"] = "failed"
        return receipt


class _WorkerGateway:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, body: dict) -> dict:
        self.calls.append(body)
        return {
            "schema": "ananta.workflow-runtime-worker-decision.v1",
            "allowed": True,
            "reason_code": "hub_tool_authorized",
            "operation_id": str(body.get("operation_id") or "operation-1"),
        }


def _app(gateway: _Gateway, monkeypatch, worker_gateway: _WorkerGateway | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(TESTING=True, AGENT_TOKEN="internal-workflow-test-token")
    app.register_blueprint(workflow_runtime_internal_bp)
    monkeypatch.setattr(
        "agent.routes.workflow_runtime_internal.get_workflow_hub_task_gateway_service",
        lambda: gateway,
    )
    monkeypatch.setattr(
        "agent.routes.workflow_runtime_internal.get_workflow_worker_gateway_service",
        lambda: worker_gateway or _WorkerGateway(),
    )
    return app


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer internal-workflow-test-token"}


def test_internal_gateway_rejects_unauthenticated_requests(monkeypatch) -> None:
    client = _app(_Gateway(), monkeypatch).test_client()

    response = client.post("/api/internal/workflow-runtime/tasks", json={})

    assert response.status_code == 401


def test_internal_gateway_accepts_json_body_without_query_payload(monkeypatch) -> None:
    gateway = _Gateway()
    client = _app(gateway, monkeypatch).test_client()
    body = {
        "schema": HUB_TASK_COMMAND_SCHEMA,
        "command": "submit",
        "operation_id": "operation-2",
    }

    response = client.post(
        "/api/internal/workflow-runtime/tasks",
        json=body,
        headers=_headers(),
    )

    assert response.status_code == 202
    assert response.get_json()["data"]["operation_id"] == "operation-2"
    assert gateway.calls == [("submit", body)]


def test_internal_gateway_requires_operation_binding_on_reads(monkeypatch) -> None:
    gateway = _Gateway()
    client = _app(gateway, monkeypatch).test_client()

    missing = client.get(
        "/api/internal/workflow-runtime/tasks/hub-task-1",
        headers=_headers(),
    )
    found = client.get(
        "/api/internal/workflow-runtime/tasks/hub-task-1?operation_id=operation-1",
        headers=_headers(),
    )

    assert missing.status_code == 400
    assert missing.get_json()["data"]["reason_code"] == "workflow_operation_id_required"
    assert found.status_code == 200
    assert gateway.calls == [("get", ("hub-task-1", "operation-1"))]


def test_internal_gateway_supports_body_only_status_payload_and_retry_commands(monkeypatch) -> None:
    gateway = _Gateway()
    client = _app(gateway, monkeypatch).test_client()
    status_body = {
        "schema": HUB_TASK_COMMAND_SCHEMA,
        "command": "status",
        "operation_id": "operation-1",
    }
    payload_body = {**status_body, "command": "payload"}
    retry_body = {
        "schema": HUB_TASK_COMMAND_SCHEMA,
        "command": "consume_retry",
        "operation_id": "operation-1",
        "retry_id": "retry-1",
        "retry_category": "temporal_activity",
    }

    status = client.post(
        "/api/internal/workflow-runtime/tasks/hub-task-1/commands",
        json=status_body,
        headers=_headers(),
    )
    payload = client.post(
        "/api/internal/workflow-runtime/tasks/hub-task-1/commands",
        json=payload_body,
        headers=_headers(),
    )
    retry = client.post(
        "/api/internal/workflow-runtime/retries",
        json=retry_body,
        headers=_headers(),
    )

    assert status.status_code == payload.status_code == retry.status_code == 200
    assert gateway.calls == [
        ("get", ("hub-task-1", "operation-1")),
        ("payload", ("hub-task-1", "operation-1")),
        ("retry", retry_body),
    ]


def test_internal_gateway_rejects_oversized_and_unsupported_commands(monkeypatch) -> None:
    gateway = _Gateway()
    client = _app(gateway, monkeypatch).test_client()

    oversized = client.post(
        "/api/internal/workflow-runtime/tasks",
        data="x" * (262_144 + 1),
        headers={**_headers(), "Content-Type": "application/json"},
    )
    unsupported = client.post(
        "/api/internal/workflow-runtime/tasks/hub-task-1/commands",
        json={
            "schema": HUB_TASK_COMMAND_SCHEMA,
            "command": "delegate-to-worker",
            "operation_id": "operation-1",
        },
        headers=_headers(),
    )

    assert oversized.status_code == 413
    assert unsupported.status_code == 422
    assert unsupported.get_json()["data"]["reason_code"] == "workflow_hub_task_command_unsupported"
    assert gateway.calls == []


def test_internal_worker_decision_is_authenticated_and_body_only(monkeypatch) -> None:
    worker_gateway = _WorkerGateway()
    client = _app(_Gateway(), monkeypatch, worker_gateway).test_client()
    body = {
        "schema": WORKFLOW_WORKER_COMMAND_SCHEMA,
        "command": "authorize_tool",
        "binding": {"tenant_id": "tenant-1"},
        "operation_id": "operation-1",
    }

    unauthorized = client.post(
        "/api/internal/workflow-runtime/worker-commands",
        json=body,
    )
    accepted = client.post(
        "/api/internal/workflow-runtime/worker-commands",
        json=body,
        headers=_headers(),
    )

    assert unauthorized.status_code == 401
    assert accepted.status_code == 200
    assert accepted.get_json()["data"]["allowed"] is True
    assert worker_gateway.calls == [body]
