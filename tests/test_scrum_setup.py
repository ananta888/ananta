import pytest
from sqlmodel import Session, delete, select
from agent.database import engine
from agent.db_models import (
    TeamDB,
    TeamMemberDB,
    TeamTypeDB,
    TeamTypeRoleLink,
    RoleDB,
    TemplateDB,
    TaskDB,
)


def _login_admin(client):
    response = client.post("/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200
    return response.json["data"]["access_token"]


def _clear_team_data():
    with Session(engine) as session:
        session.exec(delete(TaskDB))
        session.exec(delete(TeamMemberDB))
        session.exec(delete(TeamDB))
        session.exec(delete(TeamTypeRoleLink))
        session.exec(delete(TeamTypeDB))
        session.exec(delete(RoleDB))
        session.exec(delete(TemplateDB))
        session.commit()


def test_team_types_seed_defaults(client):
    _clear_team_data()
    admin_token = _login_admin(client)

    response = client.get("/teams/types", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    types = response.json["data"]
    names = {t["name"] for t in types}

    assert "Scrum" in names
    assert "Kanban" in names
    scrum_type = next(t for t in types if t["name"] == "Scrum")
    assert scrum_type.get("role_ids")


def test_setup_scrum_creates_tasks(client):
    _clear_team_data()
    admin_token = _login_admin(client)
    team_name = "Scrum Team Test"

    response = client.post(
        "/teams/setup-scrum",
        json={"name": team_name},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 201
    assert response.json["status"] == "success"
    assert response.json["data"]["team"]["name"] == team_name

    expected_titles = {
        "Scrum Backlog",
        "Sprint Board Setup",
        "Burndown Chart",
        "Roadmap",
        "Setup & Usage Instructions",
    }
    with Session(engine) as session:
        tasks = session.exec(
            select(TaskDB).where(TaskDB.title.startswith(f"{team_name}:"))
        ).all()
    found_titles = {t.title.replace(f"{team_name}: ", "") for t in tasks}
    assert expected_titles.issubset(found_titles)
