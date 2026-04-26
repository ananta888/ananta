from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError

from agent import repository
from agent.services.blueprint_planning_adapter import BlueprintPlanningAdapter


def _ensure_seed_blueprints(client) -> None:
    login = client.post("/login", json={"username": "admin", "password": "admin"})
    assert login.status_code == 200
    token = login.json["data"]["access_token"]
    response = client.get("/teams/blueprints", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200


def test_blueprint_planning_adapter_resolves_seed_blueprint_subtasks(app, client) -> None:
    _ensure_seed_blueprints(client)
    adapter = BlueprintPlanningAdapter()
    with app.app_context():
        resolution = adapter.resolve("TDD")

    assert resolution is not None
    assert resolution.degraded is False
    assert resolution.blueprint_name == "TDD"
    assert resolution.subtasks
    assert resolution.artifact_refs
    first = resolution.subtasks[0]
    assert first["blueprint_name"] == "TDD"
    assert first["blueprint_artifact_id"]
    assert isinstance(first.get("blueprint_role_hints"), list)


def test_blueprint_planning_adapter_resolves_fuzzy_goal_text(app, client) -> None:
    _ensure_seed_blueprints(client)
    adapter = BlueprintPlanningAdapter()
    with app.app_context():
        subtasks = adapter.resolve_subtasks("Bitte TDD blueprint fuer login bugfix ausfuehren")

    assert subtasks is not None
    assert len(subtasks) >= 1


def test_blueprint_planning_adapter_degrades_when_repo_unavailable(app, monkeypatch) -> None:
    adapter = BlueprintPlanningAdapter()

    def _raise_db_error():  # noqa: ANN202
        raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(repository.team_blueprint_repo, "get_all", _raise_db_error)
    with app.app_context():
        resolution = adapter.resolve("TDD")

    assert resolution is not None
    assert resolution.degraded is True
    assert resolution.subtasks == []
    assert "blueprint_repo_unavailable" in str(resolution.degraded_reason or "")
