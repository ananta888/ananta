import time
import uuid

from flask import Blueprint, g, request
from sqlmodel import Session, select

from agent.auth import admin_required, check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.database import engine
from agent.db_models import (
    BlueprintArtifactDB,
    BlueprintRoleDB,
    RoleDB,
    TaskDB,
    TeamBlueprintDB,
    TeamDB,
    TeamMemberDB,
    TeamTypeDB,
    TeamTypeRoleLink,
    TemplateDB,
)
from agent.models import (
    BlueprintArtifactDefinition,
    BlueprintRoleDefinition,
    RoleCreateRequest,
    TeamBlueprintCreateRequest,
    TeamBlueprintInstantiateRequest,
    TeamBlueprintUpdateRequest,
    TeamCreateRequest,
    TeamSetupScrumRequest,
    TeamTypeCreateRequest,
    TeamTypeRoleLinkCreateRequest,
    TeamTypeRoleLinkPatchRequest,
    TeamUpdateRequest,
)
from agent.repository import (
    agent_repo,
    blueprint_artifact_repo,
    blueprint_role_repo,
    role_repo,
    task_repo,
    team_member_repo,
    team_blueprint_repo,
    team_repo,
    team_type_repo,
    team_type_role_link_repo,
    template_repo,
)
from agent.utils import validate_request

teams_bp = Blueprint("teams", __name__)


def _team_error(message: str, code: int, **extra):
    """Return standardized API response with legacy compatibility."""
    return api_response(status="error", message=message, code=code, data=extra if extra else None)


SCRUM_INITIAL_TASKS = [
    {
        "title": "Scrum Backlog",
        "description": "Initiales Product Backlog für das Team.",
        "status": "backlog",
        "priority": "High",
    },
    {
        "title": "Sprint Board Setup",
        "description": "Visualisierung des aktuellen Sprints.",
        "status": "todo",
        "priority": "High",
    },
    {
        "title": "Burndown Chart",
        "description": "Tracken des Fortschritts im Sprint.",
        "status": "todo",
        "priority": "Medium",
    },
    {
        "title": "Roadmap",
        "description": "Langfristige Planung und Meilensteine.",
        "status": "backlog",
        "priority": "Medium",
    },
    {
        "title": "Setup & Usage Instructions",
        "description": """### Setup & Usage Instructions
1. Clone the template repository and create a new repository based on the cloned one.
2. Customize the template by updating the content of the README file and any other files you see fit.
3. Add your teammates as collaborators to the repository.
4. Set up an integration (e.g., with GitHub Actions) to automate the
creation of a new sprint branch whenever the team is ready to start a new sprint.
5. Set up your project and workflow in the Bitte interface, such as
assigning work items to team members or setting up notifications for completed tasks.
6. Use the template’s sprint board to plan and execute on each sprint.
7. Use the burndown chart to track progress towards completing user stories and reaching your sprint goals.
8. Use the roadmap to visualize upcoming milestones and help teams plan their work accordingly.
9. Use Bitte’s project and team settings to manage your team,
such as setting up access levels or adding new members to your team.""",
        "status": "todo",
        "priority": "High",
    },
]

KANBAN_INITIAL_TASKS = [
    {
        "title": "Kanban Board",
        "description": "Visualisierung des aktuellen Flusses und der WIP-Limits.",
        "status": "todo",
        "priority": "High",
    },
    {
        "title": "Flow Metrics Review",
        "description": "Durchsatz, Lead Time und Blocker regelmaessig ueberpruefen.",
        "status": "todo",
        "priority": "Medium",
    },
]

SEED_BLUEPRINTS = {
    "Scrum": {
        "description": "Standard Scrum blueprint with canonical Scrum roles and starter artifacts.",
        "roles": [
            {
                "name": "Product Owner",
                "description": "Owns the backlog and prioritization.",
                "template_name": "Scrum - Product Owner",
                "sort_order": 10,
                "is_required": True,
                "config": {"responsibility": "backlog"},
            },
            {
                "name": "Scrum Master",
                "description": "Facilitates the Scrum process.",
                "template_name": "Scrum - Scrum Master",
                "sort_order": 20,
                "is_required": True,
                "config": {"responsibility": "facilitation"},
            },
            {
                "name": "Developer",
                "description": "Builds and delivers backlog items.",
                "template_name": "Scrum - Developer",
                "sort_order": 30,
                "is_required": True,
                "config": {"responsibility": "delivery"},
            },
        ],
        "artifacts": [
            {
                "kind": "task",
                "title": task["title"],
                "description": task["description"],
                "sort_order": index * 10,
                "payload": {"status": task["status"], "priority": task["priority"]},
            }
            for index, task in enumerate(SCRUM_INITIAL_TASKS, start=1)
        ],
    },
    "Kanban": {
        "description": "Standard Kanban blueprint with flow-oriented roles and starter artifacts.",
        "roles": [
            {
                "name": "Service Delivery Manager",
                "description": "Oversees service delivery and flow metrics.",
                "template_name": "Kanban - Service Delivery Manager",
                "sort_order": 10,
                "is_required": True,
                "config": {"responsibility": "service_delivery"},
            },
            {
                "name": "Flow Manager",
                "description": "Optimizes WIP limits and flow.",
                "template_name": "Kanban - Flow Manager",
                "sort_order": 20,
                "is_required": True,
                "config": {"responsibility": "flow_management"},
            },
            {
                "name": "Developer",
                "description": "Delivers work items and maintains quality.",
                "template_name": "Kanban - Developer",
                "sort_order": 30,
                "is_required": True,
                "config": {"responsibility": "delivery"},
            },
        ],
        "artifacts": [
            {
                "kind": "task",
                "title": task["title"],
                "description": task["description"],
                "sort_order": index * 10,
                "payload": {"status": task["status"], "priority": task["priority"]},
            }
            for index, task in enumerate(KANBAN_INITIAL_TASKS, start=1)
        ],
    },
}

SCRUM_SOLID_TEMPLATE_APPENDIX = """

Engineering guardrails for every proposal, change, refactoring, and implementation:

- Act as a senior software engineer and architect.
- Apply SOLID strictly and actively:
  - SRP: keep each class, module, and function focused on one responsibility.
  - OCP: prefer extension through interfaces, composition, strategies, policies, adapters, or new implementations.
  - LSP: keep contracts substitutable without hidden side effects or stronger preconditions.
  - ISP: prefer small, focused interfaces.
  - DIP: depend on abstractions, not concrete implementations.
- Also enforce:
  - clean separation of business logic, infrastructure, persistence, API, and configuration
  - composition over inheritance
  - low coupling, minimal global state, and testable seams
  - precise naming, small understandable functions, and maintainable structure
- Before finalizing a change, explicitly check for:
  - SRP violations
  - overly strong coupling
  - missing abstractions
  - interfaces that are too broad
  - poor substitutability
  - hidden side effects
  - structures that are hard to test
- If one of these issues exists:
  1. name the problem
  2. name the affected SOLID principle
  3. propose a better structure
  4. only then provide the final code
- Do not deliver merely working code. Deliver robust, modular, extensible, testable, and maintainable solutions.
""".strip()


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
    import time

    from agent.db_models import TaskDB
    from agent.repository import task_repo

    for task_data in SCRUM_INITIAL_TASKS:
        new_task = TaskDB(
            id=str(uuid.uuid4()),
            title=f"{team_name}: {task_data['title']}",
            description=task_data["description"],
            status=task_data["status"],
            priority=task_data["priority"],
            created_at=time.time(),
            updated_at=time.time(),
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
        elif tpl.prompt_template != prompt_template:
            tpl.description = description
            tpl.prompt_template = prompt_template
            template_repo.save(tpl)
        return tpl

    def ensure_role_links(role_definitions: list[tuple[str, str, TemplateDB]]):
        from sqlmodel import Session, select

        from agent.database import engine

        for role_name, role_desc, tpl in role_definitions:
            role = role_repo.get_by_name(role_name)
            if not role:
                role = RoleDB(name=role_name, description=role_desc, default_template_id=tpl.id)
                role_repo.save(role)
            elif role.default_template_id is None:
                role.default_template_id = tpl.id
                role_repo.save(role)

            with Session(engine) as session:
                link = session.exec(
                    select(TeamTypeRoleLink).where(
                        TeamTypeRoleLink.team_type_id == tt.id, TeamTypeRoleLink.role_id == role.id
                    )
                ).first()
                if not link:
                    link = TeamTypeRoleLink(team_type_id=tt.id, role_id=role.id, template_id=tpl.id)
                    session.add(link)
                    session.commit()

    if team_type_name == "Scrum":
        scrum_po_tpl = ensure_template(
            "Scrum - Product Owner",
            "Prompt template for Scrum Product Owner.",
            (
                "You are the Product Owner in a Scrum team. Align backlog, priorities, "
                "and acceptance criteria with {{team_goal}}.\n\n"
                f"{SCRUM_SOLID_TEMPLATE_APPENDIX}"
            ),
        )
        scrum_sm_tpl = ensure_template(
            "Scrum - Scrum Master",
            "Prompt template for Scrum Master.",
            (
                "You are the Scrum Master for a Scrum team. Facilitate events, "
                "remove blockers, and improve flow toward {{team_goal}}.\n\n"
                f"{SCRUM_SOLID_TEMPLATE_APPENDIX}"
            ),
        )
        scrum_dev_tpl = ensure_template(
            "Scrum - Developer",
            "Prompt template for Scrum Developer.",
            (
                "You are a Developer in a Scrum team. Implement backlog items, "
                "review work, and deliver increments for {{team_goal}}.\n\n"
                f"{SCRUM_SOLID_TEMPLATE_APPENDIX}"
            ),
        )
        ensure_role_links(
            [
                ("Product Owner", "Owns the backlog and prioritization.", scrum_po_tpl),
                ("Scrum Master", "Facilitates the Scrum process.", scrum_sm_tpl),
                ("Developer", "Builds and delivers backlog items.", scrum_dev_tpl),
            ]
        )

    if team_type_name == "Kanban":
        kanban_sdm_tpl = ensure_template(
            "Kanban - Service Delivery Manager",
            "Prompt template for Kanban Service Delivery Manager.",
            (
                "You are the Service Delivery Manager in a Kanban team. Monitor flow metrics "
                "and service delivery toward {{team_goal}}."
            ),
        )
        kanban_flow_tpl = ensure_template(
            "Kanban - Flow Manager",
            "Prompt template for Kanban Flow Manager.",
            "You are the Flow Manager in a Kanban team. Optimize WIP, policies, and flow to achieve {{team_goal}}.",
        )
        kanban_dev_tpl = ensure_template(
            "Kanban - Developer",
            "Prompt template for Kanban Developer.",
            (
                "You are a Developer in a Kanban team. Deliver work items, limit WIP, "
                "and maintain quality for {{team_goal}}."
            ),
        )
        ensure_role_links(
            [
                ("Service Delivery Manager", "Oversees service delivery and flow metrics.", kanban_sdm_tpl),
                ("Flow Manager", "Optimizes WIP limits and flow.", kanban_flow_tpl),
                ("Developer", "Delivers work items and maintains quality.", kanban_dev_tpl),
            ]
        )


def _serialize_blueprint(
    blueprint: TeamBlueprintDB,
    roles: list[BlueprintRoleDB] | None = None,
    artifacts: list[BlueprintArtifactDB] | None = None,
) -> dict:
    blueprint_dict = blueprint.model_dump()
    blueprint_roles = roles if roles is not None else blueprint_role_repo.get_by_blueprint(blueprint.id)
    blueprint_artifacts = artifacts if artifacts is not None else blueprint_artifact_repo.get_by_blueprint(blueprint.id)
    blueprint_dict["roles"] = [role.model_dump() for role in blueprint_roles]
    blueprint_dict["artifacts"] = [artifact.model_dump() for artifact in blueprint_artifacts]
    return blueprint_dict


def _validate_blueprint_roles(roles: list) -> tuple[bool, tuple | None]:
    seen_names: set[str] = set()
    for role in roles:
        normalized_name = role.name.strip()
        if not normalized_name:
            return False, ("blueprint_role_name_required", 400, {})
        if normalized_name.lower() in seen_names:
            return False, ("duplicate_blueprint_role_name", 400, {"role_name": normalized_name})
        seen_names.add(normalized_name.lower())
        if role.template_id and not template_repo.get_by_id(role.template_id):
            return False, ("template_not_found", 404, {"template_id": role.template_id})
    return True, None


def _validate_blueprint_artifacts(artifacts: list) -> tuple[bool, tuple | None]:
    for artifact in artifacts:
        if not artifact.kind.strip():
            return False, ("blueprint_artifact_kind_required", 400, {})
        if not artifact.title.strip():
            return False, ("blueprint_artifact_title_required", 400, {})
    return True, None


def _persist_blueprint_children(
    blueprint_id: str,
    role_definitions: list | None,
    artifact_definitions: list | None,
) -> tuple[list[BlueprintRoleDB], list[BlueprintArtifactDB]]:
    persisted_roles: list[BlueprintRoleDB] = blueprint_role_repo.get_by_blueprint(blueprint_id)
    persisted_artifacts: list[BlueprintArtifactDB] = blueprint_artifact_repo.get_by_blueprint(blueprint_id)

    if role_definitions is not None:
        blueprint_role_repo.delete_by_blueprint(blueprint_id)
        persisted_roles = []
        for role_def in role_definitions:
            persisted_roles.append(
                blueprint_role_repo.save(
                    BlueprintRoleDB(
                        blueprint_id=blueprint_id,
                        name=role_def.name.strip(),
                        description=role_def.description,
                        template_id=role_def.template_id,
                        sort_order=role_def.sort_order,
                        is_required=role_def.is_required,
                        config=role_def.config,
                    )
                )
            )

    if artifact_definitions is not None:
        blueprint_artifact_repo.delete_by_blueprint(blueprint_id)
        persisted_artifacts = []
        for artifact_def in artifact_definitions:
            persisted_artifacts.append(
                blueprint_artifact_repo.save(
                    BlueprintArtifactDB(
                        blueprint_id=blueprint_id,
                        kind=artifact_def.kind.strip(),
                        title=artifact_def.title.strip(),
                        description=artifact_def.description,
                        sort_order=artifact_def.sort_order,
                        payload=artifact_def.payload,
                    )
                )
            )

    return persisted_roles, persisted_artifacts


def ensure_seed_blueprints() -> None:
    for blueprint_name, blueprint_definition in SEED_BLUEPRINTS.items():
        ensure_default_templates(blueprint_name)
        templates_by_name = {template.name: template for template in template_repo.get_all()}
        blueprint = team_blueprint_repo.get_by_name(blueprint_name)
        existing_roles = blueprint_role_repo.get_by_blueprint(blueprint.id) if blueprint else []
        existing_artifacts = blueprint_artifact_repo.get_by_blueprint(blueprint.id) if blueprint else []
        if not blueprint:
            blueprint = TeamBlueprintDB(
                name=blueprint_name,
                description=blueprint_definition["description"],
                base_team_type_name=blueprint_name,
                is_seed=True,
            )
            blueprint = team_blueprint_repo.save(blueprint)
        elif (
            blueprint.description != blueprint_definition["description"]
            or blueprint.base_team_type_name != blueprint_name
            or blueprint.is_seed is not True
        ):
            blueprint.description = blueprint_definition["description"]
            blueprint.base_team_type_name = blueprint_name
            blueprint.is_seed = True
            blueprint.updated_at = time.time()
            blueprint = team_blueprint_repo.save(blueprint)

        if existing_roles and existing_artifacts:
            continue

        role_definitions = []
        for role_definition in blueprint_definition["roles"]:
            template = templates_by_name.get(role_definition["template_name"])
            role_definitions.append(
                BlueprintRoleDefinition(
                    name=role_definition["name"],
                    description=role_definition["description"],
                    template_id=template.id if template else None,
                    sort_order=role_definition["sort_order"],
                    is_required=role_definition["is_required"],
                    config=role_definition["config"],
                )
            )

        artifact_definitions = []
        for artifact_definition in blueprint_definition["artifacts"]:
            artifact_definitions.append(
                BlueprintArtifactDefinition(
                    kind=artifact_definition["kind"],
                    title=artifact_definition["title"],
                    description=artifact_definition["description"],
                    sort_order=artifact_definition["sort_order"],
                    payload=artifact_definition["payload"],
                )
            )

        _persist_blueprint_children(blueprint.id, role_definitions, artifact_definitions)


def _ensure_role_for_blueprint_role(team_type_id: str | None, blueprint_role: BlueprintRoleDB) -> RoleDB:
    role = role_repo.get_by_name(blueprint_role.name)
    if not role:
        role = RoleDB(
            name=blueprint_role.name,
            description=blueprint_role.description,
            default_template_id=blueprint_role.template_id,
        )
    else:
        if blueprint_role.description and not role.description:
            role.description = blueprint_role.description
        if blueprint_role.template_id and role.default_template_id is None:
            role.default_template_id = blueprint_role.template_id
    role = role_repo.save(role)

    if team_type_id:
        with Session(engine) as session:
            link = session.exec(
                select(TeamTypeRoleLink).where(
                    TeamTypeRoleLink.team_type_id == team_type_id,
                    TeamTypeRoleLink.role_id == role.id,
                )
            ).first()
            if not link:
                link = TeamTypeRoleLink(
                    team_type_id=team_type_id,
                    role_id=role.id,
                    template_id=blueprint_role.template_id or role.default_template_id,
                )
                session.add(link)
                session.commit()
            elif blueprint_role.template_id and link.template_id != blueprint_role.template_id:
                link.template_id = blueprint_role.template_id
                session.add(link)
                session.commit()

    return role


def _materialize_blueprint_artifacts(team: TeamDB, blueprint_artifacts: list[BlueprintArtifactDB]) -> None:
    for artifact in blueprint_artifacts:
        if artifact.kind != "task":
            continue
        payload = artifact.payload or {}
        task_repo.save(
            TaskDB(
                id=str(uuid.uuid4()),
                title=f"{team.name}: {artifact.title}",
                description=artifact.description,
                status=payload.get("status", "todo"),
                priority=payload.get("priority", "Medium"),
                created_at=time.time(),
                updated_at=time.time(),
                team_id=team.id,
            )
        )


def _instantiate_blueprint(blueprint: TeamBlueprintDB, data: TeamBlueprintInstantiateRequest) -> TeamDB | tuple:
    blueprint_roles = blueprint_role_repo.get_by_blueprint(blueprint.id)
    blueprint_artifacts = blueprint_artifact_repo.get_by_blueprint(blueprint.id)

    team_type = None
    normalized_type_name = normalize_team_type_name(blueprint.base_team_type_name or "")
    if normalized_type_name:
        ensure_default_templates(normalized_type_name)
        team_type = team_type_repo.get_by_name(normalized_type_name)
        if not team_type:
            return _team_error("team_type_not_found", 404, team_type_name=normalized_type_name)

    blueprint_role_map = {role.id: role for role in blueprint_roles}
    role_bindings: dict[str, str] = {}
    for blueprint_role in blueprint_roles:
        role_bindings[blueprint_role.id] = _ensure_role_for_blueprint_role(team_type.id if team_type else None, blueprint_role).id

    members_payload = []
    for member in data.members:
        role_id = member.role_id
        if member.blueprint_role_id:
            blueprint_role = blueprint_role_map.get(member.blueprint_role_id)
            if not blueprint_role:
                return _team_error("blueprint_role_not_found", 404, blueprint_role_id=member.blueprint_role_id)
            role_id = role_bindings.get(member.blueprint_role_id)
        if not role_id:
            return _team_error("role_id_required", 400)
        if not role_repo.get_by_id(role_id):
            return _team_error("role_not_found", 404, role_id=role_id)
        if member.custom_template_id and not template_repo.get_by_id(member.custom_template_id):
            return _team_error("template_not_found", 404, template_id=member.custom_template_id)
        members_payload.append((member, role_id))

    snapshot = _serialize_blueprint(blueprint, roles=blueprint_roles, artifacts=blueprint_artifacts)
    team = TeamDB(
        name=data.name,
        description=data.description or blueprint.description,
        team_type_id=team_type.id if team_type else None,
        blueprint_id=blueprint.id,
        is_active=data.activate,
        role_templates={role_bindings[role.id]: role.template_id for role in blueprint_roles if role_bindings.get(role.id)},
        blueprint_snapshot=snapshot,
    )

    if data.activate:
        with Session(engine) as session:
            for other in session.exec(select(TeamDB)).all():
                other.is_active = False
                session.add(other)
            session.commit()

    team = team_repo.save(team)

    for member, role_id in members_payload:
        team_member_repo.save(
            TeamMemberDB(
                team_id=team.id,
                agent_url=member.agent_url,
                role_id=role_id,
                blueprint_role_id=member.blueprint_role_id,
                custom_template_id=member.custom_template_id,
            )
        )

    _materialize_blueprint_artifacts(team, blueprint_artifacts)
    return team


@teams_bp.route("/teams/blueprints", methods=["GET"])
@check_auth
def list_team_blueprints():
    ensure_seed_blueprints()
    blueprints = team_blueprint_repo.get_all()
    return api_response(data=[_serialize_blueprint(blueprint) for blueprint in blueprints])


@teams_bp.route("/teams/blueprints/<blueprint_id>", methods=["GET"])
@check_auth
def get_team_blueprint(blueprint_id):
    ensure_seed_blueprints()
    blueprint = team_blueprint_repo.get_by_id(blueprint_id)
    if not blueprint:
        return _team_error("not_found", 404)
    return api_response(data=_serialize_blueprint(blueprint))


@teams_bp.route("/teams/blueprints", methods=["POST"])
@check_auth
@admin_required
@validate_request(TeamBlueprintCreateRequest)
def create_team_blueprint():
    data: TeamBlueprintCreateRequest = g.validated_data
    blueprint_name = data.name.strip()
    if not blueprint_name:
        return _team_error("blueprint_name_required", 400)
    if team_blueprint_repo.get_by_name(blueprint_name):
        return _team_error("blueprint_name_exists", 409, name=blueprint_name)

    valid, error = _validate_blueprint_roles(data.roles)
    if not valid:
        return _team_error(error[0], error[1], **error[2])
    valid, error = _validate_blueprint_artifacts(data.artifacts)
    if not valid:
        return _team_error(error[0], error[1], **error[2])

    normalized_type_name = normalize_team_type_name(data.base_team_type_name or "")
    if normalized_type_name:
        ensure_default_templates(normalized_type_name)

    blueprint = team_blueprint_repo.save(
        TeamBlueprintDB(
            name=blueprint_name,
            description=data.description,
            base_team_type_name=normalized_type_name or None,
            is_seed=False,
        )
    )
    roles, artifacts = _persist_blueprint_children(blueprint.id, data.roles, data.artifacts)
    log_audit("team_blueprint_created", {"blueprint_id": blueprint.id, "name": blueprint.name})
    return api_response(data=_serialize_blueprint(blueprint, roles=roles, artifacts=artifacts), code=201)


@teams_bp.route("/teams/blueprints/<blueprint_id>", methods=["PATCH"])
@check_auth
@admin_required
@validate_request(TeamBlueprintUpdateRequest)
def update_team_blueprint(blueprint_id):
    data: TeamBlueprintUpdateRequest = g.validated_data
    blueprint = team_blueprint_repo.get_by_id(blueprint_id)
    if not blueprint:
        return _team_error("not_found", 404)

    if data.name is not None and data.name.strip() != blueprint.name:
        if not data.name.strip():
            return _team_error("blueprint_name_required", 400)
        existing = team_blueprint_repo.get_by_name(data.name.strip())
        if existing and existing.id != blueprint_id:
            return _team_error("blueprint_name_exists", 409, name=data.name.strip())
        blueprint.name = data.name.strip()
    if data.description is not None:
        blueprint.description = data.description
    if data.base_team_type_name is not None:
        normalized_type_name = normalize_team_type_name(data.base_team_type_name)
        if normalized_type_name:
            ensure_default_templates(normalized_type_name)
        blueprint.base_team_type_name = normalized_type_name or None

    if data.roles is not None:
        valid, error = _validate_blueprint_roles(data.roles)
        if not valid:
            return _team_error(error[0], error[1], **error[2])
    if data.artifacts is not None:
        valid, error = _validate_blueprint_artifacts(data.artifacts)
        if not valid:
            return _team_error(error[0], error[1], **error[2])

    blueprint.updated_at = time.time()
    blueprint = team_blueprint_repo.save(blueprint)
    roles, artifacts = _persist_blueprint_children(blueprint.id, data.roles, data.artifacts)
    log_audit("team_blueprint_updated", {"blueprint_id": blueprint.id})
    return api_response(data=_serialize_blueprint(blueprint, roles=roles, artifacts=artifacts))


@teams_bp.route("/teams/blueprints/<blueprint_id>", methods=["DELETE"])
@check_auth
@admin_required
def delete_team_blueprint(blueprint_id):
    blueprint_artifact_repo.delete_by_blueprint(blueprint_id)
    blueprint_role_repo.delete_by_blueprint(blueprint_id)
    if team_blueprint_repo.delete(blueprint_id):
        log_audit("team_blueprint_deleted", {"blueprint_id": blueprint_id})
        return api_response(data={"status": "deleted"})
    return _team_error("not_found", 404)


@teams_bp.route("/teams/blueprints/<blueprint_id>/instantiate", methods=["POST"])
@check_auth
@admin_required
@validate_request(TeamBlueprintInstantiateRequest)
def instantiate_team_blueprint(blueprint_id):
    ensure_seed_blueprints()
    blueprint = team_blueprint_repo.get_by_id(blueprint_id)
    if not blueprint:
        return _team_error("not_found", 404)

    data: TeamBlueprintInstantiateRequest = g.validated_data
    instantiated = _instantiate_blueprint(blueprint, data)
    if isinstance(instantiated, tuple):
        return instantiated

    log_audit("team_blueprint_instantiated", {"blueprint_id": blueprint_id, "team_id": instantiated.id})
    return api_response(data={"team": instantiated.model_dump(), "blueprint": _serialize_blueprint(blueprint)}, code=201)


@teams_bp.route("/teams/roles", methods=["GET"])
@check_auth
def get_team_roles():
    roles = role_repo.get_all()
    return api_response(data=[r.model_dump() for r in roles])


@teams_bp.route("/teams/types", methods=["GET"])
@check_auth
def list_team_types():
    types = team_type_repo.get_all()
    if not types:
        ensure_default_templates("Scrum")
        ensure_default_templates("Kanban")
        ensure_seed_blueprints()
        types = team_type_repo.get_all()
    result = []
    for t in types:
        td = t.model_dump()
        td["role_ids"] = team_type_role_link_repo.get_allowed_role_ids(t.id)
        from sqlmodel import Session, select

        from agent.database import engine

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
    team_type_repo.save(new_type)
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

    if not role_repo.get_by_id(role_id):
        return _team_error("role_not_found", 404)
    if template_id and not template_repo.get_by_id(template_id):
        return _team_error("template_not_found", 404)

    from sqlmodel import Session

    from agent.database import engine

    with Session(engine) as session:
        link = TeamTypeRoleLink(team_type_id=type_id, role_id=role_id, template_id=template_id)
        session.add(link)
        session.commit()
    log_audit("team_type_role_linked", {"team_type_id": type_id, "role_id": role_id, "template_id": template_id})
    return api_response(data={"status": "linked"})


@teams_bp.route("/teams/types/<type_id>/roles", methods=["GET"])
@check_auth
def list_roles_for_type(type_id):
    from sqlmodel import Session, select

    from agent.database import engine

    with Session(engine) as session:
        links = session.exec(select(TeamTypeRoleLink).where(TeamTypeRoleLink.team_type_id == type_id)).all()
    result = []
    for link in links:
        role = role_repo.get_by_id(link.role_id)
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
    if template_id and not template_repo.get_by_id(template_id):
        return _team_error("template_not_found", 404)

    from sqlmodel import Session, select

    from agent.database import engine

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
    teams = team_repo.get_all()
    result = []
    for t in teams:
        team_dict = t.model_dump()
        # Mitglieder laden
        members = team_member_repo.get_by_team(t.id)
        team_dict["members"] = [m.model_dump() for m in members]
        result.append(team_dict)
    return api_response(data=result)


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
            return _team_error("team_type_not_found", 404)
        if team_type:
            ensure_default_templates(team_type.name)

    # Validierung der Mitglieder-Rollen
    if data.members and data.team_type_id:
        allowed_role_ids = team_type_role_link_repo.get_allowed_role_ids(data.team_type_id)
        if allowed_role_ids:
            for m_data in data.members:
                if not m_data.role_id:
                    return _team_error("role_id_required", 400)
                if not role_repo.get_by_id(m_data.role_id):
                    return _team_error("role_not_found", 404, role_id=m_data.role_id)
                if m_data.role_id not in allowed_role_ids:
                    return _team_error("invalid_role_for_team_type", 400, role_id=m_data.role_id)
                if m_data.custom_template_id and not template_repo.get_by_id(m_data.custom_template_id):
                    return _team_error("template_not_found", 404, template_id=m_data.custom_template_id)
    if data.members and not data.team_type_id:
        for m_data in data.members:
            if not m_data.role_id:
                return _team_error("role_id_required", 400)
            if not role_repo.get_by_id(m_data.role_id):
                return _team_error("role_not_found", 404, role_id=m_data.role_id)
            if m_data.custom_template_id and not template_repo.get_by_id(m_data.custom_template_id):
                return _team_error("template_not_found", 404, template_id=m_data.custom_template_id)

    new_team = TeamDB(name=data.name, description=data.description, team_type_id=data.team_type_id, is_active=False)
    team_repo.save(new_team)

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
            team_member_repo.save(member)

    # Scrum Artefakte initialisieren falls es ein Scrum Team ist
    if data.team_type_id:
        team_type = team_type_repo.get_by_id(data.team_type_id)
        if team_type and team_type.name == "Scrum":
            initialize_scrum_artifacts(new_team.name, new_team.id)
    log_audit("team_created", {"team_id": new_team.id, "name": new_team.name})
    return api_response(data=new_team.model_dump(), code=201)


@teams_bp.route("/teams/<team_id>", methods=["PATCH"])
@check_auth
@admin_required
@validate_request(TeamUpdateRequest)
def update_team(team_id):
    data: TeamUpdateRequest = g.validated_data
    team = team_repo.get_by_id(team_id)

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
            allowed_role_ids = team_type_role_link_repo.get_allowed_role_ids(tt_id)
            if allowed_role_ids:
                for m_data in data.members:
                    if not m_data.role_id:
                        return _team_error("role_id_required", 400)
                    if not role_repo.get_by_id(m_data.role_id):
                        return _team_error("role_not_found", 404, role_id=m_data.role_id)
                    if m_data.role_id not in allowed_role_ids:
                        return _team_error("invalid_role_for_team_type", 400, role_id=m_data.role_id)
                    if m_data.custom_template_id and not template_repo.get_by_id(m_data.custom_template_id):
                        return _team_error("template_not_found", 404, template_id=m_data.custom_template_id)
        else:
            for m_data in data.members:
                if not m_data.role_id:
                    return _team_error("role_id_required", 400)
                if not role_repo.get_by_id(m_data.role_id):
                    return _team_error("role_not_found", 404, role_id=m_data.role_id)
                if m_data.custom_template_id and not template_repo.get_by_id(m_data.custom_template_id):
                    return _team_error("template_not_found", 404, template_id=m_data.custom_template_id)

        # Alte Mitglieder löschen und neue anlegen
        team_member_repo.delete_by_team(team_id)
        for m_data in data.members:
            member = TeamMemberDB(
                team_id=team_id,
                agent_url=m_data.agent_url,
                role_id=m_data.role_id,
                blueprint_role_id=m_data.blueprint_role_id,
                custom_template_id=m_data.custom_template_id,
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
    return api_response(data=team.model_dump())


@teams_bp.route("/teams/setup-scrum", methods=["POST"])
@check_auth
@admin_required
@validate_request(TeamSetupScrumRequest)
def setup_scrum():
    """Erstellt ein Standard-Scrum-Team via Seed-Blueprint-Instantiation."""
    data: TeamSetupScrumRequest = g.validated_data
    team_name = data.name or "Neues Scrum Team"
    ensure_seed_blueprints()
    scrum_blueprint = team_blueprint_repo.get_by_name("Scrum")
    if not scrum_blueprint:
        return _team_error("scrum_blueprint_not_found", 404)

    instantiated = _instantiate_blueprint(
        scrum_blueprint,
        TeamBlueprintInstantiateRequest(
            name=team_name,
            description="Automatisch erstelltes Scrum Team aus dem Seed-Blueprint.",
            activate=True,
            members=[],
        ),
    )
    if isinstance(instantiated, tuple):
        return instantiated

    log_audit("team_scrum_setup", {"team_id": instantiated.id, "name": instantiated.name, "blueprint_id": scrum_blueprint.id})
    return api_response(
        message=f"Scrum Team '{team_name}' wurde erfolgreich mit allen Templates und Artefakten angelegt.",
        data={"team": instantiated.model_dump(), "blueprint": _serialize_blueprint(scrum_blueprint)},
        code=201,
    )


@teams_bp.route("/teams/roles", methods=["POST"])
@check_auth
@admin_required
@validate_request(RoleCreateRequest)
def create_role():
    data: RoleCreateRequest = g.validated_data
    new_role = RoleDB(name=data.name, description=data.description, default_template_id=data.default_template_id)
    role_repo.save(new_role)
    log_audit("role_created", {"role_id": new_role.id, "name": new_role.name})
    return api_response(data=new_role.model_dump(), code=201)


@teams_bp.route("/teams/types/<type_id>", methods=["DELETE"])
@check_auth
@admin_required
def delete_team_type(type_id):
    if team_type_repo.delete(type_id):
        log_audit("team_type_deleted", {"team_type_id": type_id})
        return api_response(data={"status": "deleted"})
    return _team_error("not_found", 404)


@teams_bp.route("/teams/types/<type_id>/roles/<role_id>", methods=["DELETE"])
@check_auth
@admin_required
def unlink_role_from_type(type_id, role_id):
    if team_type_role_link_repo.delete(type_id, role_id):
        log_audit("team_type_role_unlinked", {"team_type_id": type_id, "role_id": role_id})
        return api_response(data={"status": "unlinked"})
    return _team_error("not_found", 404)


@teams_bp.route("/teams/roles/<role_id>", methods=["DELETE"])
@check_auth
@admin_required
def delete_role(role_id):
    if role_repo.delete(role_id):
        log_audit("role_deleted", {"role_id": role_id})
        return api_response(data={"status": "deleted"})
    return _team_error("not_found", 404)


@teams_bp.route("/teams/<team_id>", methods=["DELETE"])
@check_auth
@admin_required
def delete_team(team_id):
    if team_repo.delete(team_id):
        log_audit("team_deleted", {"team_id": team_id})
        return api_response(data={"status": "deleted"})
    return _team_error("not_found", 404)


@teams_bp.route("/teams/<team_id>/activate", methods=["POST"])
@check_auth
@admin_required
def activate_team(team_id):
    from sqlmodel import Session, select

    from agent.database import engine

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
