from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import TaskDB, TeamDB, TeamMemberDB


def _login_admin(client):
    response = client.post("/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200
    return response.json["data"]["access_token"]


def test_seed_blueprints_are_listed(client):
    admin_token = _login_admin(client)

    response = client.get("/teams/blueprints", headers={"Authorization": f"Bearer {admin_token}"})

    assert response.status_code == 200
    blueprints = response.json["data"]
    names = {blueprint["name"] for blueprint in blueprints}
    assert {"Scrum", "Kanban", "Research", "Code-Repair", "Security-Review", "Release-Prep"}.issubset(names)

    scrum_blueprint = next(blueprint for blueprint in blueprints if blueprint["name"] == "Scrum")
    assert scrum_blueprint["is_seed"] is True
    assert {role["name"] for role in scrum_blueprint["roles"]} == {"Product Owner", "Scrum Master", "Developer"}
    assert any(artifact["title"] == "Scrum Backlog" for artifact in scrum_blueprint["artifacts"])

    research_blueprint = next(blueprint for blueprint in blueprints if blueprint["name"] == "Research")
    assert research_blueprint["is_seed"] is True
    assert {role["name"] for role in research_blueprint["roles"]} == {"Research Lead", "Source Analyst", "Reviewer"}
    policy_artifact = next((artifact for artifact in research_blueprint["artifacts"] if artifact["kind"] == "policy"), None)
    assert policy_artifact is not None
    assert (policy_artifact.get("payload") or {}).get("security_level") == "balanced"


def test_blueprint_crud_and_instantiate(client):
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}

    blueprints_response = client.get("/teams/blueprints", headers=auth_header)
    scrum_blueprint = next(blueprint for blueprint in blueprints_response.json["data"] if blueprint["name"] == "Scrum")
    developer_role = next(role for role in scrum_blueprint["roles"] if role["name"] == "Developer")

    create_response = client.post(
        "/teams/blueprints",
        json={
            "name": "Delivery Pod",
            "description": "Custom delivery team blueprint",
            "base_team_type_name": "Kanban",
            "roles": [
                {
                    "name": "Delivery Lead",
                    "description": "Owns delivery coordination.",
                    "sort_order": 10,
                    "is_required": True,
                    "config": {"focus": "coordination"},
                }
            ],
            "artifacts": [
                {
                    "kind": "task",
                    "title": "Kickoff",
                    "description": "Start the team cadence.",
                    "sort_order": 10,
                    "payload": {"status": "todo", "priority": "High"},
                }
            ],
        },
        headers=auth_header,
    )
    assert create_response.status_code == 201
    created_blueprint = create_response.json["data"]
    assert created_blueprint["base_team_type_name"] == "Kanban"

    update_response = client.patch(
        f"/teams/blueprints/{created_blueprint['id']}",
        json={
            "description": "Updated delivery team blueprint",
            "roles": [
                {
                    "name": "Delivery Lead",
                    "description": "Owns delivery coordination and sequencing.",
                    "sort_order": 10,
                    "is_required": True,
                    "config": {"focus": "sequencing"},
                },
                {
                    "name": "Developer",
                    "description": "Builds the increment.",
                    "sort_order": 20,
                    "is_required": True,
                    "config": {"focus": "implementation"},
                },
            ],
        },
        headers=auth_header,
    )
    assert update_response.status_code == 200
    updated_blueprint = update_response.json["data"]
    assert updated_blueprint["description"] == "Updated delivery team blueprint"
    assert len(updated_blueprint["roles"]) == 2

    instantiate_response = client.post(
        f"/teams/blueprints/{scrum_blueprint['id']}/instantiate",
        json={
            "name": "Blueprint Team Alpha",
            "activate": True,
            "members": [
                {
                    "agent_url": "http://worker-dev",
                    "blueprint_role_id": developer_role["id"],
                }
            ],
        },
        headers=auth_header,
    )
    assert instantiate_response.status_code == 201
    team = instantiate_response.json["data"]["team"]
    assert team["blueprint_id"] == scrum_blueprint["id"]
    assert team["is_active"] is True
    assert team["blueprint_snapshot"]["name"] == "Scrum"

    with Session(engine) as session:
        persisted_team = session.get(TeamDB, team["id"])
        persisted_members = session.exec(select(TeamMemberDB).where(TeamMemberDB.team_id == team["id"])).all()
        persisted_tasks = session.exec(select(TaskDB).where(TaskDB.team_id == team["id"])).all()

    assert persisted_team is not None
    assert persisted_team.blueprint_id == scrum_blueprint["id"]
    assert persisted_team.blueprint_snapshot["name"] == "Scrum"
    assert len(persisted_members) == 1
    assert persisted_members[0].blueprint_role_id == developer_role["id"]
    assert any(task.title == "Blueprint Team Alpha: Scrum Backlog" for task in persisted_tasks)


def test_setup_scrum_uses_seed_blueprint(client):
    admin_token = _login_admin(client)

    response = client.post(
        "/teams/setup-scrum",
        json={"name": "Scrum via Blueprint"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 201
    team = response.json["data"]["team"]
    blueprint = response.json["data"]["blueprint"]
    assert blueprint["name"] == "Scrum"
    assert team["blueprint_id"] == blueprint["id"]

    with Session(engine) as session:
        persisted_team = session.get(TeamDB, team["id"])

    assert persisted_team is not None
    assert persisted_team.blueprint_snapshot["name"] == "Scrum"


def test_blueprint_validation_rejects_duplicate_role_names(client):
    admin_token = _login_admin(client)

    response = client.post(
        "/teams/blueprints",
        json={
            "name": "Broken Blueprint",
            "roles": [
                {"name": "Developer", "sort_order": 10, "is_required": True, "config": {}},
                {"name": "Developer", "sort_order": 20, "is_required": True, "config": {}},
            ],
            "artifacts": [],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 400
    assert response.json["message"] == "duplicate_blueprint_role_name"


def test_seed_research_blueprint_instantiation_materializes_tasks(client):
    admin_token = _login_admin(client)
    auth_header = {"Authorization": f"Bearer {admin_token}"}

    blueprints_response = client.get("/teams/blueprints", headers=auth_header)
    assert blueprints_response.status_code == 200
    research_blueprint = next(blueprint for blueprint in blueprints_response.json["data"] if blueprint["name"] == "Research")
    source_analyst_role = next(role for role in research_blueprint["roles"] if role["name"] == "Source Analyst")

    instantiate_response = client.post(
        f"/teams/blueprints/{research_blueprint['id']}/instantiate",
        json={
            "name": "Research Pod Alpha",
            "activate": False,
            "members": [
                {
                    "agent_url": "http://worker-research",
                    "blueprint_role_id": source_analyst_role["id"],
                }
            ],
        },
        headers=auth_header,
    )
    assert instantiate_response.status_code == 201
    team = instantiate_response.json["data"]["team"]
    assert team["blueprint_id"] == research_blueprint["id"]
    assert team["blueprint_snapshot"]["name"] == "Research"
    policy_artifacts = [a for a in (team["blueprint_snapshot"].get("artifacts") or []) if a.get("kind") == "policy"]
    assert policy_artifacts

    with Session(engine) as session:
        persisted_tasks = session.exec(select(TaskDB).where(TaskDB.team_id == team["id"])).all()

    assert any(task.title == "Research Pod Alpha: Research Intake" for task in persisted_tasks)
