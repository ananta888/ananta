import time
import uuid
from typing import Any

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
from agent.services.seed_blueprint_catalog import get_seed_blueprint_catalog
from agent.services.team_blueprint_service import (
    RoleLinkSpec,
    TemplateBootstrapSpec,
    ensure_default_templates as ensure_default_templates_service,
    instantiate_blueprint as instantiate_blueprint_service,
    persist_blueprint_children as persist_blueprint_children_service,
    reconcile_seed_blueprints as reconcile_seed_blueprints_service,
    save_blueprint as save_blueprint_service,
)
from agent.services.team_definition_version_service import (
    build_team_blueprint_diff,
    enrich_blueprint_payload,
    team_definition_metadata,
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
        "tdd": "TDD",
        "test-driven development": "TDD",
        "test driven development": "TDD",
        "research-evolution": "Research-Evolution",
        "research evolution": "Research-Evolution",
        "deerflow-evolver": "Research-Evolution",
        "deerflow evolver": "Research-Evolution",
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
    "TDD": {
        "Behavior Analyst": {
            "capability_defaults": ["worker.patch.propose", "worker.verify.result"],
            "risk_profile": "balanced",
            "verification_defaults": {"required": True, "gates": ["behavior_statement_defined"]},
        },
        "Test Driver": {
            "capability_defaults": ["worker.test.run", "worker.verify.result"],
            "risk_profile": "high",
            "verification_defaults": {"required": True, "gates": ["red_before_green_evidence"]},
        },
        "Refactor Verifier": {
            "capability_defaults": ["worker.patch.propose", "worker.patch.apply.approval_gated", "worker.verify.result"],
            "risk_profile": "high",
            "verification_defaults": {"required": True, "gates": ["approval_before_apply", "regression_checks_pass"]},
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
    "Research-Evolution": {
        "Research Lead": {
            "capability_defaults": ["research", "deerflow", "source_synthesis"],
            "risk_profile": "balanced",
            "verification_defaults": {"required": True, "gates": ["source_traceability", "research_report_reviewed"]},
        },
        "Evolution Strategist": {
            "capability_defaults": ["evolution", "proposal", "risk_scoring"],
            "risk_profile": "high",
            "verification_defaults": {"required": True, "gates": ["proposal_linked_to_research", "validation_plan_defined"]},
        },
        "Review Gate Owner": {
            "capability_defaults": ["review", "governance", "verification"],
            "risk_profile": "strict",
            "verification_defaults": {"required": True, "gates": ["human_review", "apply_blocked_by_default"]},
        },
    },
}


def _load_seed_blueprints() -> dict[str, dict]:
    catalog = get_seed_blueprint_catalog()
    seed_blueprints = catalog.as_seed_blueprint_map()
    if seed_blueprints:
        return seed_blueprints
    raise RuntimeError(f"seed_blueprint_catalog_unavailable: {catalog.load_error or 'unknown_error'}")


def _scrum_initial_tasks_from_catalog() -> list[dict]:
    scrum = dict(_load_seed_blueprints().get("Scrum") or {})
    artifacts = list(scrum.get("artifacts") or [])
    tasks: list[dict] = []
    for artifact in artifacts:
        if str((artifact or {}).get("kind") or "").strip().lower() != "task":
            continue
        payload = dict((artifact or {}).get("payload") or {})
        title = str((artifact or {}).get("title") or "").strip()
        description = str((artifact or {}).get("description") or "").strip()
        if not title or not description:
            continue
        tasks.append(
            {
                "title": title,
                "description": description,
                "status": str(payload.get("status") or "todo").strip() or "todo",
                "priority": str(payload.get("priority") or "Medium").strip() or "Medium",
            }
        )
    return tasks


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

    for task_data in _scrum_initial_tasks_from_catalog():
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

    if team_type_name == "TDD":
        template_specs.extend(
            [
                TemplateBootstrapSpec(
                    "TDD - Behavior Analyst",
                    "Prompt template for TDD behavior analyst.",
                    "You are the Behavior Analyst. Define expected behavior, boundaries, and acceptance checks before implementation for {{team_goal}}.",
                ),
                TemplateBootstrapSpec(
                    "TDD - Test Driver",
                    "Prompt template for TDD test driver.",
                    "You are the Test Driver. Add/adjust tests first, capture expected red evidence, and confirm green status after minimal patch for {{team_goal}}.",
                ),
                TemplateBootstrapSpec(
                    "TDD - Refactor Verifier",
                    "Prompt template for TDD refactor verifier.",
                    "You are the Refactor Verifier. Keep changes minimal, ensure approval gates for apply paths, and verify final quality for {{team_goal}}.",
                ),
            ]
        )
        role_specs.extend(
            [
                RoleLinkSpec("Behavior Analyst", "Defines testable behavior scope before coding.", "TDD - Behavior Analyst"),
                RoleLinkSpec("Test Driver", "Owns red/green test execution evidence.", "TDD - Test Driver"),
                RoleLinkSpec("Refactor Verifier", "Validates refactor and verification evidence.", "TDD - Refactor Verifier"),
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
    if team_type_name == "Research-Evolution":
        template_specs.extend(
            [
                TemplateBootstrapSpec(
                    "Research Evolution - Research Lead",
                    "Prompt template for DeerFlow-backed research lead.",
                    "You are the Research Lead. Use DeerFlow-style research to produce sources, context, and a decision-ready report for {{team_goal}}.",
                ),
                TemplateBootstrapSpec(
                    "Research Evolution - Evolution Strategist",
                    "Prompt template for Evolver-backed proposal strategist.",
                    "You are the Evolution Strategist. Use approved research context to prepare reviewable Evolver proposals for {{team_goal}} without applying changes.",
                ),
                TemplateBootstrapSpec(
                    "Research Evolution - Review Gate Owner",
                    "Prompt template for review gate owner.",
                    "You are the Review Gate Owner. Verify research evidence, proposal risk, validation needs, and human approval gates for {{team_goal}}.",
                ),
            ]
        )
        role_specs.extend(
            [
                RoleLinkSpec("Research Lead", "Owns DeerFlow research scope and synthesis.", "Research Evolution - Research Lead"),
                RoleLinkSpec("Evolution Strategist", "Owns Evolver proposal preparation.", "Research Evolution - Evolution Strategist"),
                RoleLinkSpec("Review Gate Owner", "Owns review gates and validation decisions.", "Research Evolution - Review Gate Owner"),
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
    return enrich_blueprint_payload(blueprint_dict, blueprint, blueprint_roles, blueprint_artifacts)


def _suggest_goal_modes_for_blueprint(blueprint: TeamBlueprintDB) -> list[str]:
    name = str(blueprint.name or "").strip().lower()
    team_type = str(blueprint.base_team_type_name or "").strip().lower()
    if name == "tdd" or team_type == "tdd":
        return ["code_fix", "admin_repair", "project_evolution", "code_review"]
    if "research-evolution" in name or "research-evolution" in team_type:
        return ["project_evolution", "repo_analysis", "doc_summary", "code_review"]
    if "repair" in name:
        return ["admin_repair", "code_fix", "docker_compose_repair", "runtime_repair", "sys_diag"]
    if "research" in name:
        return ["repo_analysis", "doc_summary", "doc_gen"]
    if "security" in name:
        return ["code_review", "repo_analysis", "sys_diag"]
    if "release" in name:
        return ["sys_diag", "doc_gen", "code_review"]
    if "scrum-opencode" in name or "opencode" in name:
        return ["code_fix", "code_review", "doc_gen", "docker_compose_repair"]
    if team_type == "kanban":
        return ["code_fix", "repo_analysis", "doc_gen"]
    return ["code_fix", "repo_analysis", "doc_gen", "sys_diag"]


def _suggest_playbooks_for_blueprint(blueprint: TeamBlueprintDB) -> list[str]:
    name = str(blueprint.name or "").strip().lower()
    if name == "tdd":
        return ["bugfix", "refactoring", "architecture_review"]
    if "research-evolution" in name:
        return ["architecture_review", "refactoring", "bugfix"]
    if "repair" in name:
        return ["incident", "bugfix"]
    if "security" in name:
        return ["architecture_review", "refactoring"]
    if "release" in name:
        return ["incident", "architecture_review"]
    if "research" in name:
        return ["architecture_review"]
    return ["bugfix", "refactoring", "incident", "architecture_review"]


def _build_blueprint_work_profile(
    blueprint: TeamBlueprintDB,
    roles: list[BlueprintRoleDB],
    artifacts: list[BlueprintArtifactDB],
) -> dict[str, Any]:
    capability_defaults: set[str] = set()
    execution_modes: set[str] = set()
    preferred_backends: set[str] = set()
    fallback_backends: set[str] = set()
    risk_profiles: set[str] = set()

    for role in roles:
        role_config = dict(role.config or {})
        for capability in role_config.get("capability_defaults") or []:
            normalized = str(capability).strip()
            if normalized:
                capability_defaults.add(normalized)
        execution_mode = str(role_config.get("execution_mode") or "").strip()
        if execution_mode:
            execution_modes.add(execution_mode)
        preferred_backend = str(role_config.get("preferred_backend") or "").strip()
        if preferred_backend:
            preferred_backends.add(preferred_backend)
        for backend in role_config.get("fallback_backends") or []:
            normalized_backend = str(backend).strip()
            if normalized_backend:
                fallback_backends.add(normalized_backend)
        risk_profile = str(role_config.get("risk_profile") or "").strip().lower()
        if risk_profile:
            risk_profiles.add(risk_profile)

    policy_artifacts = [
        {
            "title": artifact.title,
            "sort_order": artifact.sort_order,
            "payload": dict(artifact.payload or {}),
        }
        for artifact in artifacts
        if str(artifact.kind or "").strip().lower() == "policy"
    ]
    starter_artifacts = [
        {
            "kind": artifact.kind,
            "title": artifact.title,
            "description": artifact.description,
            "sort_order": artifact.sort_order,
            "payload": dict(artifact.payload or {}),
        }
        for artifact in sorted(artifacts, key=lambda item: (item.sort_order, item.title))
    ]
    return {
        "blueprint_id": blueprint.id,
        "blueprint_name": blueprint.name,
        "base_team_type_name": blueprint.base_team_type_name,
        "goal_modes": _suggest_goal_modes_for_blueprint(blueprint),
        "playbooks": _suggest_playbooks_for_blueprint(blueprint),
        "recommended_action_pack_capabilities": sorted(capability_defaults),
        "execution_modes": sorted(execution_modes),
        "preferred_backends": sorted(preferred_backends),
        "fallback_backends": sorted(fallback_backends),
        "risk_profiles": sorted(risk_profiles),
        "policy_profiles": policy_artifacts,
        "starter_artifacts": starter_artifacts,
    }


STANDARD_BLUEPRINT_ORDER = [
    "Scrum",
    "Kanban",
    "Research",
    "Code-Repair",
    "TDD",
    "Security-Review",
    "Release-Prep",
    "Scrum-OpenCode",
    "Research-Evolution",
]

BLUEPRINT_PRODUCT_HINTS = {
    "scrum": {
        "intended_use": "Iterative delivery with explicit sprint roles and backlog ownership.",
        "when_to_use": "Use for feature-driven work with clear increments and sprint cadence.",
    },
    "kanban": {
        "intended_use": "Continuous flow delivery with WIP-aware operational prioritization.",
        "when_to_use": "Use for mixed incoming work where throughput and flow metrics matter.",
    },
    "research": {
        "intended_use": "Evidence-driven research and synthesis with source validation.",
        "when_to_use": "Use when outcome quality depends on source coverage and defensible synthesis.",
    },
    "code-repair": {
        "intended_use": "Incident triage, targeted patching, and regression containment.",
        "when_to_use": "Use for bugfix and repair work with minimal blast radius requirements.",
    },
    "tdd": {
        "intended_use": "Behavior-first implementation with explicit red/green/refactor evidence.",
        "when_to_use": "Use when test-first change control and verification traceability are required.",
    },
    "security-review": {
        "intended_use": "Security and compliance review with risk-focused controls validation.",
        "when_to_use": "Use before risky releases or for trust-boundary and policy-sensitive changes.",
    },
    "release-prep": {
        "intended_use": "Release-readiness orchestration with verification and rollback gates.",
        "when_to_use": "Use for pre-release coordination, go/no-go framing, and rollout safety checks.",
    },
    "scrum-opencode": {
        "intended_use": "Scrum delivery with explicit OpenCode/SGPT/terminal execution cascade.",
        "when_to_use": "Use for OpenCode-first teams needing deterministic tool/handoff expectations.",
    },
    "research-evolution": {
        "intended_use": "DeerFlow research followed by Evolver proposal with mandatory review gate.",
        "when_to_use": "Use for proposal-heavy evolution where research traceability is mandatory.",
    },
}


def _catalog_hint_for_blueprint(blueprint_name: str) -> dict[str, str]:
    normalized_name = str(blueprint_name or "").strip().lower()
    return BLUEPRINT_PRODUCT_HINTS.get(
        normalized_name,
        {
            "intended_use": "Reusable team definition with roles, start artifacts, and governance defaults.",
            "when_to_use": "Use when you want a repeatable startup path instead of assembling teams manually.",
        },
    )


def _catalog_expected_outputs(artifacts: list[BlueprintArtifactDB]) -> list[str]:
    starter_tasks = [
        str(artifact.title).strip()
        for artifact in sorted(artifacts, key=lambda item: (item.sort_order, item.title))
        if str(artifact.kind or "").strip().lower() == "task" and str(artifact.title or "").strip()
    ]
    if starter_tasks:
        return starter_tasks[:3]

    policy_titles = [
        str(artifact.title).strip()
        for artifact in sorted(artifacts, key=lambda item: (item.sort_order, item.title))
        if str(artifact.kind or "").strip().lower() == "policy" and str(artifact.title or "").strip()
    ]
    return policy_titles[:2] if policy_titles else ["Starter tasks and role-ready execution context"]


def _catalog_safety_review_stance(artifacts: list[BlueprintArtifactDB]) -> str:
    policy_payloads = [
        dict(artifact.payload or {})
        for artifact in sorted(artifacts, key=lambda item: (item.sort_order, item.title))
        if str(artifact.kind or "").strip().lower() == "policy"
    ]
    if not policy_payloads:
        return "balanced security, standard verification"

    policy = policy_payloads[0]
    security_level = str(policy.get("security_level") or "balanced").strip().lower()
    verification_required = bool(policy.get("verification_required", False))
    review_required = bool(policy.get("review_required", False))

    attributes = [f"{security_level} security"]
    if verification_required:
        attributes.append("verification required")
    if review_required:
        attributes.append("human review gate")
    return ", ".join(attributes)


def _governance_profile_from_work_profile(work_profile: dict[str, Any]) -> dict[str, str]:
    risk_profiles = {str(item).strip().lower() for item in (work_profile.get("risk_profiles") or []) if str(item).strip()}
    policy_profiles = work_profile.get("policy_profiles") or []
    strict_policy = any(str((item.get("payload") or {}).get("security_level") or "").strip().lower() == "strict" for item in policy_profiles)
    review_gate = any(bool((item.get("payload") or {}).get("review_required", False)) for item in policy_profiles)
    verification_required = any(bool((item.get("payload") or {}).get("verification_required", False)) for item in policy_profiles)

    if strict_policy or "strict" in risk_profiles:
        label = "Strict review profile"
        hint = "High-assurance flow with explicit review and controlled rollout expectations."
    elif verification_required or "high" in risk_profiles:
        label = "Balanced with verification"
        hint = "Execution remains delivery-oriented but expects explicit verification before closure."
    else:
        label = "Balanced default profile"
        hint = "Standard governance profile for regular delivery and iterative refinement."

    if review_gate and "review" not in hint.lower():
        hint = f"{hint} Includes a human review gate."
    return {"label": label, "hint": hint}


def _work_profile_summary(work_profile: dict[str, Any]) -> dict[str, Any]:
    governance = _governance_profile_from_work_profile(work_profile)
    return {
        "recommended_goal_modes": list(work_profile.get("goal_modes") or [])[:4],
        "playbook_hints": list(work_profile.get("playbooks") or [])[:4],
        "capability_hints": list(work_profile.get("recommended_action_pack_capabilities") or [])[:5],
        "governance_profile": governance,
    }


def _serialize_blueprint_catalog_item(
    blueprint: TeamBlueprintDB,
    roles: list[BlueprintRoleDB],
    artifacts: list[BlueprintArtifactDB],
) -> dict[str, Any]:
    hints = _catalog_hint_for_blueprint(blueprint.name)
    work_profile = _build_blueprint_work_profile(blueprint, roles, artifacts)
    short_description = str(blueprint.description or "").strip() or hints["intended_use"]
    return {
        "id": blueprint.id,
        "name": blueprint.name,
        "short_description": short_description,
        "intended_use": hints["intended_use"],
        "when_to_use": hints["when_to_use"],
        "expected_outputs": _catalog_expected_outputs(artifacts),
        "safety_review_stance": _catalog_safety_review_stance(artifacts),
        "work_profile_summary": _work_profile_summary(work_profile),
        "goal_modes": work_profile["goal_modes"],
        "playbooks": work_profile["playbooks"],
        "is_standard_blueprint": bool(blueprint.is_seed),
        "entry_recommended": bool(blueprint.is_seed),
    }


def _blueprint_catalog_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    name = str(item.get("name") or "")
    try:
        standard_rank = STANDARD_BLUEPRINT_ORDER.index(name)
    except ValueError:
        standard_rank = len(STANDARD_BLUEPRINT_ORDER) + 1
    is_standard = bool(item.get("is_standard_blueprint"))
    return (0 if is_standard else 1, standard_rank, name.lower())


def _user_lifecycle_state_from_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    drift_status = str(metadata.get("drift_status") or "").strip().lower()
    origin_kind = str(metadata.get("origin_kind") or "").strip().lower()
    customizations = dict(metadata.get("live_customizations") or {})
    role_templates_customized = bool(customizations.get("role_templates"))

    if drift_status == "in_sync":
        return {
            "code": "standard",
            "label": "Standard",
            "hint": "Dieses Team folgt dem aktuellen Blueprint-Stand.",
        }
    if drift_status == "drifted":
        return {
            "code": "outdated",
            "label": "Aktualisierbar",
            "hint": "Blueprint wurde aktualisiert; Team laeuft noch auf einem aelteren Stand.",
        }
    if origin_kind == "not_blueprint_based":
        return {
            "code": "customized",
            "label": "Individuell",
            "hint": "Dieses Team wurde direkt als laufende Instanz aufgebaut.",
        }
    if role_templates_customized:
        return {
            "code": "customized",
            "label": "Angepasst",
            "hint": "Das Team nutzt eigene Rollen-Template-Anpassungen.",
        }
    if origin_kind in {"seed_blueprint_instance", "custom_blueprint_instance"}:
        return {
            "code": "customized",
            "label": "Angepasst",
            "hint": "Blueprint-basierte Instanz mit eigener Laufzeitentwicklung.",
        }
    return {
        "code": "customized",
        "label": "Angepasst",
        "hint": "Laufende Team-Instanz mit individueller Auspraegung.",
    }


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
    seed_blueprints = _load_seed_blueprints()
    reconcile_reports = reconcile_seed_blueprints_service(
        seed_blueprints,
        normalize_team_type_name=normalize_team_type_name,
        with_role_profile_defaults=_with_role_profile_defaults,
        ensure_default_templates_callback=ensure_default_templates,
    )
    for report in reconcile_reports:
        log_audit(
            "team_blueprint_reconciled",
            {
                "blueprint_id": report["blueprint_id"],
                "name": report["name"],
                "changes": report["changes"],
                "source": "seed_sync",
            },
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


@teams_bp.route("/teams/blueprints/catalog", methods=["GET"])
@check_auth
def list_team_blueprint_catalog():
    ensure_seed_blueprints()
    blueprints = _repos().team_blueprint_repo.get_all()
    items = []
    for blueprint in blueprints:
        roles = _repos().blueprint_role_repo.get_by_blueprint(blueprint.id)
        artifacts = _repos().blueprint_artifact_repo.get_by_blueprint(blueprint.id)
        items.append(_serialize_blueprint_catalog_item(blueprint, roles, artifacts))
    items.sort(key=_blueprint_catalog_sort_key)
    return api_response(
        data={
            "public_model": {
                "template_term": "Role Template",
                "template_api_term": "template",
                "blueprint_term": "Blueprint",
                "team_term": "Team",
                "default_entry_path": "Start with a blueprint, then instantiate a team.",
                "advanced_concepts": ["snapshot", "drift", "reconcile"],
            },
            "items": items,
        }
    )


@teams_bp.route("/teams/blueprints/<blueprint_id>", methods=["GET"])
@check_auth
def get_team_blueprint(blueprint_id):
    ensure_seed_blueprints()
    blueprint = _repos().team_blueprint_repo.get_by_id(blueprint_id)
    if not blueprint:
        return _team_error("not_found", 404)
    return api_response(data=_serialize_blueprint(blueprint))


@teams_bp.route("/teams/blueprints/<blueprint_id>/work-profile", methods=["GET"])
@check_auth
def get_team_blueprint_work_profile(blueprint_id):
    ensure_seed_blueprints()
    blueprint = _repos().team_blueprint_repo.get_by_id(blueprint_id)
    if not blueprint:
        return _team_error("not_found", 404)
    roles = _repos().blueprint_role_repo.get_by_blueprint(blueprint.id)
    artifacts = _repos().blueprint_artifact_repo.get_by_blueprint(blueprint.id)
    return api_response(data=_build_blueprint_work_profile(blueprint, roles, artifacts))


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

    result = save_blueprint_service(
        blueprint_id=None,
        name=blueprint_name,
        description=data.description,
        base_team_type_name=normalized_type_name or None,
        roles=data.roles,
        artifacts=data.artifacts,
        is_seed=False,
    )
    log_audit(
        "team_blueprint_created",
        {"blueprint_id": result.blueprint.id, "name": result.blueprint.name, "changes": result.changes},
    )
    return api_response(data=_serialize_blueprint(result.blueprint, roles=result.roles, artifacts=result.artifacts), code=201)


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

    result = save_blueprint_service(
        blueprint_id=blueprint.id,
        name=blueprint.name,
        description=blueprint.description,
        base_team_type_name=blueprint.base_team_type_name,
        roles=data.roles,
        artifacts=data.artifacts,
        is_seed=blueprint.is_seed,
    )
    log_audit(
        "team_blueprint_updated",
        {"blueprint_id": result.blueprint.id, "name": result.blueprint.name, "changes": result.changes},
    )
    return api_response(data=_serialize_blueprint(result.blueprint, roles=result.roles, artifacts=result.artifacts))


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
    team_payload = instantiated.model_dump()
    definition_metadata = team_definition_metadata(instantiated)
    team_payload["definition_metadata"] = definition_metadata
    team_payload["user_lifecycle_state"] = _user_lifecycle_state_from_metadata(definition_metadata)
    return api_response(data={"team": team_payload, "blueprint": _serialize_blueprint(blueprint)}, code=201)


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
