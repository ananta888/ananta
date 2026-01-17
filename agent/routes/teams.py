import uuid
from flask import Blueprint, jsonify, current_app, request, g
from agent.utils import validate_request, read_json, write_json
from agent.auth import check_auth, admin_required
from agent.models import (
    Team, TeamCreateRequest, TeamUpdateRequest, 
    TeamTypeCreateRequest, RoleCreateRequest, TeamMemberAssignment
)
from agent.repository import (
    team_repo, template_repo, team_type_repo, role_repo, team_member_repo, agent_repo
)
from agent.db_models import (
    TeamDB, TemplateDB, TeamTypeDB, RoleDB, TeamMemberDB, TeamTypeRoleLink
)

teams_bp = Blueprint("teams", __name__)

SCRUM_INITIAL_TASKS = [
    {"title": "Scrum Backlog", "description": "Initiales Product Backlog für das Team.", "status": "backlog", "priority": "High"},
    {"title": "Sprint Board Setup", "description": "Visualisierung des aktuellen Sprints.", "status": "todo", "priority": "High"},
    {"title": "Burndown Chart", "description": "Tracken des Fortschritts im Sprint.", "status": "todo", "priority": "Medium"},
    {"title": "Roadmap", "description": "Langfristige Planung und Meilensteine.", "status": "backlog", "priority": "Medium"},
    {"title": "Setup & Usage Instructions", "description": """### Setup & Usage Instructions
1. Clone the template repository and create a new repository based on the cloned one.
2. Customize the template by updating the content of the README file and any other files you see fit.
3. Add your teammates as collaborators to the repository.
4. Set up an integration (e.g., with GitHub Actions) to automate the creation of a new sprint branch whenever the team is ready to start a new sprint.
5. Set up your project and workflow in the Bitte interface, such as assigning work items to team members or setting up notifications for completed tasks.
6. Use the template’s sprint board to plan and execute on each sprint.
7. Use the burndown chart to track progress towards completing user stories and reaching your sprint goals.
8. Use the roadmap to visualize upcoming milestones and help teams plan their work accordingly.
9. Use Bitte’s project and team settings to manage your team, such as setting up access levels or adding new members to your team.""", "status": "todo", "priority": "High"}
]

def initialize_scrum_artifacts(team_name: str):
    """Erstellt initiale Tasks für ein Scrum Team."""
    from agent.repository import task_repo
    from agent.db_models import TaskDB
    import time
    
    for task_data in SCRUM_INITIAL_TASKS:
        new_task = TaskDB(
            id=str(uuid.uuid4()),
            title=f"{team_name}: {task_data['title']}",
            description=task_data["description"],
            status=task_data["status"],
            priority=task_data["priority"],
            created_at=time.time(),
            updated_at=time.time()
        )
        task_repo.save(new_task)

@teams_bp.route("/teams/roles", methods=["GET"])
@check_auth
def get_team_roles():
    roles = role_repo.get_all()
    return jsonify([r.dict() for r in roles])

@teams_bp.route("/teams/types", methods=["GET"])
@check_auth
def list_team_types():
    types = team_type_repo.get_all()
    return jsonify([t.dict() for t in types])

@teams_bp.route("/teams/types", methods=["POST"])
@check_auth
@admin_required
@validate_request(TeamTypeCreateRequest)
def create_team_type():
    data: TeamTypeCreateRequest = g.validated_data
    new_type = TeamTypeDB(name=data.name, description=data.description)
    team_type_repo.save(new_type)
    return jsonify(new_type.dict()), 201

@teams_bp.route("/teams/types/<type_id>/roles", methods=["POST"])
@check_auth
@admin_required
def link_role_to_type(type_id):
    role_id = request.json.get("role_id")
    if not role_id:
        return jsonify({"error": "role_id_required"}), 400
    
    from agent.database import engine
    from sqlmodel import Session
    with Session(engine) as session:
        link = TeamTypeRoleLink(team_type_id=type_id, role_id=role_id)
        session.add(link)
        session.commit()
    return jsonify({"status": "linked"})

@teams_bp.route("/teams", methods=["GET"])
@check_auth
def list_teams():
    teams = team_repo.get_all()
    result = []
    for t in teams:
        team_dict = t.dict()
        # Mitglieder laden
        members = team_member_repo.get_by_team(t.id)
        team_dict["members"] = [m.dict() for m in members]
        result.append(team_dict)
    return jsonify(result)

@teams_bp.route("/teams", methods=["POST"])
@check_auth
@validate_request(TeamCreateRequest)
def create_team():
    data: TeamCreateRequest = g.validated_data
    
    new_team = TeamDB(
        name=data.name,
        description=data.description,
        team_type_id=data.team_type_id,
        is_active=False
    )
    team_repo.save(new_team)
    
    # Mitglieder speichern
    if data.members:
        for m_data in data.members:
            member = TeamMemberDB(
                team_id=new_team.id,
                agent_url=m_data.agent_url,
                role_id=m_data.role_id
            )
            team_member_repo.save(member)
    
    # Scrum Artefakte initialisieren falls es ein Scrum Team ist
    if data.team_type_id:
        team_type = team_type_repo.get_by_id(data.team_type_id)
        if team_type and team_type.name == "Scrum":
            initialize_scrum_artifacts(new_team.name)
    
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
    if data.team_type_id is not None: team.team_type_id = data.team_type_id
    
    if data.members is not None:
        # Alte Mitglieder löschen und neue anlegen
        team_member_repo.delete_by_team(team_id)
        for m_data in data.members:
            member = TeamMemberDB(
                team_id=team_id,
                agent_url=m_data.agent_url,
                role_id=m_data.role_id
            )
            team_member_repo.save(member)

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

@teams_bp.route("/teams/setup-scrum", methods=["POST"])
@check_auth
def setup_scrum():
    """Erstellt ein Standard-Scrum-Team mit allen Artefakten."""
    team_name = request.json.get("name", "Neues Scrum Team")
    
    # Scrum Team-Typ finden
    scrum_type = team_type_repo.get_by_name("Scrum")
    if not scrum_type:
        return jsonify({"error": "scrum_type_not_found"}), 404
    
    new_team = TeamDB(
        name=team_name,
        description="Automatisch erstelltes Scrum Team mit Backlog, Board, Roadmap und Burndown Chart.",
        team_type_id=scrum_type.id,
        is_active=True
    )
    
    # Andere Teams deaktivieren
    from sqlmodel import Session, select
    from agent.database import engine
    with Session(engine) as session:
        others = session.exec(select(TeamDB).all())
        for other in others:
            other.is_active = False
            session.add(other)
        session.commit()

    team_repo.save(new_team)
    initialize_scrum_artifacts(new_team.name)
    
    return jsonify({
        "status": "success",
        "message": f"Scrum Team '{team_name}' wurde erfolgreich mit allen Templates und Artefakten angelegt.",
        "team": new_team.dict()
    }), 201

@teams_bp.route("/teams/roles", methods=["POST"])
@check_auth
@admin_required
@validate_request(RoleCreateRequest)
def create_role():
    data: RoleCreateRequest = g.validated_data
    new_role = RoleDB(
        name=data.name, 
        description=data.description, 
        default_template_id=data.default_template_id
    )
    role_repo.save(new_role)
    return jsonify(new_role.dict()), 201

@teams_bp.route("/teams/types/<type_id>", methods=["DELETE"])
@check_auth
@admin_required
def delete_team_type(type_id):
    if team_type_repo.delete(type_id):
        return jsonify({"status": "deleted"})
    return jsonify({"error": "not_found"}), 404

@teams_bp.route("/teams/roles/<role_id>", methods=["DELETE"])
@check_auth
@admin_required
def delete_role(role_id):
    if role_repo.delete(role_id):
        return jsonify({"status": "deleted"})
    return jsonify({"error": "not_found"}), 404

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
