import uuid
from flask import Blueprint, jsonify, current_app, request, g
from agent.utils import validate_request, read_json, write_json
from agent.auth import check_auth
from agent.models import Team, TeamCreateRequest, TeamUpdateRequest

teams_bp = Blueprint("teams", __name__)

def _get_teams():
    return read_json(current_app.config["TEAMS_PATH"], [])

def _save_teams(teams):
    write_json(current_app.config["TEAMS_PATH"], teams)

@teams_bp.route("/teams", methods=["GET"])
@check_auth
def list_teams():
    return jsonify(_get_teams())

@teams_bp.route("/teams", methods=["POST"])
@check_auth
@validate_request(TeamCreateRequest)
def create_team():
    data: TeamCreateRequest = g.validated_data
    teams = _get_teams()
    
    new_team = Team(
        name=data.name,
        description=data.description,
        type=data.type or "Scrum",
        agent_names=data.agent_names or [],
        is_active=False
    )
    
    # Falls es das erste Team ist, machen wir es aktiv? 
    # Nein, explizit lassen.
    
    teams.append(new_team.model_dump())
    _save_teams(teams)
    return jsonify(new_team.model_dump()), 201

@teams_bp.route("/teams/<team_id>", methods=["PATCH"])
@check_auth
@validate_request(TeamUpdateRequest)
def update_team(team_id):
    data: TeamUpdateRequest = g.validated_data
    teams = _get_teams()
    
    for t in teams:
        if t["id"] == team_id:
            if data.name is not None: t["name"] = data.name
            if data.description is not None: t["description"] = data.description
            if data.type is not None: t["type"] = data.type
            if data.agent_names is not None: t["agent_names"] = data.agent_names
            
            if data.is_active is True:
                # Alle anderen deaktivieren
                for other in teams:
                    other["is_active"] = False
                t["is_active"] = True
            elif data.is_active is False:
                t["is_active"] = False
                
            _save_teams(teams)
            return jsonify(t)
            
    return jsonify({"error": "not_found"}), 404

@teams_bp.route("/teams/<team_id>", methods=["DELETE"])
@check_auth
def delete_team(team_id):
    teams = _get_teams()
    new_teams = [t for t in teams if t["id"] != team_id]
    if len(new_teams) < len(teams):
        _save_teams(new_teams)
        return jsonify({"status": "deleted"})
    return jsonify({"error": "not_found"}), 404

@teams_bp.route("/teams/<team_id>/activate", methods=["POST"])
@check_auth
def activate_team(team_id):
    teams = _get_teams()
    found = False
    for t in teams:
        if t["id"] == team_id:
            t["is_active"] = True
            found = True
        else:
            t["is_active"] = False
            
    if found:
        _save_teams(teams)
        return jsonify({"status": "activated"})
    return jsonify({"error": "not_found"}), 404
