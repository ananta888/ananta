import pytest
from agent.db_models import TeamDB, TeamTypeDB, RoleDB, TeamMemberDB, TeamTypeRoleLink, AgentInfoDB, TemplateDB
from agent.repository import team_repo, team_type_repo, role_repo, team_member_repo, agent_repo, template_repo
from agent.database import engine
from sqlmodel import Session

def test_create_and_list_team(client):
    # Setup Admin Login
    response = client.post("/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200
    admin_token = response.json["access_token"]
    
    # Clean up
    with Session(engine) as session:
        session.query(TeamMemberDB).delete()
        session.query(TeamDB).delete()
        session.commit()

    # Create Team
    payload = {
        "name": "New Test Team",
        "description": "Test description",
        "members": []
    }
    response = client.post("/teams", json=payload, headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 201
    created_team = response.json
    assert created_team["name"] == "New Test Team"
    
    # List Teams
    response = client.get("/teams", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    teams = response.json
    assert any(t["id"] == created_team["id"] for t in teams)

def test_create_team_with_members(client):
    # Setup Admin Login
    response = client.post("/login", json={"username": "admin", "password": "admin"})
    admin_token = response.json["access_token"]
    
    # Setup Role and Agent
    r = RoleDB(name="TestRole")
    role_repo.save(r)
    a = AgentInfoDB(url="http://test-agent", name="Test Agent")
    agent_repo.save(a)

    # Create Team with members
    payload = {
        "name": "Team with Members",
        "members": [{"agent_url": a.url, "role_id": r.id}]
    }
    response = client.post("/teams", json=payload, headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 201
    
    # List Teams and check members
    response = client.get("/teams", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    teams = response.json
    team = next(t for t in teams if t["name"] == "Team with Members")
    assert len(team["members"]) == 1
    assert team["members"][0]["agent_url"] == a.url
