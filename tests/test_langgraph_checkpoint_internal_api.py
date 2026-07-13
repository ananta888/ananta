from __future__ import annotations

from flask import Flask

from agent.routes.langgraph_checkpoint_internal import (
    langgraph_checkpoint_internal_bp,
)
from ananta_contracts.langgraph_checkpoint import (
    LANGGRAPH_CHECKPOINT_COMMAND_SCHEMA,
    LANGGRAPH_CHECKPOINT_RESPONSE_SCHEMA,
)


class _Gateway:
    def __init__(self) -> None:
        self.commands: list[dict] = []

    def execute(self, body: dict) -> dict:
        self.commands.append(body)
        return {"schema": LANGGRAPH_CHECKPOINT_RESPONSE_SCHEMA, "snapshot": None}


def _app(gateway: _Gateway, monkeypatch) -> Flask:
    app = Flask(__name__)
    app.config.update(TESTING=True, AGENT_TOKEN="internal-langgraph-test-token")
    app.register_blueprint(langgraph_checkpoint_internal_bp)
    monkeypatch.setattr(
        "agent.routes.langgraph_checkpoint_internal.get_langgraph_checkpoint_gateway_service",
        lambda: gateway,
    )
    return app


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer internal-langgraph-test-token"}


def test_checkpoint_api_is_authenticated_and_post_only(monkeypatch) -> None:
    gateway = _Gateway()
    client = _app(gateway, monkeypatch).test_client()

    missing_auth = client.post("/api/internal/workflow-runtime/langgraph/checkpoints", json={})
    get_request = client.get("/api/internal/workflow-runtime/langgraph/checkpoints", headers=_headers())

    assert missing_auth.status_code == 401
    assert get_request.status_code == 405
    assert gateway.commands == []


def test_checkpoint_api_uses_body_and_rejects_query_transport(monkeypatch) -> None:
    gateway = _Gateway()
    client = _app(gateway, monkeypatch).test_client()
    body = {
        "schema": LANGGRAPH_CHECKPOINT_COMMAND_SCHEMA,
        "operation": "get",
        "binding": {},
        "config": {},
    }

    accepted = client.post(
        "/api/internal/workflow-runtime/langgraph/checkpoints",
        json=body,
        headers=_headers(),
    )
    query = client.post(
        "/api/internal/workflow-runtime/langgraph/checkpoints?token=forbidden",
        json=body,
        headers=_headers(),
    )

    assert accepted.status_code == 200
    assert accepted.get_json()["data"]["schema"] == LANGGRAPH_CHECKPOINT_RESPONSE_SCHEMA
    assert query.status_code == 400
    assert query.get_json()["data"]["reason_code"] == ("langgraph_checkpoint_query_transport_forbidden")
    assert gateway.commands == [body]


def test_checkpoint_api_rejects_oversized_or_non_json_commands(monkeypatch) -> None:
    gateway = _Gateway()
    client = _app(gateway, monkeypatch).test_client()

    oversized = client.post(
        "/api/internal/workflow-runtime/langgraph/checkpoints",
        data="x" * 262_145,
        headers={**_headers(), "Content-Type": "application/json"},
    )
    invalid = client.post(
        "/api/internal/workflow-runtime/langgraph/checkpoints",
        data="[]",
        headers={**_headers(), "Content-Type": "application/json"},
    )

    assert oversized.status_code == 413
    assert invalid.status_code == 400
    assert gateway.commands == []
