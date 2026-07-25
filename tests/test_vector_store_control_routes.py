from __future__ import annotations

from types import SimpleNamespace

from flask import Flask

from agent.auth import generate_token
from agent.config import settings
from agent.routes.vector_store_control import vector_store_control_bp


def _app() -> Flask:
    app = Flask(__name__)
    app.config.update(TESTING=True, AGENT_TOKEN=None)
    app.register_blueprint(vector_store_control_bp)
    return app


def _headers(role: str, **claims) -> dict[str, str]:
    token = generate_token(
        {"sub": "operator-a", "role": role, **claims},
        settings.secret_key,
    )
    return {"Authorization": f"Bearer {token}"}


def test_vector_store_control_requires_admin_and_workspace_scope(monkeypatch) -> None:
    service = SimpleNamespace(
        submit=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("must not submit")
        )
    )
    monkeypatch.setattr(
        "agent.routes.vector_store_control.get_vector_index_task_service",
        lambda: service,
    )
    client = _app().test_client()
    body = {
        "operation": "delete",
        "workspace_id": "workspace-a",
        "repository_id": "repo-a",
        "idempotency_key": "request-1234",
        "payload": {"point_ids": ["1"]},
    }

    assert client.post("/api/vector-store/index-tasks", json=body).status_code == 401
    forbidden = client.post(
        "/api/vector-store/index-tasks",
        json=body,
        headers=_headers("admin", workspace_id="workspace-b"),
    )
    assert forbidden.status_code == 403
    assert forbidden.get_json()["reason_code"] == "vector_store_workspace_forbidden"


def test_global_admin_submits_typed_trusted_scope(monkeypatch) -> None:
    captured: list[dict] = []
    service = SimpleNamespace(
        submit=lambda **kwargs: captured.append(kwargs)
        or {
            "job_id": "vector-index-a",
            "status": "queued",
            "scope": kwargs["trusted_scope"].to_dict(),
        }
    )
    monkeypatch.setattr(
        "agent.routes.vector_store_control.get_vector_index_task_service",
        lambda: service,
    )
    response = _app().test_client().post(
        "/api/vector-store/index-tasks",
        json={
            "operation": "delete",
            "workspace_id": "workspace-a",
            "repository_id": "repo-a",
            "idempotency_key": "request-1234",
            "payload": {"point_ids": ["1"]},
        },
        headers=_headers("system_admin"),
    )

    assert response.status_code == 202
    assert captured[0]["trusted_scope"].workspace_id == "workspace-a"
    assert captured[0]["operation"] == "delete"
