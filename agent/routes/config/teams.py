import logging
from flask import Blueprint, request
from sqlmodel import Session, select
from agent.auth import check_auth, admin_required
from agent.common.errors import api_response
from agent.database import engine
from agent.db_models import TeamDB, TeamMemberDB, TeamTypeDB, RoleDB, TeamTypeRoleLink
from agent.models import TeamCreateRequest, TeamTypeCreateRequest, RoleCreateRequest
from agent.utils import validate_request

teams_bp = Blueprint("config_teams", __name__)

# Team Routes
@teams_bp.route("/teams", methods=["GET"])
@check_auth
def list_teams():
    with Session(engine) as session:
        teams = session.exec(select(TeamDB)).all()
        members = session.exec(select(TeamMemberDB)).all()
        members_by_team = {}
        for member in members:
            members_by_team.setdefault(member.team_id, []).append(member.model_dump())
        return api_response(
            data=[
                {
                    "id": team.id,
                    "name": team.name,
                    "description": team.description,
                    "team_type_id": team.team_type_id,
                    "blueprint_id": team.blueprint_id,
                    "is_active": team.is_active,
                    "role_templates": dict(team.role_templates or {}),
                    "blueprint_snapshot": dict(team.blueprint_snapshot or {}),
                    "members": list(members_by_team.get(team.id, [])),
                }
                for team in teams
            ]
        )

@teams_bp.route("/teams", methods=["POST"])
@check_auth
@admin_required
@validate_request(TeamCreateRequest)
def create_team():
    data = request.get_json()
    with Session(engine) as session:
        new_team = TeamDB(
            name=data["name"],
            description=data.get("description"),
            type=data.get("type", "Scrum"),
            is_active=data.get("is_active", False)
        )
        session.add(new_team)
        session.commit()
        session.refresh(new_team)
        members_payload = list(data.get("members") or [])
        created_members = []
        for member in members_payload:
            team_member = TeamMemberDB(
                team_id=new_team.id,
                agent_url=member["agent_url"],
                role_id=member["role_id"],
            )
            session.add(team_member)
            created_members.append(team_member)
        session.commit()
        return api_response(
            data={
                "id": new_team.id,
                "name": new_team.name,
                "description": new_team.description,
                "team_type_id": new_team.team_type_id,
                "blueprint_id": new_team.blueprint_id,
                "is_active": new_team.is_active,
                "role_templates": dict(new_team.role_templates or {}),
                "blueprint_snapshot": dict(new_team.blueprint_snapshot or {}),
                "members": [member.model_dump() for member in created_members],
            },
            message="Team erstellt",
            code=201,
        )

# Role Routes
@teams_bp.route("/roles", methods=["GET"])
@check_auth
def list_roles():
    with Session(engine) as session:
        roles = session.exec(select(RoleDB)).all()
        return api_response(data={"roles": [r.dict() for r in roles]})

@teams_bp.route("/roles", methods=["POST"])
@check_auth
@admin_required
@validate_request(RoleCreateRequest)
def create_role():
    data = request.get_json()
    with Session(engine) as session:
        new_role = RoleDB(
            name=data["name"],
            description=data.get("description"),
            default_template_id=data.get("default_template_id")
        )
        session.add(new_role)
        session.commit()
        session.refresh(new_role)
        return api_response(data=new_role.dict(), message="Rolle erstellt")

# Team-Type Routes
@teams_bp.route("/team-types", methods=["GET"])
@check_auth
def list_team_types():
    with Session(engine) as session:
        types = session.exec(select(TeamTypeDB)).all()
        return api_response(data={"team_types": [t.dict() for t in types]})

@teams_bp.route("/team-types", methods=["POST"])
@check_auth
@admin_required
@validate_request(TeamTypeCreateRequest)
def create_team_type():
    data = request.get_json()
    with Session(engine) as session:
        new_type = TeamTypeDB(
            name=data["name"],
            description=data.get("description")
        )
        session.add(new_type)
        session.commit()
        session.refresh(new_type)
        return api_response(data=new_type.dict(), message="Team-Typ erstellt")
