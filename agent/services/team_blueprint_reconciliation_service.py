"""Seed blueprint and seed template reconciliation (file → DB).

SRP: read seed blueprint / seed template catalogs and idempotently sync
them into TeamBlueprintDB / TemplateDB rows. Owns no persistence
detail (delegates to team_blueprint_persistence_service) and no
template bootstrap (delegates to team_template_bootstrap_service via
the callback the caller passes in). Migrated from
team_blueprint_service.py (WFG-029 split) without behaviour change.

WFG-033 anchor: WFG-004 (workflow definition) and WFG-008 (default
gates) extend this module with a _reconcile_blueprint_workflow_steps
and _insert_default_gates sub-function. Do not move those out — the
reconciliation lifecycle is the right place for them.
"""
from __future__ import annotations

import time

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import (
    BlueprintWorkflowStepDB,
    TeamBlueprintDB,
    TemplateDB,
)
from agent.models import BlueprintArtifactDefinition, BlueprintRoleDefinition
from agent.services.team_blueprint_persistence_service import (
    persist_blueprint_children_in_session,
)


def reconcile_seed_blueprints(
    seed_blueprints: dict,
    *,
    normalize_team_type_name,
    with_role_profile_defaults,
    ensure_default_templates_callback,
) -> list[dict]:
    for attempt in range(2):
        try:
            return _reconcile_seed_blueprints_once(
                seed_blueprints,
                normalize_team_type_name=normalize_team_type_name,
                with_role_profile_defaults=with_role_profile_defaults,
                ensure_default_templates_callback=ensure_default_templates_callback,
            )
        except IntegrityError:
            if attempt >= 1:
                raise
            time.sleep(0.05)
    return []


def _reconcile_seed_blueprints_once(
    seed_blueprints: dict,
    *,
    normalize_team_type_name,
    with_role_profile_defaults,
    ensure_default_templates_callback,
) -> list[dict]:
    reconcile_reports: list[dict] = []
    for blueprint_name, blueprint_definition in seed_blueprints.items():
        base_team_type_name = normalize_team_type_name(str(blueprint_definition.get("base_team_type_name") or blueprint_name))
        ensure_default_templates_callback(base_team_type_name)
        with Session(engine) as session:
            templates_by_name = {template.name: template for template in session.exec(select(TemplateDB)).all()}
            blueprint = session.exec(select(TeamBlueprintDB).where(TeamBlueprintDB.name == blueprint_name)).first()
            blueprint_field_changes: list[str] = []
            if blueprint is None:
                blueprint = TeamBlueprintDB(
                    name=blueprint_name,
                    description=blueprint_definition["description"],
                    base_team_type_name=base_team_type_name or None,
                    is_seed=True,
                )
                session.add(blueprint)
                session.flush()
                blueprint_field_changes = ["name", "description", "base_team_type_name", "is_seed"]
            else:
                if (
                    blueprint.description != blueprint_definition["description"]
                    or blueprint.base_team_type_name != (base_team_type_name or None)
                    or blueprint.is_seed is not True
                ):
                    if blueprint.description != blueprint_definition["description"]:
                        blueprint_field_changes.append("description")
                    if blueprint.base_team_type_name != (base_team_type_name or None):
                        blueprint_field_changes.append("base_team_type_name")
                    if blueprint.is_seed is not True:
                        blueprint_field_changes.append("is_seed")
                    blueprint.description = blueprint_definition["description"]
                    blueprint.base_team_type_name = base_team_type_name or None
                    blueprint.is_seed = True
                    blueprint.updated_at = time.time()
                    session.add(blueprint)
                    session.flush()

            role_definitions = [
                BlueprintRoleDefinition(
                    name=role_definition["name"],
                    description=role_definition["description"],
                    template_id=(templates_by_name.get(role_definition["template_name"]).id if templates_by_name.get(role_definition["template_name"]) else None),
                    sort_order=role_definition["sort_order"],
                    is_required=role_definition["is_required"],
                    config=with_role_profile_defaults(base_team_type_name, role_definition["name"], role_definition["config"]),
                )
                for role_definition in blueprint_definition["roles"]
            ]
            artifact_definitions = [
                BlueprintArtifactDefinition(
                    kind=artifact_definition["kind"],
                    title=artifact_definition["title"],
                    description=artifact_definition["description"],
                    sort_order=artifact_definition["sort_order"],
                    payload=artifact_definition["payload"],
                )
                for artifact_definition in blueprint_definition["artifacts"]
            ]
            result = persist_blueprint_children_in_session(session, blueprint.id, role_definitions, artifact_definitions)
            # WFG-033: persist the optional workflow block.
            # The catalog normalizer (``SeedBlueprintCatalog.
            # _normalize_workflow``) is the source of truth for
            # step ids and DAG edges. The reconciler mirrors
            # them into ``blueprint_workflow_steps`` so the
            # planner and queue layers can query steps
            # without re-parsing the catalog JSON.
            workflow = blueprint_definition.get("workflow")
            workflow_changed = _reconcile_blueprint_workflow_steps(
                session, blueprint.id, workflow
            )
            if blueprint_field_changes or result.changed or workflow_changed:
                blueprint.updated_at = time.time()
                session.add(blueprint)
            session.commit()
            if blueprint_field_changes or result.changed or workflow_changed:
                reconcile_reports.append(
                    {
                        "blueprint_id": blueprint.id,
                        "name": blueprint.name,
                        "changes": {
                            "blueprint_fields": blueprint_field_changes,
                            "roles": result.changes["roles"],
                            "artifacts": result.changes["artifacts"],
                            "workflow_steps": workflow_changed,
                            "changed": True,
                        },
                    }
                )
    return reconcile_reports


def _reconcile_blueprint_workflow_steps(
    session, blueprint_id: str, workflow: dict | None
) -> int:
    """Idempotently sync a blueprint's workflow steps.

    Returns the number of changed rows (created + updated +
    deleted). The function is called from the seed-blueprint
    reconciliation path (WFG-033) and may be re-invoked on
    every deploy; the implementation MUST be safe to call
    repeatedly without producing duplicates.

    The function:

      1. Lists all ``BlueprintWorkflowStepDB`` rows for the
         blueprint.
      2. For each catalog step, upserts a row (matched by
         ``(blueprint_id, step_id)``).
      3. Deletes rows whose step_id is no longer in the
         catalog. This is the only path that removes steps
         after a blueprint is trimmed.
      4. Returns the total number of changes.

    When ``workflow`` is None or empty, all existing rows
    for the blueprint are deleted. This is the
    backward-compat escape hatch (WFG-021): a legacy
    blueprint that loses its workflow block reverts to the
    artifact-based subtask path.
    """
    existing = {
        row.step_id: row
        for row in session.exec(
            select(BlueprintWorkflowStepDB).where(
                BlueprintWorkflowStepDB.blueprint_id == blueprint_id
            )
        ).all()
    }
    catalog_step_ids: set[str] = set()
    changes = 0
    if isinstance(workflow, dict):
        for index, step in enumerate(list(workflow.get("steps") or [])):
            if not isinstance(step, dict):
                continue
            step_id = str(step.get("id") or "").strip()
            if not step_id:
                continue
            catalog_step_ids.add(step_id)
            row = existing.get(step_id)
            if row is None:
                row = BlueprintWorkflowStepDB(
                    blueprint_id=blueprint_id,
                    step_id=step_id,
                )
                changes += 1
            new_role_name = str(step.get("role") or "")
            new_task_kind = str(step.get("task_kind") or "coding")
            new_title = str(step.get("title") or "") or None
            new_description = str(step.get("description") or "") or None
            new_produces = list(step.get("produces") or [])
            new_consumes = list(step.get("consumes") or [])
            new_depends_on = list(step.get("depends_on") or [])
            new_gate = bool(step.get("gate", False))
            new_checks = dict(step.get("checks") or {}) if isinstance(step.get("checks"), dict) else {}
            new_failure_policy = str(step.get("failure_policy") or "") or None
            new_required_caps = list(step.get("required_capabilities") or [])
            if (
                row.role_name != new_role_name
                or row.task_kind != new_task_kind
                or row.title != new_title
                or row.description != new_description
                or row.produces != new_produces
                or row.consumes != new_consumes
                or row.depends_on != new_depends_on
                or row.gate != new_gate
                or row.checks != new_checks
                or row.failure_policy != new_failure_policy
                or row.required_capabilities != new_required_caps
            ):
                row.role_name = new_role_name
                row.task_kind = new_task_kind
                row.title = new_title
                row.description = new_description
                row.produces = new_produces
                row.consumes = new_consumes
                row.depends_on = new_depends_on
                row.gate = new_gate
                row.checks = new_checks
                row.failure_policy = new_failure_policy
                row.required_capabilities = new_required_caps
                row.sort_order = int(step.get("sort_order") or index)
                row.updated_at = time.time()
                session.add(row)
                changes += 1
    # Delete rows for steps that vanished from the catalog.
    for stale_step_id, stale_row in existing.items():
        if stale_step_id not in catalog_step_ids:
            session.delete(stale_row)
            changes += 1
    return changes


def reconcile_seed_templates(catalog) -> list[dict]:
    """Sync seed templates from the catalog file into the DB.

    - Creates templates that are in the catalog but not in the DB.
    - Updates seed templates (is_seed=True) when prompt_template or description changed.
    - Never modifies templates where is_seed=False (user-created).
    Returns a list of change reports.
    """
    for attempt in range(2):
        try:
            return _reconcile_seed_templates_once(catalog)
        except IntegrityError:
            if attempt >= 1:
                raise
            time.sleep(0.05)
    return []


def _reconcile_seed_templates_once(catalog) -> list[dict]:
    all_templates = catalog.get_all_templates()
    if not all_templates:
        return []

    reports: list[dict] = []
    with Session(engine) as session:
        existing: dict[str, TemplateDB] = {
            t.name: t for t in session.exec(select(TemplateDB)).all()
        }
        for tpl in all_templates:
            name = tpl["name"]
            description = tpl["description"]
            prompt_template = tpl["prompt_template"]
            existing_tpl = existing.get(name)

            if existing_tpl is None:
                new_tpl = TemplateDB(
                    name=name, description=description,
                    prompt_template=prompt_template, is_seed=True,
                )
                session.add(new_tpl)
                reports.append({"name": name, "action": "created"})
                continue

            # Preserve user-created templates (is_seed=False)
            if not getattr(existing_tpl, "is_seed", True):
                reports.append({"name": name, "action": "skipped_user_template"})
                continue

            changes: list[str] = []
            if existing_tpl.prompt_template != prompt_template:
                existing_tpl.prompt_template = prompt_template
                changes.append("prompt_template")
            if existing_tpl.description != description:
                existing_tpl.description = description
                changes.append("description")
            if not getattr(existing_tpl, "is_seed", True):
                existing_tpl.is_seed = True
                changes.append("is_seed")
            if changes:
                session.add(existing_tpl)
                reports.append({"name": name, "action": "updated", "fields": changes})
            else:
                reports.append({"name": name, "action": "unchanged"})

        session.commit()
    return reports
