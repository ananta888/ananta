from sqlmodel import Session, delete, select

from agent.database import engine
from agent.db_models import AgentInfoDB, RoleDB, TaskDB, TeamDB, TeamMemberDB, TeamTypeDB, TeamTypeRoleLink, TemplateDB
from agent.repository import agent_repo, role_repo, team_repo, team_type_repo


def test_team_role_validation(client):
    # Setup Admin Login
    response = client.post("/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200
    admin_token = response.json["data"]["access_token"]

    # Setup: Create TeamType, Role, and link them
    with Session(engine) as session:
        # Clear existing data to avoid conflicts
        session.exec(delete(TeamMemberDB))
        session.exec(delete(TeamDB))
        session.exec(delete(TeamTypeRoleLink))
        session.exec(delete(TeamTypeDB))
        session.exec(delete(RoleDB))
        session.exec(delete(AgentInfoDB))
        session.commit()

    tt = TeamTypeDB(name="SpecialType", description="A special team type")
    team_type_repo.save(tt)

    r_allowed = RoleDB(name="AllowedRole")
    role_repo.save(r_allowed)

    r_disallowed = RoleDB(name="DisallowedRole")
    role_repo.save(r_disallowed)

    # Link allowed role to team type
    with Session(engine) as session:
        link = TeamTypeRoleLink(team_type_id=tt.id, role_id=r_allowed.id)
        session.add(link)
        session.commit()

    agent = AgentInfoDB(url="http://agent1", name="Agent 1")
    agent_repo.save(agent)

    team = TeamDB(name="MyTeam", team_type_id=tt.id)
    team_repo.save(team)

    # Test 1: Assign allowed role (should succeed)
    response = client.post(
        f"/teams/{team.id}",
        json={"members": [{"agent_url": agent.url, "role_id": r_allowed.id}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    # Update: the endpoint is PATCH /teams/<team_id> based on routes/teams.py

    response = client.patch(
        f"/teams/{team.id}",
        json={"members": [{"agent_url": agent.url, "role_id": r_allowed.id}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200

    # Test 2: Assign disallowed role (should fail)
    response = client.patch(
        f"/teams/{team.id}",
        json={"members": [{"agent_url": agent.url, "role_id": r_disallowed.id}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Derzeit wird es wohl 200 zurückgeben, da die Validierung noch fehlt.
    # Wir wollen, dass es fehlschlägt.
    assert response.status_code == 400
    assert response.json["message"] == "invalid_role_for_team_type"


def test_team_member_template_validation(client):
    response = client.post("/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200
    admin_token = response.json["data"]["access_token"]

    with Session(engine) as session:
        session.exec(delete(TeamMemberDB))
        session.exec(delete(TaskDB))
        session.exec(delete(TeamDB))
        session.exec(delete(TeamTypeRoleLink))
        session.exec(delete(TeamTypeDB))
        session.exec(delete(RoleDB))
        session.exec(delete(AgentInfoDB))
        session.exec(delete(TemplateDB))
        session.commit()

    tt = TeamTypeDB(name="TemplateType", description="Template team type")
    team_type_repo.save(tt)

    role = RoleDB(name="RoleWithTemplate")
    role_repo.save(role)

    with Session(engine) as session:
        link = TeamTypeRoleLink(team_type_id=tt.id, role_id=role.id)
        session.add(link)
        session.commit()

    agent = AgentInfoDB(url="http://agent2", name="Agent 2")
    agent_repo.save(agent)

    team = TeamDB(name="TeamTemplate", team_type_id=tt.id)
    team_repo.save(team)

    # Invalid template should fail
    response = client.patch(
        f"/teams/{team.id}",
        json={"members": [{"agent_url": agent.url, "role_id": role.id, "custom_template_id": "missing"}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404
    assert response.json["message"] == "template_not_found"


def test_delete_team_with_members_cleans_members_first(client):
    response = client.post("/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200
    admin_token = response.json["data"]["access_token"]

    with Session(engine) as session:
        session.exec(delete(TeamMemberDB))
        session.exec(delete(TeamDB))
        session.exec(delete(TeamTypeRoleLink))
        session.exec(delete(TeamTypeDB))
        session.exec(delete(RoleDB))
        session.exec(delete(AgentInfoDB))
        session.commit()

    team_type = TeamTypeDB(name="DeleteType", description="Delete flow")
    team_type_repo.save(team_type)

    role = RoleDB(name="DeleteRole")
    role_repo.save(role)

    with Session(engine) as session:
        session.add(TeamTypeRoleLink(team_type_id=team_type.id, role_id=role.id))
        session.commit()

    agent = AgentInfoDB(url="http://agent-delete", name="Agent Delete")
    agent_repo.save(agent)

    team = TeamDB(name="DeleteTeam", team_type_id=team_type.id)
    team_repo.save(team)

    patch_res = client.patch(
        f"/teams/{team.id}",
        json={"members": [{"agent_url": agent.url, "role_id": role.id}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert patch_res.status_code == 200

    with Session(engine) as session:
        session.add(TaskDB(id="team-delete-task", title="Team task", status="todo", team_id=team.id))
        session.commit()

    delete_res = client.delete(
        f"/teams/{team.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert delete_res.status_code == 200

    with Session(engine) as session:
        assert session.get(TeamDB, team.id) is None
        members = session.exec(select(TeamMemberDB).where(TeamMemberDB.team_id == team.id)).all()
        task = session.get(TaskDB, "team-delete-task")
    assert members == []
    assert task is not None and task.team_id is None
