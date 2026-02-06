import uuid
from flask import Blueprint, jsonify, current_app, request, g
from agent.utils import validate_request, read_json, write_json
from agent.auth import check_auth, admin_required
from agent.models import (
    Team, TeamCreateRequest, TeamUpdateRequest, 
    TeamTypeCreateRequest, RoleCreateRequest, TeamMemberAssignment
)
from agent.common.audit import log_audit
from agent.repository import (
    team_repo, template_repo, team_type_repo, role_repo, team_member_repo, agent_repo,
    team_type_role_link_repo
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

def normalize_team_type_name(team_type_name: str) -> str:
    if not team_type_name:
        return ""
    normalized = team_type_name.strip()
    mapping = {
        "scrum": "Scrum",
        "kanban": "Kanban",
    }
    return mapping.get(normalized.lower(), normalized)

def initialize_scrum_artifacts(team_name: str, team_id: str | None = None):
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

def ensure_default_templates(team_type_name: str):
    """Stellt sicher, dass Standard-Rollen und Templates fuer einen Team-Typ existieren."""
    team_type_name = normalize_team_type_name(team_type_name)
    if not team_type_name:
        return
    tt = team_type_repo.get_by_name(team_type_name)
    if not tt:
        tt = TeamTypeDB(name=team_type_name, description=f"Standard {team_type_name} Team")
        team_type_repo.save(tt)

    templates_by_name = {t.name: t for t in template_repo.get_all()}

    def ensure_template(name: str, description: str, prompt_template: str) -> TemplateDB:
        tpl = templates_by_name.get(name)
        if not tpl:
            tpl = TemplateDB(name=name, description=description, prompt_template=prompt_template)
            template_repo.save(tpl)
            templates_by_name[name] = tpl
        return tpl

    def ensure_role_links(role_definitions: list[tuple[str, str, TemplateDB]]):
        from agent.database import engine
        from sqlmodel import Session, select
        for role_name, role_desc, tpl in role_definitions:
            role = role_repo.get_by_name(role_name)
            if not role:
                role = RoleDB(name=role_name, description=role_desc, default_template_id=tpl.id)
                role_repo.save(role)
            elif role.default_template_id is None:
                role.default_template_id = tpl.id
                role_repo.save(role)

            with Session(engine) as session:
                link = session.exec(select(TeamTypeRoleLink).where(
                    TeamTypeRoleLink.team_type_id == tt.id,
                    TeamTypeRoleLink.role_id == role.id
                )).first()
                if not link:
                    link = TeamTypeRoleLink(team_type_id=tt.id, role_id=role.id, template_id=tpl.id)
                    session.add(link)
                    session.commit()

    if team_type_name == "Scrum":
        scrum_po_tpl = ensure_template(
            "Scrum - Product Owner",
            "Prompt template for Scrum Product Owner.",
            "You are the Product Owner in a Scrum team. Align backlog, priorities, and acceptance criteria with {{team_goal}}."
        )
        scrum_sm_tpl = ensure_template(
            "Scrum - Scrum Master",
            "Prompt template for Scrum Master.",
            "You are the Scrum Master for a Scrum team. Facilitate events, remove blockers, and improve flow toward {{team_goal}}."
        )
        scrum_dev_tpl = ensure_template(
            "Scrum - Developer",
            "Prompt template for Scrum Developer.",
            "You are a Developer in a Scrum team. Implement backlog items, review work, and deliver increments for {{team_goal}}."
        )
        ensure_role_links([
            ("Product Owner", "Owns the backlog and prioritization.", scrum_po_tpl),
            ("Scrum Master", "Facilitates the Scrum process.", scrum_sm_tpl),
            ("Developer", "Builds and delivers backlog items.", scrum_dev_tpl),
        ])

    if team_type_name == "Kanban":
        kanban_sdm_tpl = ensure_template(
            "Kanban - Service Delivery Manager",
            "Prompt template for Kanban Service Delivery Manager.",
            "You are the Service Delivery Manager in a Kanban team. Monitor flow metrics and service delivery toward {{team_goal}}."
        )
        kanban_flow_tpl = ensure_template(
            "Kanban - Flow Manager",
            "Prompt template for Kanban Flow Manager.",
            "You are the Flow Manager in a Kanban team. Optimize WIP, policies, and flow to achieve {{team_goal}}."
        )
        kanban_dev_tpl = ensure_template(
            "Kanban - Developer",
            "Prompt template for Kanban Developer.",
            "You are a Developer in a Kanban team. Deliver work items, limit WIP, and maintain quality for {{team_goal}}."
        )
        ensure_role_links([
            ("Service Delivery Manager", "Oversees service delivery and flow metrics.", kanban_sdm_tpl),
            ("Flow Manager", "Optimizes WIP limits and flow.", kanban_flow_tpl),
            ("Developer", "Delivers work items and maintains quality.", kanban_dev_tpl),
        ])

@teams_bp.route("/teams/roles", methods=["GET"])
@check_auth
def get_team_roles():
    roles = role_repo.get_all()
    return jsonify([r.model_dump() for r in roles])

@teams_bp.route("/teams/types", methods=["GET"])
@check_auth
def list_team_types():
    types = team_type_repo.get_all()
    if not types:
        ensure_default_templates("Scrum")
        ensure_default_templates("Kanban")
        types = team_type_repo.get_all()
    result = []
    for t in types:
        td = t.model_dump()
        td["role_ids"] = team_type_role_link_repo.get_allowed_role_ids(t.id)
        from agent.database import engine
        from sqlmodel import Session, select
        with Session(engine) as session:
            links = session.exec(select(TeamTypeRoleLink).where(
                TeamTypeRoleLink.team_type_id == t.id
            )).all()
        td["role_templates"] = {link.role_id: link.template_id for link in links}
        result.append(td)
    return jsonify(result)

@teams_bp.route("/teams/types", methods=["POST"])
@check_auth
@admin_required
@validate_request(TeamTypeCreateRequest)
def create_team_type():
    data: TeamTypeCreateRequest = g.validated_data
    normalized_name = normalize_team_type_name(data.name)
    new_type = TeamTypeDB(name=normalized_name, description=data.description)
    team_type_repo.save(new_type)
    if normalized_name:
        ensure_default_templates(normalized_name)
    log_audit("team_type_created", {"team_type_id": new_type.id, "name": new_type.name})
    return jsonify(new_type.model_dump()), 201

@teams_bp.route("/teams/types/<type_id>/roles", methods=["POST"])
@check_auth
@admin_required
def link_role_to_type(type_id):
    role_id = request.json.get("role_id")
    template_id = request.json.get("template_id")
    if not role_id:
        return jsonify({"error": "role_id_required"}), 400

    if not role_repo.get_by_id(role_id):
        return jsonify({"error": "role_not_found"}), 404
    if template_id and not template_repo.get_by_id(template_id):
        return jsonify({"error": "template_not_found"}), 404
    
    from agent.database import engine
    from sqlmodel import Session
    with Session(engine) as session:
        link = TeamTypeRoleLink(team_type_id=type_id, role_id=role_id, template_id=template_id)
        session.add(link)
        session.commit()
    log_audit("team_type_role_linked", {"team_type_id": type_id, "role_id": role_id, "template_id": template_id})
    return jsonify({"status": "linked"})

@teams_bp.route("/teams/types/<type_id>/roles", methods=["GET"])
@check_auth
def list_roles_for_type(type_id):
    from agent.database import engine
    from sqlmodel import Session, select
    with Session(engine) as session:
        links = session.exec(select(TeamTypeRoleLink).where(
            TeamTypeRoleLink.team_type_id == type_id
        )).all()
    result = []
    for link in links:
        role = role_repo.get_by_id(link.role_id)
        if not role:
            continue
        rd = role.model_dump()
        rd["template_id"] = link.template_id
        result.append(rd)
    return jsonify(result)

@teams_bp.route("/teams/types/<type_id>/roles/<role_id>", methods=["PATCH"])
@check_auth
@admin_required
def update_role_template_mapping(type_id, role_id):
    template_id = request.json.get("template_id")
    if template_id and not template_repo.get_by_id(template_id):
        return jsonify({"error": "template_not_found"}), 404

    from agent.database import engine
    from sqlmodel import Session, select
    with Session(engine) as session:
        link = session.exec(select(TeamTypeRoleLink).where(
            TeamTypeRoleLink.team_type_id == type_id,
            TeamTypeRoleLink.role_id == role_id
        )).first()
        if not link:
            return jsonify({"error": "not_found"}), 404
        link.template_id = template_id
        session.add(link)
        session.commit()
    log_audit("team_type_role_template_updated", {"team_type_id": type_id, "role_id": role_id, "template_id": template_id})
    return jsonify({"status": "updated"})

@teams_bp.route("/teams", methods=["GET"])
@check_auth
def list_teams():
    """
    Alle Teams auflisten
    ---
    tags:
      - Teams
    security:
      - Bearer: []
    responses:
      200:
        description: Liste aller Teams mit Mitgliedern
    """
    teams = team_repo.get_all()
    result = []
    for t in teams:
        team_dict = t.model_dump()
        # Mitglieder laden
        members = team_member_repo.get_by_team(t.id)
        team_dict["members"] = [m.model_dump() for m in members]
        result.append(team_dict)
    return jsonify(result)

@teams_bp.route("/teams", methods=["POST"])
@check_auth
@admin_required
@validate_request(TeamCreateRequest)
def create_team():
    """
    Neues Team erstellen
    ---
    tags:
      - Teams
    security:
      - Bearer: []
    parameters:
      - in: body
        name: team
        required: true
        schema:
          id: TeamCreateRequest
    responses:
      201:
        description: Team erstellt
    """
    data: TeamCreateRequest = g.validated_data
    
    team_type = None
    if data.team_type_id:
        team_type = team_type_repo.get_by_id(data.team_type_id)
        if not team_type:
            return jsonify({"error": "team_type_not_found"}), 404
        if team_type:
            ensure_default_templates(team_type.name)
    
    # Validierung der Mitglieder-Rollen
    if data.members and data.team_type_id:
        allowed_role_ids = team_type_role_link_repo.get_allowed_role_ids(data.team_type_id)
        if allowed_role_ids:
            for m_data in data.members:
                if not role_repo.get_by_id(m_data.role_id):
                    return jsonify({"error": "role_not_found", "role_id": m_data.role_id}), 404
                if m_data.role_id not in allowed_role_ids:
                    return jsonify({"error": "invalid_role_for_team_type", "role_id": m_data.role_id}), 400
                if m_data.custom_template_id and not template_repo.get_by_id(m_data.custom_template_id):
                    return jsonify({"error": "template_not_found", "template_id": m_data.custom_template_id}), 404
    if data.members and not data.team_type_id:
        for m_data in data.members:
            if not role_repo.get_by_id(m_data.role_id):
                return jsonify({"error": "role_not_found", "role_id": m_data.role_id}), 404
            if m_data.custom_template_id and not template_repo.get_by_id(m_data.custom_template_id):
                return jsonify({"error": "template_not_found", "template_id": m_data.custom_template_id}), 404

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
                role_id=m_data.role_id,
                custom_template_id=m_data.custom_template_id
            )
            team_member_repo.save(member)
    
    # Scrum Artefakte initialisieren falls es ein Scrum Team ist
    if data.team_type_id:
        team_type = team_type_repo.get_by_id(data.team_type_id)
        if team_type and team_type.name == "Scrum":
            initialize_scrum_artifacts(new_team.name, new_team.id)
    log_audit("team_created", {"team_id": new_team.id, "name": new_team.name})
    return jsonify(new_team.model_dump()), 201

@teams_bp.route("/teams/<team_id>", methods=["PATCH"])
@check_auth
@admin_required
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
        # Validierung der Mitglieder-Rollen
        tt_id = data.team_type_id if data.team_type_id is not None else team.team_type_id
        if tt_id:
            allowed_role_ids = team_type_role_link_repo.get_allowed_role_ids(tt_id)
            if allowed_role_ids:
                for m_data in data.members:
                    if not role_repo.get_by_id(m_data.role_id):
                        return jsonify({"error": "role_not_found", "role_id": m_data.role_id}), 404
                    if m_data.role_id not in allowed_role_ids:
                        return jsonify({"error": "invalid_role_for_team_type", "role_id": m_data.role_id}), 400
                    if m_data.custom_template_id and not template_repo.get_by_id(m_data.custom_template_id):
                        return jsonify({"error": "template_not_found", "template_id": m_data.custom_template_id}), 404
        else:
            for m_data in data.members:
                if not role_repo.get_by_id(m_data.role_id):
                    return jsonify({"error": "role_not_found", "role_id": m_data.role_id}), 404
                if m_data.custom_template_id and not template_repo.get_by_id(m_data.custom_template_id):
                    return jsonify({"error": "template_not_found", "template_id": m_data.custom_template_id}), 404

        # Alte Mitglieder löschen und neue anlegen
        team_member_repo.delete_by_team(team_id)
        for m_data in data.members:
            member = TeamMemberDB(
                team_id=team_id,
                agent_url=m_data.agent_url,
                role_id=m_data.role_id,
                custom_template_id=m_data.custom_template_id
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
    log_audit("team_updated", {"team_id": team_id})
    return jsonify(team.model_dump())

@teams_bp.route("/teams/setup-scrum", methods=["POST"])
@check_auth
@admin_required
def setup_scrum():
    """Erstellt ein Standard-Scrum-Team mit allen Artefakten."""
    team_name = request.json.get("name", "Neues Scrum Team")
    
    # Scrum Team-Typ finden
    ensure_default_templates("Scrum")
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
        others = session.exec(select(TeamDB)).all()
        for other in others:
            other.is_active = False
            session.add(other)
        session.commit()

    team_repo.save(new_team)
    initialize_scrum_artifacts(new_team.name, new_team.id)
    
    log_audit("team_scrum_setup", {"team_id": new_team.id, "name": new_team.name})
    return jsonify({
        "status": "success",
        "message": f"Scrum Team '{team_name}' wurde erfolgreich mit allen Templates und Artefakten angelegt.",
        "team": new_team.model_dump()
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
    log_audit("role_created", {"role_id": new_role.id, "name": new_role.name})
    return jsonify(new_role.model_dump()), 201

@teams_bp.route("/teams/types/<type_id>", methods=["DELETE"])
@check_auth
@admin_required
def delete_team_type(type_id):
    if team_type_repo.delete(type_id):
        log_audit("team_type_deleted", {"team_type_id": type_id})
        return jsonify({"status": "deleted"})
    return jsonify({"error": "not_found"}), 404

@teams_bp.route("/teams/types/<type_id>/roles/<role_id>", methods=["DELETE"])
@check_auth
@admin_required
def unlink_role_from_type(type_id, role_id):
    if team_type_role_link_repo.delete(type_id, role_id):
        log_audit("team_type_role_unlinked", {"team_type_id": type_id, "role_id": role_id})
        return jsonify({"status": "unlinked"})
    return jsonify({"error": "not_found"}), 404

@teams_bp.route("/teams/roles/<role_id>", methods=["DELETE"])
@check_auth
@admin_required
def delete_role(role_id):
    if role_repo.delete(role_id):
        log_audit("role_deleted", {"role_id": role_id})
        return jsonify({"status": "deleted"})
    return jsonify({"error": "not_found"}), 404

@teams_bp.route("/teams/<team_id>", methods=["DELETE"])
@check_auth
@admin_required
def delete_team(team_id):
    if team_repo.delete(team_id):
        log_audit("team_deleted", {"team_id": team_id})
        return jsonify({"status": "deleted"})
    return jsonify({"error": "not_found"}), 404

@teams_bp.route("/teams/<team_id>/activate", methods=["POST"])
@check_auth
@admin_required
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
        log_audit("team_activated", {"team_id": team_id})
        return jsonify({"status": "activated"})
