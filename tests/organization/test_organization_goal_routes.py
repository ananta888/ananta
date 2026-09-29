from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from flask import Flask, g

from agent.models.organization_goal_models import OrganizationGoalCreateResult
from agent.routes import organization_goals as routes
from agent.services.organization_goal_application_service import OrganizationGoalApplicationError
from agent.services.project_access_authority import ProjectCapability


@pytest.fixture()
def app() -> Flask:
    application = Flask(__name__)
    application.register_blueprint(routes.organization_goals_bp)
    return application


class _GoalService:
    def __init__(self, *, replayed: bool = False, error: OrganizationGoalApplicationError | None = None) -> None:
        self.replayed = replayed
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> OrganizationGoalCreateResult:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return OrganizationGoalCreateResult(
            goal_id="orggoal-123",
            trace_id="goal-123",
            organization_id=kwargs["organization_id"],
            status="received",
            goal_kind="organization",
            replayed=self.replayed,
        )


def _scope():
    return SimpleNamespace(
        principal=SimpleNamespace(subject_id="operator-a"),
        tenant_id="tenant-a",
        project_id="project-a",
        organization_id="organization-a",
    )


def _install_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    def require_scope(organization_id: str, capability: ProjectCapability):
        assert organization_id == "organization-a"
        assert capability is ProjectCapability.MANAGE
        return _scope()

    monkeypatch.setattr(routes, "require_organization_scope", require_scope)


def test_blueprint_exposes_only_scoped_goal_intake(app: Flask) -> None:
    rules = {(rule.rule, tuple(sorted(rule.methods - {"HEAD", "OPTIONS"}))) for rule in app.url_map.iter_rules()}

    assert rules == {
        ("/static/<path:filename>", ("GET",)),
        ("/api/organizations/<organization_id>/goals", ("POST",)),
    }


def test_create_goal_uses_authoritative_scope_and_returns_201(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_scope(monkeypatch)
    service = _GoalService()
    app.extensions["organization_goal_application_service"] = service

    with app.test_request_context(
        "/api/organizations/organization-a/goals",
        method="POST",
        headers={"Idempotency-Key": "goal-create-key-1"},
        json={
            "goal": "  Research the HRM workbench  ",
            "summary": "  Produce a Category plan  ",
            "constraints": ["Ground every claim"],
            "acceptance_criteria": ["Conforms to todo.schema.json"],
        },
    ):
        g.user = {"credential_type": "caller-controlled-value-is-ignored"}
        response, status_code = routes.create_organization_goal.__wrapped__("organization-a")

    assert status_code == 201
    assert response.get_json() == {
        "status": "success",
        "data": {
            "goal_id": "orggoal-123",
            "trace_id": "goal-123",
            "organization_id": "organization-a",
            "status": "received",
            "goal_kind": "organization",
            "replayed": False,
        },
    }
    assert len(service.calls) == 1
    call = service.calls[0]
    assert call["organization_id"] == "organization-a"
    assert call["idempotency_key"] == "goal-create-key-1"
    assert call["command"].goal == "Research the HRM workbench"
    assert call["command"].summary == "Produce a Category plan"
    assert (
        call["principal"].principal_id,
        call["principal"].tenant_id,
        call["principal"].project_id,
        call["principal"].credential_type,
    ) == ("operator-a", "tenant-a", "project-a", "user")


def test_exact_replay_returns_200(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_scope(monkeypatch)
    app.extensions["organization_goal_application_service"] = _GoalService(replayed=True)

    with app.test_request_context(
        "/api/organizations/organization-a/goals",
        method="POST",
        headers={"Idempotency-Key": "goal-create-key-1"},
        json={"goal": "Research"},
    ):
        g.user = {"credential_type": "user"}
        response, status_code = routes.create_organization_goal.__wrapped__("organization-a")

    assert status_code == 200
    assert response.get_json()["data"]["replayed"] is True


@pytest.mark.parametrize(
    ("body", "headers", "expected_status", "expected_reason"),
    (
        (
            {"goal": "Research", "team_id": "caller-selected-team"},
            {"Idempotency-Key": "goal-create-key-1"},
            400,
            "organization_payload_fields_invalid",
        ),
        (
            {"goal": "Research"},
            {},
            400,
            "organization_idempotency_key_invalid",
        ),
        (
            {"goal": "   "},
            {"Idempotency-Key": "goal-create-key-1"},
            422,
            "organization_contract_invalid",
        ),
    ),
)
def test_goal_intake_fails_closed_before_service_write(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
    body: dict[str, Any],
    headers: dict[str, str],
    expected_status: int,
    expected_reason: str,
) -> None:
    _install_scope(monkeypatch)
    service = _GoalService()
    app.extensions["organization_goal_application_service"] = service

    with app.test_request_context(
        "/api/organizations/organization-a/goals",
        method="POST",
        headers=headers,
        json=body,
    ):
        g.user = {"credential_type": "user"}
        response, status_code = routes.create_organization_goal.__wrapped__("organization-a")

    assert status_code == expected_status
    assert response.get_json()["message"] == expected_reason
    assert service.calls == []


def test_application_denial_is_mapped_at_http_boundary(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_scope(monkeypatch)
    app.extensions["organization_goal_application_service"] = _GoalService(
        error=OrganizationGoalApplicationError(
            "organization_goal_not_found",
            public_status=404,
        )
    )

    with app.test_request_context(
        "/api/organizations/organization-a/goals",
        method="POST",
        headers={"Idempotency-Key": "goal-create-key-1"},
        json={"goal": "Research"},
    ):
        g.user = {"credential_type": "user"}
        response, status_code = routes.create_organization_goal.__wrapped__("organization-a")

    assert status_code == 404
    assert response.get_json()["message"] == "organization_goal_not_found"


def test_credential_classification_distinguishes_hub_service_and_worker(app: Flask) -> None:
    with app.test_request_context("/"):
        g.user = {}
        g.auth_payload = {"auth_mode": "agent_jwt"}
        assert routes._credential_type() == "hub_service"

    with app.test_request_context("/"):
        g.user = {"sub": "must-not-shadow-worker-identity"}
        g.auth_payload = {
            "auth_mode": "registered_worker_service_token",
            "token_use": "workflow_worker_service",
        }
        g.service_identity = {"worker_id": "worker-a"}
        assert routes._credential_type() == "worker"
