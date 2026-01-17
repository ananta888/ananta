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

@teams_bp.route("/teams/roles", methods=["GET"])
@check_auth
def get_team_roles():
    roles = {}
    for team_type, tpls in DEFAULT_TEMPLATES.items():
        roles[team_type] = [t["name"] for t in tpls]
    return jsonify(roles)

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
        role_templates=data.role_templates or {},
        is_active=False
    )
    
    # Automatische Template-Erstellung
    ensure_default_templates(new_team.type)
    
    # Standard-Templates zuordnen falls nicht übergeben
    if not new_team.role_templates and new_team.type in DEFAULT_TEMPLATES:
        all_templates = {t.name: t.id for t in template_repo.get_all()}
        # Zuordnung zu Agenten (falls vorhanden) oder Initialisierung des Mapping-Objekts
        new_team.role_templates = {}
        for i, agent_name in enumerate(new_team.agent_names):
            if i < len(DEFAULT_TEMPLATES[new_team.type]):
                tpl_data = DEFAULT_TEMPLATES[new_team.type][i]
                if tpl_data["name"] in all_templates:
                    new_team.role_templates[agent_name] = {
                        "role": tpl_data["name"],
                        "template_id": all_templates[tpl_data["name"]]
                    }

    team_repo.save(new_team)
    
    # Scrum Artefakte initialisieren
    if new_team.type == "Scrum":
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
    if data.type is not None: team.type = data.type
    if data.agent_names is not None: team.agent_names = data.agent_names
    if data.role_templates is not None: team.role_templates = data.role_templates
    
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
    
    new_team = TeamDB(
        name=team_name,
        description="Automatisch erstelltes Scrum Team mit Backlog, Board, Roadmap und Burndown Chart.",
        type="Scrum",
        agent_names=[],
        role_templates={},
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

    ensure_default_templates("Scrum")
    
    # Standard-Templates zuordnen
    all_templates = {t.name: t.id for t in template_repo.get_all()}
    new_team.role_templates = {}
    for i, agent_name in enumerate(new_team.agent_names):
        if i < len(DEFAULT_TEMPLATES["Scrum"]):
            tpl_data = DEFAULT_TEMPLATES["Scrum"][i]
            if tpl_data["name"] in all_templates:
                new_team.role_templates[agent_name] = {
                    "role": tpl_data["name"],
                    "template_id": all_templates[tpl_data["name"]]
                }
    
    team_repo.save(new_team)
    initialize_scrum_artifacts(new_team.name)
    
    return jsonify({
        "status": "success",
        "message": f"Scrum Team '{team_name}' wurde erfolgreich mit allen Templates und Artefakten angelegt.",
        "team": new_team.dict()
    }), 201

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
