from __future__ import annotations

from agent.services.scrum_architecture_loop_service import ScrumArchitectureLoopService
from agent.services.scrum_continuous_improvement_query_service import (
    ScrumContinuousImprovementQueryService,
)
from agent.services.scrum_retrospective_service import ScrumRetrospectiveService
from agent.services.scrum_sprint_control_service import ScrumSprintControlService
from agent.services.scrum_state_store import ScrumStateStore


def _wire(app, tmp_path):
    store = ScrumStateStore(tmp_path / "scrum-api.sqlite3")
    architecture = ScrumArchitectureLoopService(store)
    sprints = ScrumSprintControlService(store, architecture)
    app.extensions["scrum_architecture_loop_service"] = architecture
    app.extensions["scrum_sprint_control_service"] = sprints
    app.extensions["scrum_retrospective_service"] = ScrumRetrospectiveService(store, sprints)
    app.extensions["scrum_continuous_improvement_query_service"] = ScrumContinuousImprovementQueryService(store)


def test_scrum_api_exposes_versioned_architecture_and_sprint_commands(
    app,
    client,
    admin_auth_header,
    tmp_path,
):
    _wire(app, tmp_path)
    created = client.post(
        "/api/scrum/architecture/baselines",
        headers=admin_auth_header,
        json={
            "scope_id": "project-1",
            "revision_id": "arch-1",
            "author_id": "architecture-agent",
            "parent_revision_id": None,
            "target_architecture": {"style": "hub-worker"},
            "guardrails": [{"guardrail_id": "hub", "rule": "Hub owns tasks", "scopes": []}],
            "adr_refs": ["ADR-1"],
        },
    )
    assert created.status_code == 200
    activated = client.post(
        "/api/scrum/architecture/baselines/arch-1/activate",
        headers=admin_auth_header,
        json={
            "reviewer_id": "automated-reviewer-agent",
            "checks": {
                "scope": True,
                "security": True,
                "compatibility": True,
                "migration": True,
                "evidence": True,
            },
            "evidence_refs": ["review-1"],
        },
    )
    assert activated.status_code == 200
    planned = client.post(
        "/api/scrum/sprints",
        headers=admin_auth_header,
        json={
            "sprint_id": "sprint-1",
            "scope_id": "project-1",
            "sequence": 1,
            "predecessor_sprint_id": None,
            "product_goal": "Deliver product",
            "sprint_goal": "Deliver governed API",
            "task_ids": ["task-1"],
            "sprint_scope": ["api"],
            "boundary": {"task_count": 1},
            "planned_at": "2026-08-28T10:00:00Z",
        },
    )
    assert planned.status_code == 200
    sprint = client.get("/api/scrum/sprints/sprint-1", headers=admin_auth_header)
    assert sprint.status_code == 200
    assert sprint.get_json()["data"]["architecture_handoff"]["architecture_revision_id"] == "arch-1"
    overview = client.get(
        "/api/scrum/overview?scope_id=project-1",
        headers=admin_auth_header,
    )
    assert overview.status_code == 200
    assert overview.get_json()["data"]["counts"]["sprints"] == 1


def test_scrum_api_returns_closed_validation_error_without_approval_wait(
    app,
    client,
    admin_auth_header,
    tmp_path,
):
    _wire(app, tmp_path)
    response = client.post(
        "/api/scrum/sprints",
        headers=admin_auth_header,
        json={
            "sprint_id": "invalid",
            "scope_id": "project-1",
            "sequence": 0,
            "predecessor_sprint_id": None,
            "product_goal": "Product",
            "sprint_goal": "Sprint",
            "task_ids": ["task-1"],
            "sprint_scope": ["api"],
            "boundary": {"task_count": 1},
            "planned_at": "2026-08-28T10:00:00Z",
        },
    )
    assert response.status_code == 422
    assert "sprint_sequence_invalid" in response.get_json()["message"]


def test_scrum_mutations_require_hub_admin_authority(
    app,
    client,
    user_auth_header,
    tmp_path,
):
    _wire(app, tmp_path)
    response = client.post(
        "/api/scrum/architecture/baselines",
        headers=user_auth_header,
        json={},
    )
    assert response.status_code == 403
