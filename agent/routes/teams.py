"""Team, team-type and role routes.

Blueprint routes live in agent/routes/blueprint_routes.py; seed/bootstrap
helpers in agent/services/blueprint_seed_service.py; serialization helpers in
agent/services/blueprint_serializer.py (SPLIT-012).
"""

from flask import Blueprint, g, request
from sqlmodel import Session, select

from agent.auth import admin_required, check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.database import engine
from agent.db_models import (
    RoleDB,
    TeamDB,
    TeamMemberDB,
    TeamTypeDB,
    TeamTypeRoleLink,
)
from agent.models import (
    RoleCreateRequest,
    TeamBlueprintInstantiateRequest,
    TeamCreateRequest,
    TeamSetupScrumRequest,
    TeamTypeCreateRequest,
    TeamTypeRoleLinkCreateRequest,
    TeamTypeRoleLinkPatchRequest,
    TeamUpdateRequest,
)
from agent.routes.blueprint_routes import _instantiate_blueprint
from agent.routes.route_utils import team_error as _team_error
from agent.services.blueprint_seed_service import (
    ensure_default_templates,
    ensure_seed_blueprints,
    initialize_scrum_artifacts,
    normalize_team_type_name,
)
from agent.services.blueprint_serializer import (
    _serialize_blueprint,
    _user_lifecycle_state_from_metadata,
)
from agent.services.repository_registry import get_repository_registry
from agent.services.team_definition_version_service import (
    build_team_blueprint_diff,
    team_definition_metadata,
)
from agent.utils import validate_request

__all__ = [
    "teams_bp",
    # Re-exported for legacy consumers (e.g. agent/tools.py):
    "ensure_default_templates",
    "ensure_seed_blueprints",
    "initialize_scrum_artifacts",
    "normalize_team_type_name",
]

teams_bp = Blueprint("teams", __name__)


def _repos():
    return get_repository_registry()


@teams_bp.route("/teams/roles", methods=["GET"])
@check_auth
def get_team_roles():
    roles = _repos().role_repo.get_all()
    return api_response(data=[r.model_dump() for r in roles])


@teams_bp.route("/teams/types", methods=["GET"])
@check_auth
def list_team_types():
    types = _repos().team_type_repo.get_all()
    if not types:
        ensure_default_templates("Scrum")
        ensure_default_templates("Kanban")
        ensure_seed_blueprints()
        types = _repos().team_type_repo.get_all()
    result = []
    for t in types:
        td = t.model_dump()
        td["role_ids"] = _repos().team_type_role_link_repo.get_allowed_role_ids(t.id)
        with Session(engine) as session:
            links = session.exec(select(TeamTypeRoleLink).where(TeamTypeRoleLink.team_type_id == t.id)).all()
        td["role_templates"] = {link.role_id: link.template_id for link in links}
        result.append(td)
    return api_response(data=result)


@teams_bp.route("/teams/types", methods=["POST"])
@check_auth
@admin_required
@validate_request(TeamTypeCreateRequest)
def create_team_type():
    data: TeamTypeCreateRequest = g.validated_data
    normalized_name = normalize_team_type_name(data.name)
    new_type = TeamTypeDB(name=normalized_name, description=data.description)
    _repos().team_type_repo.save(new_type)
    if normalized_name:
        ensure_default_templates(normalized_name)
    log_audit("team_type_created", {"team_type_id": new_type.id, "name": new_type.name})
    return api_response(data=new_type.model_dump(), code=201)


@teams_bp.route("/teams/types/<type_id>/roles", methods=["POST"])
@check_auth
@admin_required
@validate_request(TeamTypeRoleLinkCreateRequest)
def link_role_to_type(type_id):
    data: TeamTypeRoleLinkCreateRequest = g.validated_data
    role_id = data.role_id
    template_id = request.json.get("template_id")
    if not role_id:
        return _team_error("role_id_required", 400)

    if not _repos().role_repo.get_by_id(role_id):
        return _team_error("role_not_found", 404)
    if template_id and not _repos().template_repo.get_by_id(template_id):
        return _team_error("template_not_found", 404)

    with Session(engine) as session:
        link = TeamTypeRoleLink(team_type_id=type_id, role_id=role_id, template_id=template_id)
        session.add(link)
        session.commit()
    log_audit("team_type_role_linked", {"team_type_id": type_id, "role_id": role_id, "template_id": template_id})
    return api_response(data={"status": "linked"})


@teams_bp.route("/teams/types/<type_id>/roles", methods=["GET"])
@check_auth
def list_roles_for_type(type_id):
    with Session(engine) as session:
        links = session.exec(select(TeamTypeRoleLink).where(TeamTypeRoleLink.team_type_id == type_id)).all()
    result = []
    for link in links:
        role = _repos().role_repo.get_by_id(link.role_id)
        if not role:
            continue
        rd = role.model_dump()
        rd["template_id"] = link.template_id
        result.append(rd)
    return api_response(data=result)


@teams_bp.route("/teams/types/<type_id>/roles/<role_id>", methods=["PATCH"])
@check_auth
@admin_required
@validate_request(TeamTypeRoleLinkPatchRequest)
def update_role_template_mapping(type_id, role_id):
    data: TeamTypeRoleLinkPatchRequest = g.validated_data
    template_id = data.template_id
    if template_id and not _repos().template_repo.get_by_id(template_id):
        return _team_error("template_not_found", 404)

    with Session(engine) as session:
        link = session.exec(
            select(TeamTypeRoleLink).where(
                TeamTypeRoleLink.team_type_id == type_id, TeamTypeRoleLink.role_id == role_id
            )
        ).first()
        if not link:
            return _team_error("not_found", 404)
        link.template_id = template_id
        session.add(link)
        session.commit()
    log_audit(
        "team_type_role_template_updated", {"team_type_id": type_id, "role_id": role_id, "template_id": template_id}
    )
    return api_response(data={"status": "updated"})


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
    teams = _repos().team_repo.get_all()
    result = []
    for t in teams:
        team_dict = t.model_dump()
        definition_metadata = team_definition_metadata(t)
        team_dict["definition_metadata"] = definition_metadata
        team_dict["user_lifecycle_state"] = _user_lifecycle_state_from_metadata(definition_metadata)
        # Mitglieder laden
        members = _repos().team_member_repo.get_by_team(t.id)
        team_dict["members"] = [m.model_dump() for m in members]
        result.append(team_dict)
    return api_response(data=result)


@teams_bp.route("/teams/<team_id>/blueprint-diff", methods=["GET"])
@check_auth
def get_team_blueprint_diff(team_id):
    diff = build_team_blueprint_diff(team_id)
    if diff is None:
        return _team_error("not_found", 404)
    return api_response(data=diff)


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
        team_type = _repos().team_type_repo.get_by_id(data.team_type_id)
        if not team_type:
            return _team_error("team_type_not_found", 404)
        if team_type:
            ensure_default_templates(team_type.name)

    # Validierung der Mitglieder-Rollen
    if data.members and data.team_type_id:
        allowed_role_ids = _repos().team_type_role_link_repo.get_allowed_role_ids(data.team_type_id)
        if allowed_role_ids:
            for m_data in data.members:
                if not m_data.role_id:
                    return _team_error("role_id_required", 400)
                if not _repos().role_repo.get_by_id(m_data.role_id):
                    return _team_error("role_not_found", 404, role_id=m_data.role_id)
                if m_data.role_id not in allowed_role_ids:
                    return _team_error("invalid_role_for_team_type", 400, role_id=m_data.role_id)
                if m_data.custom_template_id and not _repos().template_repo.get_by_id(m_data.custom_template_id):
                    return _team_error("template_not_found", 404, template_id=m_data.custom_template_id)
    if data.members and not data.team_type_id:
        for m_data in data.members:
            if not m_data.role_id:
                return _team_error("role_id_required", 400)
            if not _repos().role_repo.get_by_id(m_data.role_id):
                return _team_error("role_not_found", 404, role_id=m_data.role_id)
            if m_data.custom_template_id and not _repos().template_repo.get_by_id(m_data.custom_template_id):
                return _team_error("template_not_found", 404, template_id=m_data.custom_template_id)

    new_team = TeamDB(name=data.name, description=data.description, team_type_id=data.team_type_id, is_active=False)
    _repos().team_repo.save(new_team)

    # Mitglieder speichern
    if data.members:
        for m_data in data.members:
            member = TeamMemberDB(
                team_id=new_team.id,
                agent_url=m_data.agent_url,
                role_id=m_data.role_id,
                blueprint_role_id=m_data.blueprint_role_id,
                custom_template_id=m_data.custom_template_id,
            )
            _repos().team_member_repo.save(member)

    # Scrum Artefakte initialisieren falls es ein Scrum Team ist
    if data.team_type_id:
        team_type = _repos().team_type_repo.get_by_id(data.team_type_id)
        if team_type and team_type.name == "Scrum":
            initialize_scrum_artifacts(new_team.name, new_team.id)
    log_audit("team_created", {"team_id": new_team.id, "name": new_team.name})
    payload = new_team.model_dump()
    definition_metadata = team_definition_metadata(new_team)
    payload["definition_metadata"] = definition_metadata
    payload["user_lifecycle_state"] = _user_lifecycle_state_from_metadata(definition_metadata)
    return api_response(data=payload, code=201)


@teams_bp.route("/teams/<team_id>", methods=["PATCH"])
@check_auth
@admin_required
@validate_request(TeamUpdateRequest)
def update_team(team_id):
    data: TeamUpdateRequest = g.validated_data
    team = _repos().team_repo.get_by_id(team_id)

    if not team:
        return _team_error("not_found", 404)

    if data.name is not None:
        team.name = data.name
    if data.description is not None:
        team.description = data.description
    if data.team_type_id is not None:
        team.team_type_id = data.team_type_id

    if data.members is not None:
        # Validierung der Mitglieder-Rollen
        tt_id = data.team_type_id if data.team_type_id is not None else team.team_type_id
        if tt_id:
            allowed_role_ids = _repos().team_type_role_link_repo.get_allowed_role_ids(tt_id)
            if allowed_role_ids:
                for m_data in data.members:
                    if not m_data.role_id:
                        return _team_error("role_id_required", 400)
                    if not _repos().role_repo.get_by_id(m_data.role_id):
                        return _team_error("role_not_found", 404, role_id=m_data.role_id)
                    if m_data.role_id not in allowed_role_ids:
                        return _team_error("invalid_role_for_team_type", 400, role_id=m_data.role_id)
                    if m_data.custom_template_id and not _repos().template_repo.get_by_id(m_data.custom_template_id):
                        return _team_error("template_not_found", 404, template_id=m_data.custom_template_id)
        else:
            for m_data in data.members:
                if not m_data.role_id:
                    return _team_error("role_id_required", 400)
                if not _repos().role_repo.get_by_id(m_data.role_id):
                    return _team_error("role_not_found", 404, role_id=m_data.role_id)
                if m_data.custom_template_id and not _repos().template_repo.get_by_id(m_data.custom_template_id):
                    return _team_error("template_not_found", 404, template_id=m_data.custom_template_id)

        # Alte Mitglieder löschen und neue anlegen
        _repos().team_member_repo.delete_by_team(team_id)
        for m_data in data.members:
            member = TeamMemberDB(
                team_id=team_id,
                agent_url=m_data.agent_url,
                role_id=m_data.role_id,
                blueprint_role_id=m_data.blueprint_role_id,
                custom_template_id=m_data.custom_template_id,
            )
            _repos().team_member_repo.save(member)

    if data.is_active is True:
        # Alle anderen deaktivieren
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
        _repos().team_repo.save(team)
    else:
        _repos().team_repo.save(team)
    log_audit("team_updated", {"team_id": team_id})
    payload = team.model_dump()
    definition_metadata = team_definition_metadata(team)
    payload["definition_metadata"] = definition_metadata
    payload["user_lifecycle_state"] = _user_lifecycle_state_from_metadata(definition_metadata)
    return api_response(data=payload)


@teams_bp.route("/teams/setup-scrum", methods=["POST"])
@check_auth
@admin_required
@validate_request(TeamSetupScrumRequest)
def setup_scrum():
    """Erstellt ein Standard-Scrum-Team via Seed-Blueprint-Instantiation."""
    data: TeamSetupScrumRequest = g.validated_data
    team_name = data.name or "Neues Scrum Team"
    ensure_seed_blueprints()
    blueprint_name = str(data.blueprint_name or "Scrum").strip() or "Scrum"
    scrum_blueprint = _repos().team_blueprint_repo.get_by_name(blueprint_name)
    if not scrum_blueprint:
        return _team_error("scrum_blueprint_not_found", 404, blueprint_name=blueprint_name)

    instantiated = _instantiate_blueprint(
        scrum_blueprint,
        TeamBlueprintInstantiateRequest(
            name=team_name,
            description=f"Automatisch erstelltes Scrum Team aus dem Seed-Blueprint '{scrum_blueprint.name}'.",
            activate=True,
            members=[],
        ),
    )
    if isinstance(instantiated, tuple):
        return instantiated

    log_audit(
        "team_scrum_setup",
        {
            "team_id": instantiated.id,
            "name": instantiated.name,
            "blueprint_id": scrum_blueprint.id,
            "blueprint_name": scrum_blueprint.name,
        },
    )
    definition_metadata = team_definition_metadata(instantiated)
    return api_response(
        message=f"Scrum Team '{team_name}' wurde erfolgreich mit allen Templates und Artefakten angelegt.",
        data={
            "team": {
                **instantiated.model_dump(),
                "definition_metadata": definition_metadata,
                "user_lifecycle_state": _user_lifecycle_state_from_metadata(definition_metadata),
            },
            "blueprint": _serialize_blueprint(scrum_blueprint),
        },
        code=201,
    )


@teams_bp.route("/teams/roles", methods=["POST"])
@check_auth
@admin_required
@validate_request(RoleCreateRequest)
def create_role():
    data: RoleCreateRequest = g.validated_data
    new_role = RoleDB(name=data.name, description=data.description, default_template_id=data.default_template_id)
    _repos().role_repo.save(new_role)
    log_audit("role_created", {"role_id": new_role.id, "name": new_role.name})
    return api_response(data=new_role.model_dump(), code=201)


@teams_bp.route("/teams/types/<type_id>", methods=["DELETE"])
@check_auth
@admin_required
def delete_team_type(type_id):
    if _repos().team_type_repo.delete(type_id):
        log_audit("team_type_deleted", {"team_type_id": type_id})
        return api_response(data={"status": "deleted"})
    return _team_error("not_found", 404)


@teams_bp.route("/teams/types/<type_id>/roles/<role_id>", methods=["DELETE"])
@check_auth
@admin_required
def unlink_role_from_type(type_id, role_id):
    if _repos().team_type_role_link_repo.delete(type_id, role_id):
        log_audit("team_type_role_unlinked", {"team_type_id": type_id, "role_id": role_id})
        return api_response(data={"status": "unlinked"})
    return _team_error("not_found", 404)


@teams_bp.route("/teams/roles/<role_id>", methods=["DELETE"])
@check_auth
@admin_required
def delete_role(role_id):
    if _repos().role_repo.delete(role_id):
        log_audit("role_deleted", {"role_id": role_id})
        return api_response(data={"status": "deleted"})
    return _team_error("not_found", 404)


@teams_bp.route("/teams/<team_id>", methods=["DELETE"])
@check_auth
@admin_required
def delete_team(team_id):
    repos = _repos()
    team = repos.team_repo.get_by_id(team_id)
    if not team:
        return _team_error("not_found", 404)

    # Team-Mitglieder zuerst entfernen, damit FK-Constraints das Team-Delete nicht blockieren.
    repos.team_member_repo.delete_by_team(team_id)
    repos.task_repo.clear_team_assignments(team_id)
    repos.goal_repo.clear_team_assignments(team_id)

    if repos.team_repo.delete(team_id):
        log_audit("team_deleted", {"team_id": team_id})
        return api_response(data={"status": "deleted"})
    return _team_error("not_found", 404)


@teams_bp.route("/teams/<team_id>/activate", methods=["POST"])
@check_auth
@admin_required
def activate_team(team_id):
    with Session(engine) as session:
        team = session.get(TeamDB, team_id)
        if not team:
            return _team_error("not_found", 404)

        others = session.exec(select(TeamDB).where(TeamDB.id != team_id)).all()
        for other in others:
            other.is_active = False
            session.add(other)

        team.is_active = True
        session.add(team)
        session.commit()
        log_audit("team_activated", {"team_id": team_id})
        return api_response(data={"status": "activated"})
