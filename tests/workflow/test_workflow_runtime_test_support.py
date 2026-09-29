from unittest.mock import patch

import pytest
from flask import Flask

from agent.config import settings
from agent.routes.workflow_runtime_test_support import (
    COMPOSE_E2E_PROJECT_ID,
    register_workflow_runtime_test_support,
    workflow_runtime_test_support_bp,
)
from agent.services.user_session_tokens import issue_user_access_token
from agent.services.workflow_runtime.execution_plan import ExecutionPlan
from agent.services.workflow_runtime_compose_e2e_support import (
    ComposeE2ERuntimeReleaseAdmission,
)
from agent.services.workflow_runtime_rollout_service import (
    InMemoryWorkflowRolloutPolicyStore,
    WorkflowRolloutScope,
)


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(workflow_runtime_test_support_bp)
    return app.test_client()


def _headers(*, role: str = "admin", username: str | None = None) -> dict[str, str]:
    token = issue_user_access_token(username=username or settings.initial_admin_user, role=role)
    return {"Authorization": f"Bearer {token}"}


def _enable_compose_e2e(monkeypatch) -> None:
    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "auth_test_endpoints_enabled", True)
    monkeypatch.setattr(settings, "workflow_runtime_test_context", "compose-e2e")


def test_native_rollout_support_is_not_found_when_test_endpoints_are_disabled(
    client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "auth_test_endpoints_enabled", False)
    monkeypatch.setattr(settings, "workflow_runtime_test_context", "")
    response = client.post(
        "/test/workflow-runtime/native-rollout",
        json={"project_id": COMPOSE_E2E_PROJECT_ID},
    )
    assert response.status_code == 404


def test_native_health_support_is_bounded_to_exact_compose_context(
    client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "auth_test_endpoints_enabled", False)
    monkeypatch.setattr(settings, "workflow_runtime_test_context", "")
    assert client.get("/test/workflow-runtime/native-health").status_code == 404

    _enable_compose_e2e(monkeypatch)
    response = client.get("/test/workflow-runtime/native-health")
    assert response.status_code == 200
    assert response.json == {
        "schema": "ananta.workflow_runtime_test_health.v1",
        "runtime_id": "ananta-native",
        "runtime_version": "1.0.0",
        "status": "ready",
        "ready": True,
    }


def test_compose_release_admission_is_synthetic_and_exactly_scoped(monkeypatch) -> None:
    _enable_compose_e2e(monkeypatch)
    admission = ComposeE2ERuntimeReleaseAdmission()
    plan = ExecutionPlan.from_mapping(
        {
            "tenant_id": "admin",
            "plan_id": "chat-test-plan",
            "workflow_id": "chat-test",
            "policy_version": "compose-e2e-native-v1",
            "nodes": [{"id": "one"}],
            "capabilities": [],
            "metadata": {
                "workflow_rollout_scope": {
                    "project_id": COMPOSE_E2E_PROJECT_ID,
                }
            },
        }
    )

    assert admission.evaluate(
        plan=plan,
        runtime_id="ananta-native",
        runtime_version="1.0.0",
        required_capabilities=frozenset({"audit", "authorization"}),
    ) == (True, "runtime_release_compose_e2e_test_fixture")
    assert admission.evaluate(
        plan=plan,
        runtime_id="temporal",
        runtime_version="1.0.0",
        required_capabilities=frozenset({"audit"}),
    ) == (False, "runtime_release_compose_e2e_scope_denied")
    assert admission.evaluate(
        plan=plan,
        runtime_id="ananta-native",
        runtime_version="1.0.0",
        required_capabilities=frozenset({"audit", "tool_execution"}),
    ) == (False, "runtime_release_compose_e2e_scope_denied")

    monkeypatch.setattr(settings, "workflow_runtime_test_context", "")
    assert admission.evaluate(
        plan=plan,
        runtime_id="ananta-native",
        runtime_version="1.0.0",
        required_capabilities=frozenset({"audit"}),
    ) == (False, "runtime_release_compose_e2e_scope_denied")


def test_native_rollout_support_is_not_registered_without_exact_context(monkeypatch) -> None:
    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "auth_test_endpoints_enabled", True)
    monkeypatch.setattr(settings, "workflow_runtime_test_context", "native-production")
    app = Flask(__name__)
    assert register_workflow_runtime_test_support(app) is False
    assert all("native-rollout" not in str(rule) for rule in app.url_map.iter_rules())


def test_native_rollout_support_requires_hub_admin(client, monkeypatch) -> None:
    _enable_compose_e2e(monkeypatch)
    response = client.post(
        "/test/workflow-runtime/native-rollout",
        json={"project_id": COMPOSE_E2E_PROJECT_ID},
        headers=_headers(role="user"),
    )
    assert response.status_code == 403
    assert response.json["reason_code"] == "admin_required"


def test_native_rollout_support_rejects_non_initial_admin(client, monkeypatch) -> None:
    _enable_compose_e2e(monkeypatch)
    response = client.post(
        "/test/workflow-runtime/native-rollout",
        json={"project_id": COMPOSE_E2E_PROJECT_ID},
        headers=_headers(username="other-admin"),
    )
    assert response.status_code == 403
    assert response.json["reason_code"] == "admin_required"


def test_native_rollout_support_accepts_only_fixed_project_contract(
    client,
    monkeypatch,
) -> None:
    _enable_compose_e2e(monkeypatch)
    response = client.post(
        "/test/workflow-runtime/native-rollout",
        json={"project_id": COMPOSE_E2E_PROJECT_ID, "allowed_runtimes": ["temporal"]},
        headers=_headers(),
    )
    assert response.status_code == 400
    assert response.json["reason_code"] == "test_rollout_contract_invalid"


def test_native_rollout_support_rejects_arbitrary_project_scope(client, monkeypatch) -> None:
    _enable_compose_e2e(monkeypatch)
    response = client.post(
        "/test/workflow-runtime/native-rollout",
        json={"project_id": "another-project"},
        headers=_headers(),
    )
    assert response.status_code == 422
    assert response.json["reason_code"] == "test_rollout_scope_invalid"


def test_native_rollout_support_provisions_idempotent_isolated_policy(
    client,
    monkeypatch,
) -> None:
    _enable_compose_e2e(monkeypatch)
    store = InMemoryWorkflowRolloutPolicyStore()
    with patch(
        "agent.routes.workflow_runtime_test_support._rollout_store",
        return_value=store,
    ):
        first = client.post(
            "/test/workflow-runtime/native-rollout",
            json={"project_id": COMPOSE_E2E_PROJECT_ID},
            headers=_headers(),
        )
        replay = client.post(
            "/test/workflow-runtime/native-rollout",
            json={"project_id": COMPOSE_E2E_PROJECT_ID},
            headers=_headers(),
        )

    assert first.status_code == 201
    assert replay.status_code == 200
    assert first.json["runtime_id"] == "ananta-native"
    assert replay.json["revision"] == 1
    stored = store.get(WorkflowRolloutScope(project_id=COMPOSE_E2E_PROJECT_ID))
    assert stored is not None
    assert stored.policy.mode == "live"
    assert stored.policy.allowed_runtimes == ("ananta-native",)
