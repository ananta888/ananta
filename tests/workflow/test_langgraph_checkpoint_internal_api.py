from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from flask import Flask

from agent.auth import generate_token
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
        self.identities: list[dict[str, str]] = []

    def execute(self, body: dict, **identity: str) -> dict:
        self.commands.append(body)
        self.identities.append(dict(identity))
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


def test_checkpoint_api_rejects_user_jwt(monkeypatch) -> None:
    secret = "langgraph-user-jwt-test-secret-with-at-least-32-bytes"
    monkeypatch.setattr("agent.auth.settings.secret_key", secret)
    gateway = _Gateway()
    client = _app(gateway, monkeypatch).test_client()
    user_token = generate_token(
        {"sub": "admin-user", "tenant_id": "tenant-1", "role": "admin"},
        secret,
    )

    response = client.post(
        "/api/internal/workflow-runtime/langgraph/checkpoints",
        json={},
        headers={"Authorization": f"Bearer {user_token}"},
    )

    assert response.status_code == 403
    assert response.get_json()["data"]["reason_code"] == "workflow_service_auth_required"
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


def test_strict_checkpoint_route_passes_authenticated_worker_identity(
    monkeypatch,
    tmp_path,
) -> None:
    worker_token = "langgraph-service-token-0123456789abcdef"
    bootstrap = "langgraph-bootstrap-token-0123456789abcdef"
    hub_token = "hub-service-token-0123456789abcdefghijkl"
    keyring = tmp_path / "registration-keyring.json"
    keyring.write_text(
        json.dumps(
            {
                "schema": "ananta.workflow-worker-registration-keyring.v1",
                "workers": {
                    "langgraph-worker-1": {
                        "worker_url": "http://langgraph-worker:5000",
                        "registration_token": bootstrap,
                        "service_token_sha256": hashlib.sha256(
                            worker_token.encode("utf-8")
                        ).hexdigest(),
                        "session_signing_key_sha256": hashlib.sha256(
                            b"langgraph-session-signing-key-0123456789abcdef"
                        ).hexdigest(),
                        "allowed_capabilities": [
                            "workflow.adapter.langgraph"
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    keyring.chmod(0o440)
    gateway = _Gateway()
    app = _app(gateway, monkeypatch)
    app.config.update(
        AGENT_TOKEN=hub_token,
        ANANTA_WORKFLOW_REQUIRE_REGISTERED_WORKER_AUTH=True,
        ANANTA_WORKFLOW_WORKER_REGISTRATION_KEYRING_FILE=str(keyring),
    )
    app.extensions["repository_registry"] = SimpleNamespace(
        agent_repo=SimpleNamespace(
            get_all=lambda: [
                SimpleNamespace(
                    name="langgraph-worker-1",
                    url="http://langgraph-worker:5000",
                    token=worker_token,
                    role="worker",
                    status="online",
                    capabilities=["workflow.adapter.langgraph"],
                    authorized_capabilities=["workflow.adapter.langgraph"],
                    registration_validated=True,
                    registration_provenance="strict_registration_keyring_v1",
                )
            ]
        )
    )
    monkeypatch.setattr("agent.auth.log_audit", lambda *_args, **_kwargs: None)

    response = app.test_client().post(
        "/api/internal/workflow-runtime/langgraph/checkpoints",
        json={
            "schema": LANGGRAPH_CHECKPOINT_COMMAND_SCHEMA,
            "operation": "get",
            "binding": {},
            "config": {},
        },
        headers={
            "Authorization": f"Bearer {worker_token}",
            "X-Ananta-Worker-ID": "langgraph-worker-1",
            "X-Ananta-Worker-URL": "http://langgraph-worker:5000",
        },
    )

    assert response.status_code == 200
    assert gateway.identities == [
        {
            "authenticated_worker_id": "langgraph-worker-1",
            "authenticated_worker_url": "http://langgraph-worker:5000",
        }
    ]
