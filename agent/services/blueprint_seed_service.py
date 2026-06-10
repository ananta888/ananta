from __future__ import annotations

import time
import uuid
from typing import Any

from agent.db_models import TaskDB
from agent.services.repository_registry import get_repository_registry as _repos
from agent.services.seed_blueprint_catalog import get_seed_blueprint_catalog
from agent.services.seed_template_catalog import get_seed_template_catalog
from agent.services.team_utils import normalize_team_type_name


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

    from agent.services.team_template_bootstrap_service import (
        RoleLinkSpec,
        TemplateBootstrapSpec,
        ensure_default_templates as ensure_default_templates_service,
    )

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
