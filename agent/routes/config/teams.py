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
def list_teams():
    check_auth()
    with Session(engine) as session:
        teams = session.exec(select(TeamDB)).all()
        return api_response(data={"teams": [t.dict() for t in teams]})

@teams_bp.route("/teams", methods=["POST"])
@validate_request(TeamCreateRequest)
def create_team():
    admin_required()
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
        return api_response(data=new_team.dict(), message="Team erstellt")

# Role Routes
@teams_bp.route("/roles", methods=["GET"])
def list_roles():
    check_auth()
    with Session(engine) as session:
        roles = session.exec(select(RoleDB)).all()
        return api_response(data={"roles": [r.dict() for r in roles]})

@teams_bp.route("/roles", methods=["POST"])
@validate_request(RoleCreateRequest)
def create_role():
    admin_required()
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
def list_team_types():
    check_auth()
    with Session(engine) as session:
        types = session.exec(select(TeamTypeDB)).all()
        return api_response(data={"team_types": [t.dict() for t in types]})

@teams_bp.route("/team-types", methods=["POST"])
@validate_request(TeamTypeCreateRequest)
def create_team_type():
    admin_required()
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
