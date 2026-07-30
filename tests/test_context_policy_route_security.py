from __future__ import annotations

from types import SimpleNamespace

from flask import Flask

from agent.auth import generate_token
from agent.config import settings
from agent.routes.context_policy import context_policy_bp


def _app() -> Flask:
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        AGENT_TOKEN="test-agent-token-with-sufficient-length-1234567890",
    )
    app.register_blueprint(context_policy_bp)
    return app


def _headers(role: str) -> dict[str, str]:
    token = generate_token(
        {
            "sub": f"{role}-operator",
            "role": role,
            "tenant_id": "tenant-a",
            "project_id": "project-a",
        },
        settings.secret_key,
    )
    return {"Authorization": f"Bearer {token}"}


def test_context_policy_management_is_fail_closed_admin_only(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "agent.routes.context_policy.policy_service",
        SimpleNamespace(
            get_latest_policy=lambda _policy_id: SimpleNamespace(
                tenant_id="tenant-a",
                project_id="project-a",
                owner_id="user-operator",
            )
        ),
    )
    client = _app().test_client()
    requests = (
        ("get", "/api/context-policy/policies", None),
        ("get", "/api/context-policy/policies/policy-a/latest", None),
        ("post", "/api/context-policy/validate", {"rules": []}),
        (
            "post",
            "/api/context-policy/policies",
            {"policy_id": "policy-a", "rules": []},
        ),
    )

    for method, path, body in requests:
        unauthenticated = getattr(client, method)(path, json=body)
        regular_user = getattr(client, method)(
            path,
            json=body,
            headers=_headers("user"),
        )

        assert unauthenticated.status_code == 401
        assert regular_user.status_code == 403


def test_admin_can_read_context_policy_projection(monkeypatch) -> None:
    record = SimpleNamespace(
        dict=lambda: {
            "policy_id": "policy-a",
            "project_id": "project-a",
            "version": 1,
        }
    )
    monkeypatch.setattr(
        "agent.routes.context_policy.policy_service",
        SimpleNamespace(list_policies=lambda **_kwargs: [record]),
    )

    response = _app().test_client().get(
        "/api/context-policy/policies?project_id=project-a",
        headers=_headers("admin"),
    )

    assert response.status_code == 200
    assert response.get_json()["data"] == [record.dict()]


def test_invalid_payload_never_leaks_exception_details(monkeypatch) -> None:
    secret_detail = "postgresql://operator:secret@database/policies"
    monkeypatch.setattr(
        "agent.routes.context_policy.policy_service",
        SimpleNamespace(
            validate_policy=lambda _policy: [],
            create_policy_record=lambda **_kwargs: (_ for _ in ()).throw(
                ValueError(secret_detail)
            ),
        ),
    )

    response = _app().test_client().post(
        "/api/context-policy/policies",
        json={"policy_id": "policy-a", "rules": []},
        headers=_headers("admin"),
    )

    assert response.status_code == 400
    assert response.get_json()["reason_code"] == "context_policy_payload_invalid"
    assert secret_detail not in response.get_data(as_text=True)
