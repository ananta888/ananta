from sqlmodel import Session, delete

from agent.database import engine
from agent.db_models import AgentInfoDB, RoleDB, TeamDB, TeamMemberDB
from agent.repository import agent_repo, role_repo
from tests.support import admin_login_token as _login_admin


def test_team_member_display_name_can_be_set(client):
    admin_token = _login_admin(client)
    with Session(engine) as session:
        session.exec(delete(TeamMemberDB))
        session.exec(delete(TeamDB))
        session.commit()

    role = RoleDB(name="Display Role")
    role_repo.save(role)
    agent = AgentInfoDB(url="http://display-agent", name="Display Agent")
    agent_repo.save(agent)

    created = client.post(
        "/teams",
        json={"name": "Display Team", "members": [{"agent_url": agent.url, "role_id": role.id}]},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert created.status_code == 201

    teams = client.get("/teams", headers={"Authorization": f"Bearer {admin_token}"}).json["data"]
    team = next(item for item in teams if item["name"] == "Display Team")
    member = team["members"][0]
    assert member["display_name"] is None

    response = client.patch(
        f"/teams/{team['id']}/members/{member['id']}",
        json={"display_name": "Nova"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json["data"]["display_name"] == "Nova"

    teams = client.get("/teams", headers={"Authorization": f"Bearer {admin_token}"}).json["data"]
    team = next(item for item in teams if item["name"] == "Display Team")
    assert team["members"][0]["display_name"] == "Nova"
