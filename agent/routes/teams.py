import uuid
from flask import Blueprint, jsonify, current_app, request, g
from agent.utils import validate_request, read_json, write_json
from agent.auth import check_auth, admin_required
from agent.models import Team, TeamCreateRequest, TeamUpdateRequest
from agent.repository import team_repo
from agent.db_models import TeamDB

teams_bp = Blueprint("teams", __name__)

@teams_bp.route("/teams", methods=["GET"])
@check_auth
def list_teams():
    teams = team_repo.get_all()
    return jsonify([t.dict() for t in teams])

@teams_bp.route("/teams", methods=["POST"])
@check_auth
@validate_request(TeamCreateRequest)
def create_team():
    data: TeamCreateRequest = g.validated_data
    
    new_team = TeamDB(
        name=data.name,
        description=data.description,
        type=data.type or "Scrum",
        agent_names=data.agent_names or [],
        is_active=False
    )
    
    team_repo.save(new_team)
    return jsonify(new_team.dict()), 201

@teams_bp.route("/teams/<team_id>", methods=["PATCH"])
@check_auth
@validate_request(TeamUpdateRequest)
def update_team(team_id):
    data: TeamUpdateRequest = g.validated_data
    team = team_repo.get_by_id(team_id)
    
    if not team:
        return jsonify({"error": "not_found"}), 404
        
    if data.name is not None: team.name = data.name
    if data.description is not None: team.description = data.description
    if data.type is not None: team.type = data.type
    if data.agent_names is not None: team.agent_names = data.agent_names
    
    if data.is_active is True:
        # Alle anderen deaktivieren
        from sqlmodel import Session, select
        from agent.database import engine
        with Session(engine) as session:
            others = session.exec(select(TeamDB).where(TeamDB.id != team_id)).all()
            for other in others:
                other.is_active = False
                session.add(other)
            team.is_active = True
            session.add(team)
            session.commit()
            session.refresh(team)
    elif data.is_active is False:
        team.is_active = False
        team_repo.save(team)
    else:
        team_repo.save(team)
        
    return jsonify(team.dict())

@teams_bp.route("/teams/<team_id>", methods=["DELETE"])
@check_auth
def delete_team(team_id):
    if team_repo.delete(team_id):
        return jsonify({"status": "deleted"})
    return jsonify({"error": "not_found"}), 404

@teams_bp.route("/teams/<team_id>/activate", methods=["POST"])
@check_auth
def activate_team(team_id):
    from sqlmodel import Session, select
    from agent.database import engine
    with Session(engine) as session:
        team = session.get(TeamDB, team_id)
        if not team:
            return jsonify({"error": "not_found"}), 404
            
        others = session.exec(select(TeamDB).where(TeamDB.id != team_id)).all()
        for other in others:
            other.is_active = False
            session.add(other)
            
        team.is_active = True
        session.add(team)
        session.commit()
        return jsonify({"status": "activated"})
