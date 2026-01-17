import uuid
from flask import Blueprint, jsonify, current_app, request, g
from agent.utils import validate_request, read_json, write_json
from agent.auth import check_auth, admin_required
from agent.models import Team, TeamCreateRequest, TeamUpdateRequest
from agent.repository import team_repo, template_repo
from agent.db_models import TeamDB, TemplateDB

teams_bp = Blueprint("teams", __name__)

DEFAULT_TEMPLATES = {
    "Scrum": [
        {"name": "Scrum Master", "description": "Facilitator for a Scrum team", "prompt_template": "Du bist ein erfahrener Scrum Master. Deine Aufgabe ist es, das Team bei der Umsetzung von Scrum zu unterstützen, Hindernisse zu beseitigen und für eine reibungslose Zusammenarbeit zu sorgen."},
        {"name": "Product Owner", "description": "Business representative in a Scrum team", "prompt_template": "Du bist ein Product Owner. Deine Aufgabe ist es, das Product Backlog zu verwalten, Anforderungen zu priorisieren und sicherzustellen, dass das Team den größtmöglichen Geschäftswert liefert."},
        {"name": "Developer", "description": "Core contributor in a Scrum team", "prompt_template": "Du bist ein erfahrener Software-Entwickler. Deine Aufgabe ist es, qualitativ hochwertigen Code zu schreiben, an Architekturdiskussionen teilzunehmen und im Team Lösungen zu entwickeln."}
    ],
    "Kanban": [
        {"name": "Service Request Manager", "description": "Handles incoming requests", "prompt_template": "Du bist ein Service Request Manager. Deine Aufgabe ist es, eingehende Anfragen zu sichten, zu priorisieren und in den Kanban-Flow einzusteuern."},
        {"name": "Service Delivery Manager", "description": "Ensures flow and efficiency", "prompt_template": "Du bist ein Service Delivery Manager. Deine Aufgabe ist es, den Flow im Team zu überwachen, Engpässe zu identifizieren und die Lieferfähigkeit zu optimieren."}
    ]
}

def ensure_default_templates(team_type: str):
    """Erstellt Standard-Templates für einen Team-Typ, falls diese noch nicht existieren."""
    if team_type not in DEFAULT_TEMPLATES:
        return
    
    existing_templates = {t.name: t for t in template_repo.get_all()}
    
    for tpl_data in DEFAULT_TEMPLATES[team_type]:
        if tpl_data["name"] not in existing_templates:
            new_tpl = TemplateDB(
                name=tpl_data["name"],
                description=tpl_data["description"],
                prompt_template=tpl_data["prompt_template"]
            )
            template_repo.save(new_tpl)
            current_app.logger.info(f"Standard-Template erstellt: {new_tpl.name} für Team-Typ {team_type}")

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
    
    # Automatische Template-Erstellung
    ensure_default_templates(new_team.type)
    
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
