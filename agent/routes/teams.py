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
    BlueprintBundleDefinition,
    BlueprintBundleMemberAssignment,
    BlueprintBundleTeamDefinition,
    BlueprintArtifactDefinition,
    BlueprintRoleDefinition,
    RoleCreateRequest,
    TeamBlueprintBundleImportRequest,
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
from agent.services.blueprint_bundle_service import (
    BUNDLE_SCHEMA_VERSION,
    build_bundle_import_plan,
    export_blueprint_bundle,
    normalize_bundle_mode,
    normalize_bundle_parts,
    validate_bundle_mode_and_parts,
)
from agent.services.repository_registry import get_repository_registry
from agent.services.team_blueprint_service import (
    RoleLinkSpec,
    TemplateBootstrapSpec,
    ensure_default_templates as ensure_default_templates_service,
    instantiate_blueprint as instantiate_blueprint_service,
    persist_blueprint_children as persist_blueprint_children_service,
    reconcile_seed_blueprints as reconcile_seed_blueprints_service,
    save_blueprint as save_blueprint_service,
)
from agent.utils import validate_request

teams_bp = Blueprint("teams", __name__)


def _repos():
    return get_repository_registry()


def _team_error(message: str, code: int, **extra):
    """Return standardized API response with legacy compatibility."""
    return api_response(status="error", message=message, code=code, data=extra if extra else None)


def _parse_bool_query(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_parts_query(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _bundle_plan_error_response(plan) -> tuple:
    has_conflict = any(error.get("type") == "conflict" for error in plan.errors)
    return _team_error(
        "bundle_import_conflict" if has_conflict else "bundle_import_invalid",
        409 if has_conflict else 400,
        errors=plan.errors,
        diff=plan.diff,
        summary=plan.summary,
        parts=plan.parts,
        mode=plan.mode,
        schema_version=plan.schema_version,
    )


def _resolve_bundle_template_by_name(imported_templates: dict[str, TemplateDB], template_name: str | None) -> TemplateDB | None:
    if not template_name:
        return None
    normalized_name = template_name.strip()
    if not normalized_name:
        return None
    return imported_templates.get(normalized_name) or _repos().template_repo.get_by_name(normalized_name)


def _resolve_bundle_blueprint_role_id(blueprint_roles: list[BlueprintRoleDB], blueprint_role_name: str | None) -> str | None:
    if not blueprint_role_name:
        return None
    normalized_name = blueprint_role_name.strip().lower()
    for blueprint_role in blueprint_roles:
        if blueprint_role.name.strip().lower() == normalized_name:
            return blueprint_role.id
    return None


def _apply_bundle_team_members(team_id: str, team_type_id: str | None, blueprint_roles: list[BlueprintRoleDB], members: list[BlueprintBundleMemberAssignment], imported_templates: dict[str, TemplateDB]) -> tuple[bool, tuple | None]:
    allowed_role_ids = _repos().team_type_role_link_repo.get_allowed_role_ids(team_type_id) if team_type_id else []
    _repos().team_member_repo.delete_by_team(team_id)
    for member in members:
        role_name = (member.role_name or "").strip()
        role = _repos().role_repo.get_by_name(role_name) if role_name else None
        if role is None:
            return False, _team_error("role_not_found", 404, role_name=role_name)
        if allowed_role_ids and role.id not in allowed_role_ids:
            return False, _team_error("invalid_role_for_team_type", 400, role_id=role.id)
        template = _resolve_bundle_template_by_name(imported_templates, member.custom_template_name)
        if member.custom_template_name and template is None:
            return False, _team_error("template_not_found", 404, template_name=member.custom_template_name)
        blueprint_role_id = _resolve_bundle_blueprint_role_id(blueprint_roles, member.blueprint_role_name)
        if member.blueprint_role_name and blueprint_role_id is None:
            return False, _team_error("blueprint_role_not_found", 404, blueprint_role_name=member.blueprint_role_name)
        _repos().team_member_repo.save(
            TeamMemberDB(
                team_id=team_id,
                agent_url=member.agent_url,
                role_id=role.id,
                blueprint_role_id=blueprint_role_id,
                custom_template_id=template.id if template else None,
            )
        )
    return True, None


def _activate_only_team(team_id: str) -> None:
    with Session(engine) as session:
        for other in session.exec(select(TeamDB)).all():
            other.is_active = other.id == team_id
            session.add(other)
        session.commit()


def _apply_team_blueprint_bundle_import(plan, bundle) -> tuple | dict:
    imported_templates: dict[str, TemplateDB] = {}
    imported_blueprint = None
    imported_roles: list[BlueprintRoleDB] = []
    imported_artifacts: list[BlueprintArtifactDB] = []
    imported_team = None

    for spec in plan.template_specs:
        action = spec["action"]
        template = spec["existing"]
        bundle_template = spec["bundle"]
        if action == "create":
            template = _repos().template_repo.save(
                TemplateDB(
                    name=bundle_template.name.strip(),
                    description=bundle_template.description,
                    prompt_template=bundle_template.prompt_template,
                )
            )
        elif action == "update":
            template.description = bundle_template.description
            template.prompt_template = bundle_template.prompt_template
            template = _repos().template_repo.save(template)
        imported_templates[spec["name"]] = template

    if plan.blueprint_spec and plan.blueprint_spec["action"] in {"create", "update", "unchanged"}:
        blueprint_bundle: BlueprintBundleDefinition = plan.blueprint_spec["bundle"]
        role_definitions = []
        for role in blueprint_bundle.roles:
            template = _resolve_bundle_template_by_name(imported_templates, role.template_name)
            role_definitions.append(
                BlueprintRoleDefinition(
                    name=role.name,
                    description=role.description,
                    template_id=template.id if template else None,
                    sort_order=role.sort_order,
                    is_required=role.is_required,
                    config=role.config or {},
                )
            )
        artifact_definitions = [
            BlueprintArtifactDefinition(
                kind=artifact.kind,
                title=artifact.title,
                description=artifact.description,
                sort_order=artifact.sort_order,
                payload=artifact.payload or {},
            )
            for artifact in blueprint_bundle.artifacts
        ]
        valid, error = _validate_blueprint_roles(role_definitions)
        if not valid:
            return _team_error(error[0], error[1], **error[2])
        valid, error = _validate_blueprint_artifacts(artifact_definitions)
        if not valid:
            return _team_error(error[0], error[1], **error[2])

        imported_blueprint = plan.blueprint_spec["existing"]
        if imported_blueprint is None:
            imported_blueprint = _repos().team_blueprint_repo.save(
                TeamBlueprintDB(
                    name=blueprint_bundle.name.strip(),
                    description=blueprint_bundle.description,
                    base_team_type_name=normalize_team_type_name(blueprint_bundle.base_team_type_name or "") or None,
                    is_seed=False,
                )
            )
        elif plan.blueprint_spec["action"] == "update":
            imported_blueprint.name = blueprint_bundle.name.strip()
            imported_blueprint.description = blueprint_bundle.description
            imported_blueprint.base_team_type_name = normalize_team_type_name(blueprint_bundle.base_team_type_name or "") or None
            imported_blueprint.updated_at = time.time()
            imported_blueprint = _repos().team_blueprint_repo.save(imported_blueprint)
        imported_roles, imported_artifacts = _persist_blueprint_children(imported_blueprint.id, role_definitions, artifact_definitions)

    if plan.team_spec and plan.team_spec["action"] in {"create", "update", "unchanged"}:
        team_bundle: BlueprintBundleTeamDefinition = plan.team_spec["bundle"]
        target_blueprint = imported_blueprint
        if target_blueprint is None and plan.team_spec.get("blueprint_name"):
            target_blueprint = _repos().team_blueprint_repo.get_by_name(plan.team_spec["blueprint_name"])
        if target_blueprint is None:
            return _team_error("blueprint_not_found", 404, blueprint_name=plan.team_spec.get("blueprint_name"))

        if not imported_roles:
            imported_roles = _repos().blueprint_role_repo.get_by_blueprint(target_blueprint.id)
            imported_artifacts = _repos().blueprint_artifact_repo.get_by_blueprint(target_blueprint.id)

        normalized_type_name = normalize_team_type_name(team_bundle.team_type_name or target_blueprint.base_team_type_name or "")
        team_type = _repos().team_type_repo.get_by_name(normalized_type_name) if normalized_type_name else None
        if normalized_type_name and team_type is None:
            return _team_error("team_type_not_found", 404, team_type_name=normalized_type_name)

        role_templates: dict[str, str] = {}
        for role_name, template_name in (team_bundle.role_templates or {}).items():
            role = _repos().role_repo.get_by_name((role_name or "").strip())
            if role is None:
                return _team_error("role_not_found", 404, role_name=role_name)
            template = _resolve_bundle_template_by_name(imported_templates, template_name)
            if template is None:
                return _team_error("template_not_found", 404, template_name=template_name, role_name=role_name)
            role_templates[role.id] = template.id

        snapshot = _serialize_blueprint(target_blueprint, roles=imported_roles, artifacts=imported_artifacts)
        imported_team = plan.team_spec["existing"]
        if imported_team is None:
            imported_team = TeamDB(
                name=team_bundle.name.strip(),
                description=team_bundle.description,
                team_type_id=team_type.id if team_type else None,
                blueprint_id=target_blueprint.id,
                is_active=team_bundle.is_active,
                role_templates=role_templates,
                blueprint_snapshot=snapshot,
            )
        else:
            imported_team.name = team_bundle.name.strip()
            imported_team.description = team_bundle.description
            imported_team.team_type_id = team_type.id if team_type else None
            imported_team.blueprint_id = target_blueprint.id
            imported_team.is_active = team_bundle.is_active
            imported_team.role_templates = role_templates
            imported_team.blueprint_snapshot = snapshot
        imported_team = _repos().team_repo.save(imported_team)
        if plan.team_spec.get("include_members"):
            valid_members, error_response = _apply_bundle_team_members(
                imported_team.id,
                team_type.id if team_type else None,
                imported_roles,
                team_bundle.members,
                imported_templates,
            )
            if not valid_members:
                return error_response
        if imported_team.is_active:
            _activate_only_team(imported_team.id)

    result = {
        "schema_version": plan.schema_version,
        "mode": plan.mode,
        "parts": plan.parts,
        "dry_run": False,
        "diff": plan.diff,
        "summary": plan.summary,
        "templates": [template.model_dump() for template in imported_templates.values()],
    }
    if imported_blueprint is not None:
        result["blueprint"] = _serialize_blueprint(imported_blueprint, roles=imported_roles, artifacts=imported_artifacts)
    if imported_team is not None:
        team_payload = imported_team.model_dump()
        team_payload["members"] = [member.model_dump() for member in _repos().team_member_repo.get_by_team(imported_team.id)]
        result["team"] = team_payload
    return result


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

SCRUM_OPENCODE_INITIAL_TASKS = [
    {
        "title": "Backlog Intake And Story Slicing",
        "description": "Shape the current goal into sprint-ready stories with explicit acceptance criteria, dependencies, and handoff notes for worker execution.",
        "status": "backlog",
        "priority": "High",
    },
    {
        "title": "Execution Cascade Agreement",
        "description": "Define when the team should use OpenCode sessions, ShellGPT, or direct terminal commands so every worker follows the same execution contract.",
        "status": "todo",
        "priority": "High",
    },
    {
        "title": "Workspace And Artifact Sync",
        "description": "Establish how workspace files, artifact outputs, and rag_helper context are read, updated, and returned to the hub after each task.",
        "status": "todo",
        "priority": "High",
    },
    {
        "title": "Vertical Slice Delivery",
        "description": "Implement one end-to-end increment in OpenCode-first mode, including changed files, verification evidence, and artifact handoff.",
        "status": "todo",
        "priority": "High",
    },
    {
        "title": "Review And Definition Of Done",
        "description": "Review code, artifacts, and verification results before closing sprint work. Capture residual risks and any follow-up tasks explicitly.",
        "status": "todo",
        "priority": "Medium",
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


RESEARCH_INITIAL_TASKS = [
    {
        "title": "Research Intake",
        "description": "Capture research objective, constraints, and expected deliverable shape.",
        "status": "todo",
        "priority": "High",
    },
    {
        "title": "Source Collection",
        "description": "Collect and classify primary sources and repository/context evidence.",
        "status": "todo",
        "priority": "High",
    },
    {
        "title": "Synthesis Report",
        "description": "Produce a concise, cited research summary with recommendations.",
        "status": "todo",
        "priority": "Medium",
    },
]

CODE_REPAIR_INITIAL_TASKS = [
    {
        "title": "Incident Triage",
        "description": "Reproduce issue, identify impact scope, and define repair strategy.",
        "status": "todo",
        "priority": "High",
    },
    {
        "title": "Repair Implementation",
        "description": "Apply minimal, targeted patch and preserve compatibility.",
        "status": "todo",
        "priority": "High",
    },
    {
        "title": "Regression Validation",
        "description": "Add/adjust tests and verify bug is fixed without side effects.",
        "status": "todo",
        "priority": "Medium",
    },
]

SECURITY_REVIEW_INITIAL_TASKS = [
    {
        "title": "Threat Review",
        "description": "Map attack surface, trust boundaries, and escalation paths.",
        "status": "todo",
        "priority": "High",
    },
    {
        "title": "Control Validation",
        "description": "Validate least-privilege, policy checks, and audit coverage.",
        "status": "todo",
        "priority": "High",
    },
    {
        "title": "Security Findings Report",
        "description": "Summarize findings with severity, remediation, and verification plan.",
        "status": "todo",
        "priority": "Medium",
    },
]

RELEASE_PREP_INITIAL_TASKS = [
    {
        "title": "Release Readiness Checklist",
        "description": "Validate release scope, blockers, and dependency state.",
        "status": "todo",
        "priority": "High",
    },
    {
        "title": "Verification Sweep",
        "description": "Run final validation for tests, migrations, and quality gates.",
        "status": "todo",
        "priority": "High",
    },
    {
        "title": "Deployment And Rollback Plan",
        "description": "Prepare rollout sequence, monitoring, and rollback criteria.",
        "status": "todo",
        "priority": "Medium",
    },
]


def _task_artifacts(tasks: list[dict]) -> list[dict]:
    return [
        {
            "kind": "task",
            "title": task["title"],
            "description": task["description"],
            "sort_order": index * 10,
            "payload": {"status": task["status"], "priority": task["priority"]},
        }
        for index, task in enumerate(tasks, start=1)
    ]


def _policy_artifact(*, title: str, sort_order: int, policy: dict) -> dict:
    return {
        "kind": "policy",
        "title": title,
        "description": "Default policy profile for teams instantiated from this blueprint.",
        "sort_order": sort_order,
        "payload": policy,
    }


SEED_BLUEPRINTS = {
    "Scrum": {
        "description": "Standard Scrum blueprint with canonical Scrum roles and starter artifacts.",
        "base_team_type_name": "Scrum",
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
        "artifacts": _task_artifacts(SCRUM_INITIAL_TASKS),
    },
    "Scrum-OpenCode": {
        "description": "Scrum blueprint adapted for OpenCode-centered delivery with explicit execution cascade for OpenCode, ShellGPT, and direct terminal work.",
        "base_team_type_name": "Scrum",
        "roles": [
            {
                "name": "Product Owner",
                "description": "Owns backlog readiness, acceptance criteria, and artifact expectations.",
                "template_name": "OpenCode Scrum - Product Owner",
                "sort_order": 10,
                "is_required": True,
                "config": {
                    "responsibility": "backlog",
                    "execution_mode": "planning",
                    "preferred_backend": "sgpt",
                    "fallback_backends": ["opencode", "terminal"],
                },
            },
            {
                "name": "Scrum Master",
                "description": "Facilitates delivery flow, blocker removal, and explicit handoffs.",
                "template_name": "OpenCode Scrum - Scrum Master",
                "sort_order": 20,
                "is_required": True,
                "config": {
                    "responsibility": "facilitation",
                    "execution_mode": "coordination",
                    "preferred_backend": "sgpt",
                    "fallback_backends": ["terminal", "opencode"],
                },
            },
            {
                "name": "Developer",
                "description": "Implements, verifies, and returns code/artifact changes through the OpenCode workspace flow.",
                "template_name": "OpenCode Scrum - Developer",
                "sort_order": 30,
                "is_required": True,
                "config": {
                    "responsibility": "delivery",
                    "execution_mode": "implementation",
                    "preferred_backend": "opencode",
                    "fallback_backends": ["terminal", "sgpt"],
                },
            },
        ],
        "artifacts": _task_artifacts(SCRUM_OPENCODE_INITIAL_TASKS)
        + [
            _policy_artifact(
                title="OpenCode Scrum Default Policy",
                sort_order=100,
                policy={
                    "task_kind": "coding",
                    "security_level": "balanced",
                    "verification_required": True,
                    "artifact_flow_expected": True,
                },
            )
        ],
    },
    "Kanban": {
        "description": "Standard Kanban blueprint with flow-oriented roles and starter artifacts.",
        "base_team_type_name": "Kanban",
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
        "artifacts": _task_artifacts(KANBAN_INITIAL_TASKS),
    },
    "Research": {
        "description": "Operational research blueprint for evidence collection, synthesis, and reporting.",
        "base_team_type_name": "Research",
        "roles": [
            {
                "name": "Research Lead",
                "description": "Owns research scope, synthesis quality, and final recommendations.",
                "template_name": "Research - Lead",
                "sort_order": 10,
                "is_required": True,
                "config": {"responsibility": "scope_and_synthesis"},
            },
            {
                "name": "Source Analyst",
                "description": "Collects, validates, and classifies sources.",
                "template_name": "Research - Source Analyst",
                "sort_order": 20,
                "is_required": True,
                "config": {"responsibility": "source_validation"},
            },
            {
                "name": "Reviewer",
                "description": "Checks assumptions, coverage, and evidence quality.",
                "template_name": "Research - Reviewer",
                "sort_order": 30,
                "is_required": False,
                "config": {"responsibility": "quality_review"},
            },
        ],
        "artifacts": _task_artifacts(RESEARCH_INITIAL_TASKS)
        + [
            _policy_artifact(
                title="Research Default Policy",
                sort_order=100,
                policy={"task_kind": "research", "security_level": "balanced", "verification_required": True},
            )
        ],
    },
    "Code-Repair": {
        "description": "Operational code-repair blueprint for incident triage, patching, and regression checks.",
        "base_team_type_name": "Code-Repair",
        "roles": [
            {
                "name": "Repair Lead",
                "description": "Owns incident diagnosis and repair plan.",
                "template_name": "Code Repair - Lead",
                "sort_order": 10,
                "is_required": True,
                "config": {"responsibility": "triage_and_plan"},
            },
            {
                "name": "Fix Engineer",
                "description": "Implements targeted fixes with minimal blast radius.",
                "template_name": "Code Repair - Engineer",
                "sort_order": 20,
                "is_required": True,
                "config": {"responsibility": "implementation"},
            },
            {
                "name": "QA Verifier",
                "description": "Verifies regressions and completion criteria.",
                "template_name": "Code Repair - QA",
                "sort_order": 30,
                "is_required": True,
                "config": {"responsibility": "verification"},
            },
        ],
        "artifacts": _task_artifacts(CODE_REPAIR_INITIAL_TASKS)
        + [
            _policy_artifact(
                title="Code Repair Default Policy",
                sort_order=100,
                policy={"task_kind": "coding", "security_level": "balanced", "verification_required": True},
            )
        ],
    },
    "Security-Review": {
        "description": "Operational security-review blueprint for control validation and remediation guidance.",
        "base_team_type_name": "Security-Review",
        "roles": [
            {
                "name": "Security Lead",
                "description": "Owns review scope, severity model, and sign-off.",
                "template_name": "Security Review - Lead",
                "sort_order": 10,
                "is_required": True,
                "config": {"responsibility": "risk_governance"},
            },
            {
                "name": "Security Analyst",
                "description": "Executes technical review and evidence collection.",
                "template_name": "Security Review - Analyst",
                "sort_order": 20,
                "is_required": True,
                "config": {"responsibility": "technical_review"},
            },
            {
                "name": "Compliance Reviewer",
                "description": "Checks policy and compliance obligations.",
                "template_name": "Security Review - Compliance",
                "sort_order": 30,
                "is_required": False,
                "config": {"responsibility": "compliance"},
            },
        ],
        "artifacts": _task_artifacts(SECURITY_REVIEW_INITIAL_TASKS)
        + [
            _policy_artifact(
                title="Security Review Default Policy",
                sort_order=100,
                policy={"task_kind": "analysis", "security_level": "strict", "verification_required": True},
            )
        ],
    },
    "Release-Prep": {
        "description": "Operational release-preparation blueprint for readiness, verification, and rollout planning.",
        "base_team_type_name": "Release-Prep",
        "roles": [
            {
                "name": "Release Manager",
                "description": "Coordinates release scope, schedule, and go/no-go decision.",
                "template_name": "Release Prep - Manager",
                "sort_order": 10,
                "is_required": True,
                "config": {"responsibility": "release_governance"},
            },
            {
                "name": "Verification Engineer",
                "description": "Runs release validation and preflight checks.",
                "template_name": "Release Prep - Verification",
                "sort_order": 20,
                "is_required": True,
                "config": {"responsibility": "verification"},
            },
            {
                "name": "Operations Liaison",
                "description": "Prepares deployment/rollback operations.",
                "template_name": "Release Prep - Operations",
                "sort_order": 30,
                "is_required": True,
                "config": {"responsibility": "operations_readiness"},
            },
        ],
        "artifacts": _task_artifacts(RELEASE_PREP_INITIAL_TASKS)
        + [
            _policy_artifact(
                title="Release Prep Default Policy",
                sort_order=100,
                policy={"task_kind": "ops", "security_level": "strict", "verification_required": True},
            )
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

SCRUM_OPENCODE_WORKFLOW_APPENDIX = """

Execution cascade:

1. Prefer OpenCode for multi-step coding, repository-aware edits, iterative debugging, and any task that benefits from a stateful worker session.
2. Prefer ShellGPT/SGPT for concise synthesis, backlog refinement, drafting acceptance criteria, and short analytical turns that do not require a persistent coding session.
3. Prefer direct terminal commands for deterministic, auditable operations such as ls/find/rg, tests, builds, formatting, git status, and exact environment checks.
4. When switching backend, say why briefly and keep the change intentional instead of mixing tools arbitrarily.
5. Use the worker workspace, artifact directory, and rag_helper context as the source of truth for exchanged files; return concrete changed-file and artifact outcomes to the hub.
6. Surface blockers, assumptions, missing context, and verification gaps explicitly instead of hiding them in vague summaries.
""".strip()


def normalize_team_type_name(team_type_name: str) -> str:
    if not team_type_name:
        return ""
    normalized = team_type_name.strip()
    mapping = {
        "scrum": "Scrum",
        "kanban": "Kanban",
        "research": "Research",
        "code-repair": "Code-Repair",
        "code repair": "Code-Repair",
        "security-review": "Security-Review",
        "security review": "Security-Review",
        "release-prep": "Release-Prep",
        "release prep": "Release-Prep",
    }
    return mapping.get(normalized.lower(), normalized)


ROLE_PROFILE_DEFAULTS = {
    "Scrum": {
        "Product Owner": {
            "capability_defaults": ["planning", "analysis", "backlog"],
            "risk_profile": "balanced",
            "verification_defaults": {"required": True, "gates": ["acceptance_criteria_defined"]},
        },
        "Scrum Master": {
            "capability_defaults": ["coordination", "analysis", "verification"],
            "risk_profile": "balanced",
            "verification_defaults": {"required": True, "gates": ["blockers_reviewed"]},
        },
        "Developer": {
            "capability_defaults": ["coding", "testing", "verification"],
            "risk_profile": "high",
            "verification_defaults": {"required": True, "gates": ["implementation_verified"]},
        },
    },
    "Research": {
        "Research Lead": {
            "capability_defaults": ["research", "analysis", "synthesis"],
            "risk_profile": "balanced",
            "verification_defaults": {"required": True, "gates": ["evidence_quality", "source_coverage"]},
        },
        "Source Analyst": {
            "capability_defaults": ["research", "repo_research"],
            "risk_profile": "low",
            "verification_defaults": {"required": True, "gates": ["source_traceability"]},
        },
        "Reviewer": {
            "capability_defaults": ["review", "analysis"],
            "risk_profile": "balanced",
            "verification_defaults": {"required": True, "gates": ["independent_review"]},
        },
    },
    "Code-Repair": {
        "Repair Lead": {
            "capability_defaults": ["planning", "analysis", "repair"],
            "risk_profile": "balanced",
            "verification_defaults": {"required": True, "gates": ["impact_assessment"]},
        },
        "Fix Engineer": {
            "capability_defaults": ["coding", "repair", "testing"],
            "risk_profile": "high",
            "verification_defaults": {"required": True, "gates": ["regression_tests"]},
        },
        "QA Verifier": {
            "capability_defaults": ["verification", "testing"],
            "risk_profile": "balanced",
            "verification_defaults": {"required": True, "gates": ["quality_gate_pass"]},
        },
    },
    "Security-Review": {
        "Security Lead": {
            "capability_defaults": ["security", "review", "governance"],
            "risk_profile": "strict",
            "verification_defaults": {"required": True, "gates": ["severity_signoff"]},
        },
        "Security Analyst": {
            "capability_defaults": ["security", "analysis"],
            "risk_profile": "strict",
            "verification_defaults": {"required": True, "gates": ["control_validation"]},
        },
        "Compliance Reviewer": {
            "capability_defaults": ["compliance", "review"],
            "risk_profile": "balanced",
            "verification_defaults": {"required": True, "gates": ["policy_alignment"]},
        },
    },
    "Release-Prep": {
        "Release Manager": {
            "capability_defaults": ["planning", "ops", "governance"],
            "risk_profile": "strict",
            "verification_defaults": {"required": True, "gates": ["release_approval"]},
        },
        "Verification Engineer": {
            "capability_defaults": ["verification", "testing"],
            "risk_profile": "balanced",
            "verification_defaults": {"required": True, "gates": ["preflight_checks"]},
        },
        "Operations Liaison": {
            "capability_defaults": ["ops", "rollback"],
            "risk_profile": "high",
            "verification_defaults": {"required": True, "gates": ["rollback_readiness"]},
        },
    },
}


def _with_role_profile_defaults(base_team_type_name: str, role_name: str, config: dict | None) -> dict:
    merged = dict(config or {})
    defaults = dict((ROLE_PROFILE_DEFAULTS.get(base_team_type_name) or {}).get(role_name) or {})
    for key, value in defaults.items():
        merged.setdefault(key, value)
    return merged


def initialize_scrum_artifacts(team_name: str, team_id: str | None = None):
    """Erstellt initiale Tasks für ein Scrum Team."""
    import time

    from agent.db_models import TaskDB

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
        _repos().task_repo.save(new_task)


def ensure_default_templates(team_type_name: str):
    """Stellt sicher, dass Standard-Rollen und Templates fuer einen Team-Typ existieren."""
    team_type_name = normalize_team_type_name(team_type_name)
    if not team_type_name:
        return
    template_specs: list[TemplateBootstrapSpec] = []
    role_specs: list[RoleLinkSpec] = []
    if team_type_name == "Scrum":
        template_specs.extend(
            [
                TemplateBootstrapSpec(
                    "Scrum - Product Owner",
                    "Prompt template for Scrum Product Owner.",
                    (
                        "You are the Product Owner in a Scrum team. Align backlog, priorities, "
                        "and acceptance criteria with {{team_goal}}.\n\n"
                        f"{SCRUM_SOLID_TEMPLATE_APPENDIX}"
                    ),
                ),
                TemplateBootstrapSpec(
                    "Scrum - Scrum Master",
                    "Prompt template for Scrum Master.",
                    (
                        "You are the Scrum Master for a Scrum team. Facilitate events, "
                        "remove blockers, and improve flow toward {{team_goal}}.\n\n"
                        f"{SCRUM_SOLID_TEMPLATE_APPENDIX}"
                    ),
                ),
                TemplateBootstrapSpec(
                    "Scrum - Developer",
                    "Prompt template for Scrum Developer.",
                    (
                        "You are a Developer in a Scrum team. Implement backlog items, "
                        "review work, and deliver increments for {{team_goal}}.\n\n"
                        f"{SCRUM_SOLID_TEMPLATE_APPENDIX}"
                    ),
                ),
                TemplateBootstrapSpec(
                    "OpenCode Scrum - Product Owner",
                    "Prompt template for an OpenCode-adapted Scrum Product Owner.",
                    (
                        "You are the Product Owner in an OpenCode-adapted Scrum team working toward {{team_goal}}.\n\n"
                        "Your focus:\n"
                        "- keep backlog items decision-ready\n"
                        "- define acceptance criteria and artifact expectations\n"
                        "- clarify what must be returned to the hub after worker execution\n\n"
                        "Backend emphasis:\n"
                        "- prefer SGPT for story slicing, prioritization, and concise synthesis\n"
                        "- use OpenCode when repository-aware investigation or artifact-producing analysis is needed\n"
                        "- use the terminal only for exact evidence gathering and deterministic checks\n\n"
                        f"{SCRUM_OPENCODE_WORKFLOW_APPENDIX}\n\n"
                        f"{SCRUM_SOLID_TEMPLATE_APPENDIX}"
                    ),
                ),
                TemplateBootstrapSpec(
                    "OpenCode Scrum - Scrum Master",
                    "Prompt template for an OpenCode-adapted Scrum Master.",
                    (
                        "You are the Scrum Master in an OpenCode-adapted Scrum team working toward {{team_goal}}.\n\n"
                        "Your focus:\n"
                        "- remove blockers and keep work flowing through the hub-worker model\n"
                        "- make handoffs explicit between planning, execution, review, and follow-up\n"
                        "- ensure Definition of Done includes verification and artifact return paths\n\n"
                        "Backend emphasis:\n"
                        "- prefer SGPT for coordination, summarization, and decision framing\n"
                        "- use the terminal for environment diagnostics or exact verification commands\n"
                        "- use OpenCode when you need a stateful investigation across multiple related files or steps\n\n"
                        f"{SCRUM_OPENCODE_WORKFLOW_APPENDIX}\n\n"
                        f"{SCRUM_SOLID_TEMPLATE_APPENDIX}"
                    ),
                ),
                TemplateBootstrapSpec(
                    "OpenCode Scrum - Developer",
                    "Prompt template for an OpenCode-adapted Scrum Developer.",
                    (
                        "You are a Developer in an OpenCode-adapted Scrum team delivering {{team_goal}}.\n\n"
                        "Your focus:\n"
                        "- implement working increments with minimal blast radius\n"
                        "- keep changed files, artifact outputs, and verification evidence synchronized back to the hub\n"
                        "- make follow-up work explicit when a slice cannot be completed in one pass\n\n"
                        "Backend emphasis:\n"
                        "- prefer OpenCode for implementation, repair loops, code review passes, and stateful coding sessions\n"
                        "- use the terminal for deterministic commands, builds, tests, formatters, and exact repo inspection\n"
                        "- use SGPT for short explanations, tradeoff summaries, or draft reasoning when no persistent coding loop is required\n\n"
                        f"{SCRUM_OPENCODE_WORKFLOW_APPENDIX}\n\n"
                        f"{SCRUM_SOLID_TEMPLATE_APPENDIX}"
                    ),
                ),
            ]
        )
        role_specs.extend(
            [
                RoleLinkSpec("Product Owner", "Owns the backlog and prioritization.", "Scrum - Product Owner"),
                RoleLinkSpec("Scrum Master", "Facilitates the Scrum process.", "Scrum - Scrum Master"),
                RoleLinkSpec("Developer", "Builds and delivers backlog items.", "Scrum - Developer"),
            ]
        )

    if team_type_name == "Kanban":
        template_specs.extend(
            [
                TemplateBootstrapSpec(
                    "Kanban - Service Delivery Manager",
                    "Prompt template for Kanban Service Delivery Manager.",
                    "You are the Service Delivery Manager in a Kanban team. Monitor flow metrics and service delivery toward {{team_goal}}.",
                ),
                TemplateBootstrapSpec(
                    "Kanban - Flow Manager",
                    "Prompt template for Kanban Flow Manager.",
                    "You are the Flow Manager in a Kanban team. Optimize WIP, policies, and flow to achieve {{team_goal}}.",
                ),
                TemplateBootstrapSpec(
                    "Kanban - Developer",
                    "Prompt template for Kanban Developer.",
                    "You are a Developer in a Kanban team. Deliver work items, limit WIP, and maintain quality for {{team_goal}}.",
                ),
            ]
        )
        role_specs.extend(
            [
                RoleLinkSpec("Service Delivery Manager", "Oversees service delivery and flow metrics.", "Kanban - Service Delivery Manager"),
                RoleLinkSpec("Flow Manager", "Optimizes WIP limits and flow.", "Kanban - Flow Manager"),
                RoleLinkSpec("Developer", "Delivers work items and maintains quality.", "Kanban - Developer"),
            ]
        )

    if team_type_name == "Research":
        template_specs.extend(
            [
                TemplateBootstrapSpec("Research - Lead", "Prompt template for Research Lead.", "You are the Research Lead. Define scope, synthesis, and decision-ready outcomes for {{team_goal}}."),
                TemplateBootstrapSpec("Research - Source Analyst", "Prompt template for Source Analyst.", "You are the Source Analyst. Collect, validate, and summarize reliable sources for {{team_goal}}."),
                TemplateBootstrapSpec("Research - Reviewer", "Prompt template for Research Reviewer.", "You are the Research Reviewer. Challenge assumptions and verify evidence quality for {{team_goal}}."),
            ]
        )
        role_specs.extend(
            [
                RoleLinkSpec("Research Lead", "Owns research scope and synthesis quality.", "Research - Lead"),
                RoleLinkSpec("Source Analyst", "Collects and validates sources.", "Research - Source Analyst"),
                RoleLinkSpec("Reviewer", "Checks assumptions and evidence quality.", "Research - Reviewer"),
            ]
        )

    if team_type_name == "Code-Repair":
        template_specs.extend(
            [
                TemplateBootstrapSpec("Code Repair - Lead", "Prompt template for Repair Lead.", "You are the Repair Lead. Triage incidents and guide minimal-risk remediation for {{team_goal}}."),
                TemplateBootstrapSpec("Code Repair - Engineer", "Prompt template for Fix Engineer.", "You are the Fix Engineer. Implement and validate targeted fixes for {{team_goal}}."),
                TemplateBootstrapSpec("Code Repair - QA", "Prompt template for QA Verifier.", "You are the QA Verifier. Confirm regressions are prevented and quality criteria are met for {{team_goal}}."),
            ]
        )
        role_specs.extend(
            [
                RoleLinkSpec("Repair Lead", "Owns incident diagnosis and repair planning.", "Code Repair - Lead"),
                RoleLinkSpec("Fix Engineer", "Implements targeted fixes.", "Code Repair - Engineer"),
                RoleLinkSpec("QA Verifier", "Validates regressions and completion.", "Code Repair - QA"),
            ]
        )

    if team_type_name == "Security-Review":
        template_specs.extend(
            [
                TemplateBootstrapSpec("Security Review - Lead", "Prompt template for Security Lead.", "You are the Security Lead. Define security review scope and sign-off for {{team_goal}}."),
                TemplateBootstrapSpec("Security Review - Analyst", "Prompt template for Security Analyst.", "You are the Security Analyst. Assess vulnerabilities and control coverage for {{team_goal}}."),
                TemplateBootstrapSpec("Security Review - Compliance", "Prompt template for Compliance Reviewer.", "You are the Compliance Reviewer. Validate policy and compliance obligations for {{team_goal}}."),
            ]
        )
        role_specs.extend(
            [
                RoleLinkSpec("Security Lead", "Owns review scope and severity model.", "Security Review - Lead"),
                RoleLinkSpec("Security Analyst", "Performs technical security analysis.", "Security Review - Analyst"),
                RoleLinkSpec("Compliance Reviewer", "Validates compliance obligations.", "Security Review - Compliance"),
            ]
        )

    if team_type_name == "Release-Prep":
        template_specs.extend(
            [
                TemplateBootstrapSpec("Release Prep - Manager", "Prompt template for Release Manager.", "You are the Release Manager. Coordinate readiness and go/no-go decisions for {{team_goal}}."),
                TemplateBootstrapSpec("Release Prep - Verification", "Prompt template for Verification Engineer.", "You are the Verification Engineer. Execute release validation and preflight checks for {{team_goal}}."),
                TemplateBootstrapSpec("Release Prep - Operations", "Prompt template for Operations Liaison.", "You are the Operations Liaison. Prepare rollout and rollback operations for {{team_goal}}."),
            ]
        )
        role_specs.extend(
            [
                RoleLinkSpec("Release Manager", "Coordinates release scope and timeline.", "Release Prep - Manager"),
                RoleLinkSpec("Verification Engineer", "Runs verification and release checks.", "Release Prep - Verification"),
                RoleLinkSpec("Operations Liaison", "Prepares deployment and rollback operations.", "Release Prep - Operations"),
            ]
        )
    ensure_default_templates_service(
        team_type_name,
        team_type_description=f"Standard {team_type_name} Team",
        template_specs=template_specs,
        role_specs=role_specs,
    )


def _serialize_blueprint(
    blueprint: TeamBlueprintDB,
    roles: list[BlueprintRoleDB] | None = None,
    artifacts: list[BlueprintArtifactDB] | None = None,
) -> dict:
    blueprint_dict = blueprint.model_dump()
    blueprint_roles = roles if roles is not None else _repos().blueprint_role_repo.get_by_blueprint(blueprint.id)
    blueprint_artifacts = artifacts if artifacts is not None else _repos().blueprint_artifact_repo.get_by_blueprint(blueprint.id)
    blueprint_dict["roles"] = [role.model_dump() for role in blueprint_roles]
    blueprint_dict["artifacts"] = [artifact.model_dump() for artifact in blueprint_artifacts]
    return blueprint_dict


ALLOWED_BLUEPRINT_ARTIFACT_KINDS = {"task", "policy"}


def _validate_blueprint_roles(roles: list) -> tuple[bool, tuple | None]:
    seen_names: set[str] = set()
    seen_sort_orders: set[int] = set()
    for role in roles:
        normalized_name = role.name.strip()
        if not normalized_name:
            return False, ("blueprint_role_name_required", 400, {})
        if normalized_name.lower() in seen_names:
            return False, ("duplicate_blueprint_role_name", 400, {"role_name": normalized_name})
        seen_names.add(normalized_name.lower())
        if role.sort_order in seen_sort_orders:
            return False, ("duplicate_blueprint_role_sort_order", 400, {"sort_order": role.sort_order})
        seen_sort_orders.add(role.sort_order)
        if role.template_id and not _repos().template_repo.get_by_id(role.template_id):
            return False, ("template_not_found", 404, {"template_id": role.template_id})
        role_config = dict(role.config or {})
        capability_defaults = role_config.get("capability_defaults")
        risk_profile = role_config.get("risk_profile")
        verification_defaults = role_config.get("verification_defaults")
        if capability_defaults is not None and not isinstance(capability_defaults, list):
            return False, ("blueprint_role_capability_defaults_invalid", 400, {"role_name": normalized_name})
        if risk_profile is not None and str(risk_profile).strip().lower() not in {"low", "balanced", "high", "strict"}:
            return False, ("blueprint_role_risk_profile_invalid", 400, {"role_name": normalized_name})
        if verification_defaults is not None and not isinstance(verification_defaults, dict):
            return False, ("blueprint_role_verification_defaults_invalid", 400, {"role_name": normalized_name})
    return True, None


def _validate_blueprint_artifacts(artifacts: list) -> tuple[bool, tuple | None]:
    seen_titles: set[str] = set()
    seen_sort_orders: set[int] = set()
    for artifact in artifacts:
        normalized_kind = artifact.kind.strip().lower()
        normalized_title = artifact.title.strip()
        if not normalized_kind:
            return False, ("blueprint_artifact_kind_required", 400, {})
        if normalized_kind not in ALLOWED_BLUEPRINT_ARTIFACT_KINDS:
            return False, (
                "blueprint_artifact_kind_invalid",
                400,
                {"kind": artifact.kind, "allowed_kinds": sorted(ALLOWED_BLUEPRINT_ARTIFACT_KINDS)},
            )
        if not normalized_title:
            return False, ("blueprint_artifact_title_required", 400, {})
        if normalized_title.lower() in seen_titles:
            return False, ("duplicate_blueprint_artifact_title", 400, {"title": normalized_title})
        seen_titles.add(normalized_title.lower())
        if artifact.sort_order in seen_sort_orders:
            return False, ("duplicate_blueprint_artifact_sort_order", 400, {"sort_order": artifact.sort_order})
        seen_sort_orders.add(artifact.sort_order)
    return True, None


def _persist_blueprint_children(
    blueprint_id: str,
    role_definitions: list | None,
    artifact_definitions: list | None,
) -> tuple[list[BlueprintRoleDB], list[BlueprintArtifactDB]]:
    return persist_blueprint_children_service(blueprint_id, role_definitions, artifact_definitions)


def ensure_seed_blueprints() -> None:
    reconcile_seed_blueprints_service(
        SEED_BLUEPRINTS,
        normalize_team_type_name=normalize_team_type_name,
        with_role_profile_defaults=_with_role_profile_defaults,
        ensure_default_templates_callback=ensure_default_templates,
    )


def _ensure_role_for_blueprint_role(team_type_id: str | None, blueprint_role: BlueprintRoleDB) -> RoleDB:
    role = _repos().role_repo.get_by_name(blueprint_role.name)
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
    role = _repos().role_repo.save(role)

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
        _repos().task_repo.save(
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
    normalized_type_name = normalize_team_type_name(blueprint.base_team_type_name or "")
    if normalized_type_name:
        ensure_default_templates(normalized_type_name)
    return instantiate_blueprint_service(
        blueprint.id,
        data,
        error_factory=_team_error,
        normalize_team_type_name=normalize_team_type_name,
    )


@teams_bp.route("/teams/blueprints", methods=["GET"])
@check_auth
def list_team_blueprints():
    ensure_seed_blueprints()
    blueprints = _repos().team_blueprint_repo.get_all()
    return api_response(data=[_serialize_blueprint(blueprint) for blueprint in blueprints])


@teams_bp.route("/teams/blueprints/<blueprint_id>", methods=["GET"])
@check_auth
def get_team_blueprint(blueprint_id):
    ensure_seed_blueprints()
    blueprint = _repos().team_blueprint_repo.get_by_id(blueprint_id)
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
    if _repos().team_blueprint_repo.get_by_name(blueprint_name):
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

    blueprint, roles, artifacts = save_blueprint_service(
        blueprint_id=None,
        name=blueprint_name,
        description=data.description,
        base_team_type_name=normalized_type_name or None,
        roles=data.roles,
        artifacts=data.artifacts,
        is_seed=False,
    )
    log_audit("team_blueprint_created", {"blueprint_id": blueprint.id, "name": blueprint.name})
    return api_response(data=_serialize_blueprint(blueprint, roles=roles, artifacts=artifacts), code=201)


@teams_bp.route("/teams/blueprints/<blueprint_id>", methods=["PATCH"])
@check_auth
@admin_required
@validate_request(TeamBlueprintUpdateRequest)
def update_team_blueprint(blueprint_id):
    data: TeamBlueprintUpdateRequest = g.validated_data
    blueprint = _repos().team_blueprint_repo.get_by_id(blueprint_id)
    if not blueprint:
        return _team_error("not_found", 404)

    if data.name is not None and data.name.strip() != blueprint.name:
        if not data.name.strip():
            return _team_error("blueprint_name_required", 400)
        existing = _repos().team_blueprint_repo.get_by_name(data.name.strip())
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

    blueprint, roles, artifacts = save_blueprint_service(
        blueprint_id=blueprint.id,
        name=blueprint.name,
        description=blueprint.description,
        base_team_type_name=blueprint.base_team_type_name,
        roles=data.roles,
        artifacts=data.artifacts,
        is_seed=blueprint.is_seed,
    )
    log_audit("team_blueprint_updated", {"blueprint_id": blueprint.id})
    return api_response(data=_serialize_blueprint(blueprint, roles=roles, artifacts=artifacts))


@teams_bp.route("/teams/blueprints/<blueprint_id>", methods=["DELETE"])
@check_auth
@admin_required
def delete_team_blueprint(blueprint_id):
    with Session(engine) as session:
        referencing_teams = session.exec(select(TeamDB).where(TeamDB.blueprint_id == blueprint_id)).all()
    if referencing_teams:
        return _team_error(
            "blueprint_in_use",
            409,
            blueprint_id=blueprint_id,
            team_ids=[team.id for team in referencing_teams],
            team_count=len(referencing_teams),
        )
    _repos().blueprint_artifact_repo.delete_by_blueprint(blueprint_id)
    _repos().blueprint_role_repo.delete_by_blueprint(blueprint_id)
    if _repos().team_blueprint_repo.delete(blueprint_id):
        log_audit("team_blueprint_deleted", {"blueprint_id": blueprint_id})
        return api_response(data={"status": "deleted"})
    return _team_error("not_found", 404)


@teams_bp.route("/teams/blueprints/<blueprint_id>/bundle", methods=["GET"])
@check_auth
@admin_required
def export_team_blueprint_bundle(blueprint_id):
    ensure_seed_blueprints()
    blueprint = _repos().team_blueprint_repo.get_by_id(blueprint_id)
    if not blueprint:
        return _team_error("not_found", 404)
    mode = normalize_bundle_mode(request.args.get("mode"))
    parts = normalize_bundle_parts(_parse_parts_query(request.args.get("parts")), [])
    errors = validate_bundle_mode_and_parts(mode, parts)
    if errors:
        return _team_error("bundle_export_invalid", 400, errors=errors)

    team = None
    team_id = request.args.get("team_id")
    if team_id:
        team = _repos().team_repo.get_by_id(team_id)
        if not team:
            return _team_error("team_not_found", 404, team_id=team_id)
        if team.blueprint_id != blueprint_id:
            return _team_error("team_blueprint_mismatch", 400, team_id=team_id, blueprint_id=blueprint_id)

    payload = export_blueprint_bundle(
        _repos(),
        blueprint,
        team=team,
        include_members=_parse_bool_query(request.args.get("include_members")),
        mode=mode,
        parts=parts,
    )
    return api_response(data=payload)


@teams_bp.route("/teams/blueprints/import", methods=["POST"])
@check_auth
@admin_required
@validate_request(TeamBlueprintBundleImportRequest)
def import_team_blueprint_bundle():
    data: TeamBlueprintBundleImportRequest = g.validated_data
    plan = build_bundle_import_plan(_repos(), data.bundle, data.conflict_strategy)
    if plan.errors:
        return _bundle_plan_error_response(plan)
    if data.dry_run:
        return api_response(
            data={
                "schema_version": plan.schema_version,
                "mode": plan.mode,
                "parts": plan.parts,
                "dry_run": True,
                "diff": plan.diff,
                "summary": plan.summary,
            }
        )

    result = _apply_team_blueprint_bundle_import(plan, data.bundle)
    if isinstance(result, tuple):
        return result
    log_audit(
        "team_blueprint_bundle_imported",
        {
            "mode": plan.mode,
            "parts": plan.parts,
            "conflict_strategy": plan.conflict_strategy,
            "schema_version": BUNDLE_SCHEMA_VERSION,
        },
    )
    return api_response(data=result)


@teams_bp.route("/teams/blueprints/<blueprint_id>/instantiate", methods=["POST"])
@check_auth
@admin_required
@validate_request(TeamBlueprintInstantiateRequest)
def instantiate_team_blueprint(blueprint_id):
    ensure_seed_blueprints()
    blueprint = _repos().team_blueprint_repo.get_by_id(blueprint_id)
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
    teams = _repos().team_repo.get_all()
    result = []
    for t in teams:
        team_dict = t.model_dump()
        # Mitglieder laden
        members = _repos().team_member_repo.get_by_team(t.id)
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
    return api_response(data=new_team.model_dump(), code=201)


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
        _repos().team_repo.save(team)
    else:
        _repos().team_repo.save(team)
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

    if repos.team_repo.delete(team_id):
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
