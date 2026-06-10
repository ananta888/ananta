"""Seed/bootstrap helpers for team blueprints, templates and scrum artifacts.

Extracted from agent/routes/teams.py (SPLIT-012). Template prompt strings and
role-profile defaults are defined in config/blueprints/standard/templates.json
and loaded via SeedTemplateCatalog (see agent/services/seed_template_catalog.py).
"""

import time
import uuid

from agent.common.audit import log_audit
from agent.db_models import TaskDB
from agent.services.repository_registry import get_repository_registry
from agent.services.seed_blueprint_catalog import get_seed_blueprint_catalog
from agent.services.seed_template_catalog import get_seed_template_catalog
from agent.services.system_prompt_catalog import get_system_prompt_catalog
from agent.services.team_blueprint_reconciliation_service import (
    reconcile_seed_blueprints as reconcile_seed_blueprints_service,
)
from agent.services.team_blueprint_reconciliation_service import (
    reconcile_seed_templates as reconcile_seed_templates_service,
)
from agent.services.team_system_prompt_reconciliation_service import (
    reconcile_system_prompts as reconcile_system_prompts_service,
)
from agent.services.team_template_bootstrap_service import (
    RoleLinkSpec,
    TemplateBootstrapSpec,
)
from agent.services.team_template_bootstrap_service import (
    ensure_default_templates as ensure_default_templates_service,
)


def _repos():
    return get_repository_registry()


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
    defaults = get_seed_template_catalog().get_role_profile_defaults(base_team_type_name, role_name)
    for key, value in defaults.items():
        merged.setdefault(key, value)
    return merged


def initialize_scrum_artifacts(team_name: str, team_id: str | None = None):
    """Erstellt initiale Tasks für ein Scrum Team."""
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
    """Ensures default roles and templates for a team type exist.

    Template prompt text and role-profile defaults are loaded from
    config/blueprints/standard/templates.json via SeedTemplateCatalog.
    """
    team_type_name = normalize_team_type_name(team_type_name)
    if not team_type_name:
        return

    catalog = get_seed_template_catalog()
    raw_templates = catalog.get_templates_for_team_type(team_type_name)
    raw_roles = catalog.get_role_specs_for_team_type(team_type_name)

    if not raw_templates:
        return

    template_specs = [
        TemplateBootstrapSpec(t["name"], t["description"], t["prompt_template"])
        for t in raw_templates
    ]
    role_specs = [
        RoleLinkSpec(r["name"], r["description"], r["template_name"])
        for r in raw_roles
    ]
    ensure_default_templates_service(
        team_type_name,
        team_type_description=f"Standard {team_type_name} Team",
        template_specs=template_specs,
        role_specs=role_specs,
    )


def ensure_seed_blueprints() -> None:
    # 1. Reconcile system prompts (internal infra prompts, no blueprint deps)
    for report in reconcile_system_prompts_service(get_system_prompt_catalog()):
        if report.get("action") in {"created", "updated"}:
            log_audit("seed_system_prompt_reconciled",
                      {"name": report["name"], "action": report["action"],
                       "fields": report.get("fields"), "source": "seed_sync"})

    # 2. Reconcile role-prompt templates (must run before blueprint role-linking)
    tpl_reports = reconcile_seed_templates_service(get_seed_template_catalog())
    for report in tpl_reports:
        if report.get("action") in {"created", "updated"}:
            log_audit(
                "seed_template_reconciled",
                {"name": report["name"], "action": report["action"],
                 "fields": report.get("fields"), "source": "seed_sync"},
            )

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
